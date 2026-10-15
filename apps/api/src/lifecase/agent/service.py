"""Transactional Case-scoped orchestration and checkpoint persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from lifecase.agent.errors import (
    AgentBudgetError,
    AgentCancelledError,
    AgentCaseNotFoundError,
    AgentErrorCode,
    AgentIntegrityError,
    AgentPersistenceError,
    AgentProviderFailureError,
    AgentRunNotFoundError,
    AgentSequenceError,
    AgentStageSchemaError,
    AgentStageTimeoutError,
    AgentValidationError,
    AgentVersionConflictError,
)
from lifecase.agent.models import (
    AgentBudget,
    AgentCheckpoint,
    AgentRunRecord,
    AgentRunStatus,
    AgentStageName,
    AgentStageRecord,
    AgentStageStatus,
    BudgetUsage,
    CallReservation,
    CancelAgentRunCommand,
    CreateAgentRunCommand,
    ExecuteStageCommand,
    RecoverAgentRunCommand,
    StageBudget,
    StageInput,
    checkpoint_payload,
    stage_order_for_run,
)
from lifecase.agent.question_binding import validate_run_binding
from lifecase.agent.rag import question_provider_input
from lifecase.agent.registry import (
    BUILT_IN_STAGE_REGISTRY,
    StageDefinition,
    StageRegistry,
    validate_stage_output,
)
from lifecase.providers.errors import ModelProviderError
from lifecase.providers.models import (
    ProviderCallMetadata,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    request_scope_sha256,
)
from lifecase.providers.protocol import ModelProvider

_REPAIR_INSTRUCTION = (
    " The previous candidate failed local schema validation. "
    "Return one corrected object without commentary."
)


@dataclass(frozen=True, slots=True)
class _ReservedCall:
    run_id: str
    case_id: str
    stage_id: str
    definition: StageDefinition
    input_scope_sha256: str
    input_json: str
    run_version: int
    stage_version: int
    call_number: int
    repair_count: int
    reservation: CallReservation

    @property
    def request_scope_sha256(self) -> str:
        return stage_call_scope_sha256(
            self.run_id,
            self.definition.stage,
            self.input_scope_sha256,
            self.call_number,
        )


class _CallCancelled(Exception):
    pass


class _CallTimedOut(Exception):
    pass


class StageCheckpointHandler(Protocol):
    """Validate and atomically commit stage-specific business projections."""

    def normalize(
        self,
        *,
        connection: sqlite3.Connection,
        case_id: str,
        stage_input_json: str,
        checkpoint: AgentCheckpoint,
    ) -> AgentCheckpoint:
        """Replace untrusted derived metadata with deterministic authority values."""

    def prepare(
        self,
        *,
        connection: sqlite3.Connection,
        run_id: str,
        case_id: str,
        stage_input_json: str,
        checkpoint: AgentCheckpoint,
    ) -> object:
        """Validate one checkpoint before the bounded repair decision."""

    def commit(
        self,
        *,
        connection: sqlite3.Connection,
        run_id: str,
        case_id: str,
        checkpoint: AgentCheckpoint,
        prepared: object,
    ) -> None:
        """Commit business rows inside the Agent finalization transaction."""


class StageRunner:
    """Own the only durable orchestration transitions for one SQLite connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        provider: ModelProvider,
        *,
        registry: StageRegistry = BUILT_IN_STAGE_REGISTRY,
        checkpoint_handler: StageCheckpointHandler | None = None,
        clock: Callable[[], datetime] | None = None,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._connection = connection
        self._provider = provider
        self._registry = registry
        self._checkpoint_handler = checkpoint_handler
        self._clock = clock or (lambda: datetime.now(UTC))
        self._failure_injector = failure_injector
        self._active_cancellations: dict[str, asyncio.Event] = {}

    async def execute_stage(
        self,
        command: ExecuteStageCommand,
        *,
        cancellation_event: asyncio.Event | None = None,
    ) -> AgentRunRecord:
        """Execute one stage with durable reservation, one repair, and fail-closed stops."""

        provider_input_json = command.stage_input.payload_json
        definition = self._registry.get(command.stage_input.stage)
        if definition.stage_version == "validate.question.v1":
            # Preserve stored input scope and leave room for the one repair's
            # fixed prompt/schema within the existing 100 KB provider boundary.
            overhead = sum(
                len(part.encode("utf-8"))
                for part in (
                    definition.system_instruction,
                    definition.stage_instruction,
                    definition.output_schema_json,
                    _REPAIR_INSTRUCTION,
                )
            )
            provider_input_json = question_provider_input(
                self._connection, command.stage_input, maximum_bytes=100_000 - overhead
            )
        context = self._reserve_call(command)
        self._inject_failure("after_stage_reservation")
        local_cancellation = asyncio.Event()
        if context.run_id in self._active_cancellations:
            self._finalize_failure(
                context,
                code=AgentErrorCode.INTERRUPTED,
                retryable=True,
            )
            raise AgentVersionConflictError(context.run_version)
        self._active_cancellations[context.run_id] = local_cancellation
        try:
            while True:
                request = self._provider_request(context, provider_input_json)
                try:
                    result = await self._await_provider(
                        request,
                        context.definition.timeout_seconds,
                        local_cancellation,
                        cancellation_event,
                    )
                except _CallCancelled as error:
                    self._finalize_failure(
                        context,
                        code=AgentErrorCode.CANCELLED,
                        retryable=False,
                        cancelled=True,
                    )
                    raise AgentCancelledError(
                        "Agent stage was cancelled",
                        code=AgentErrorCode.CANCELLED,
                    ) from error
                except _CallTimedOut as error:
                    self._finalize_failure(
                        context,
                        code=AgentErrorCode.TIMEOUT,
                        retryable=True,
                    )
                    raise AgentStageTimeoutError(
                        "Agent stage timed out",
                        code=AgentErrorCode.TIMEOUT,
                        retryable=True,
                    ) from error
                except ModelProviderError as error:
                    self._finalize_failure(
                        context,
                        code=AgentErrorCode.PROVIDER_FAILURE,
                        retryable=error.retryable,
                        provider_error=error,
                    )
                    raise AgentProviderFailureError(
                        "Agent provider call failed",
                        code=AgentErrorCode.PROVIDER_FAILURE,
                        retryable=error.retryable,
                    ) from error

                if self._cancellation_requested(context.case_id, context.run_id):
                    self._finalize_failure(
                        context,
                        code=AgentErrorCode.CANCELLED,
                        retryable=False,
                        result=result,
                        cancelled=True,
                    )
                    raise AgentCancelledError(
                        "Agent stage was cancelled",
                        code=AgentErrorCode.CANCELLED,
                    )
                try:
                    checkpoint = validate_stage_output(
                        context.definition,
                        result.output_json,
                        input_scope_sha256=context.input_scope_sha256,
                    )
                    if self._checkpoint_handler is not None:
                        checkpoint = self._checkpoint_handler.normalize(
                            connection=self._connection,
                            case_id=context.case_id,
                            stage_input_json=context.input_json,
                            checkpoint=checkpoint,
                        )
                    prepared = (
                        self._checkpoint_handler.prepare(
                            connection=self._connection,
                            run_id=context.run_id,
                            case_id=context.case_id,
                            stage_input_json=context.input_json,
                            checkpoint=checkpoint,
                        )
                        if self._checkpoint_handler is not None
                        else None
                    )
                except AgentValidationError as error:
                    if self._can_repair(context):
                        context = self._reserve_repair(context, result)
                        continue
                    self._finalize_failure(
                        context,
                        code=AgentErrorCode.SCHEMA_INVALID,
                        retryable=False,
                        result=result,
                    )
                    raise AgentStageSchemaError(
                        "Agent stage output remained invalid after repair",
                        code=AgentErrorCode.SCHEMA_INVALID,
                    ) from error
                return self._finalize_checkpoint(context, result, checkpoint, prepared)
        except asyncio.CancelledError as error:
            self._finalize_failure(
                context,
                code=AgentErrorCode.CANCELLED,
                retryable=False,
                cancelled=True,
            )
            raise AgentCancelledError(
                "Agent stage was cancelled",
                code=AgentErrorCode.CANCELLED,
            ) from error
        finally:
            self._active_cancellations.pop(context.run_id, None)

    def create_run(self, command: CreateAgentRunCommand) -> AgentRunRecord:
        """Idempotently create one durable pipeline version owned by a Case."""

        timestamp = self._utc_now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if not self._case_exists(command.case_id):
                raise AgentCaseNotFoundError(
                    "Agent Case was not found",
                    code=AgentErrorCode.CASE_NOT_FOUND,
                )
            if command.allow_case_history:
                existing = self._connection.execute(
                    "SELECT * FROM agent_run WHERE id = ? AND case_id = ?",
                    (command.run_id, command.case_id),
                ).fetchone()
            else:
                existing = self._connection.execute(
                    "SELECT * FROM agent_run WHERE case_id = ?",
                    (command.case_id,),
                ).fetchone()
            if existing is not None:
                record = self._run_from_row(existing, ())
                if (
                    record.id != command.run_id
                    or record.budget != command.budget
                    or record.input_binding_json != command.input_binding_json
                    or record.input_binding_sha256 != command.input_binding_sha256
                ):
                    raise AgentSequenceError(
                        "Case already has an Agent run",
                        code=AgentErrorCode.INVALID_SEQUENCE,
                    )
                self._connection.execute("COMMIT")
                return self.get_run(command.case_id, command.run_id)
            if command.allow_case_history:
                unfinished = self._connection.execute(
                    """
                    SELECT 1 FROM agent_run WHERE case_id = ?
                    AND status IN ('queued', 'running', 'checkpointed') LIMIT 1
                    """,
                    (command.case_id,),
                ).fetchone()
                if unfinished is not None:
                    raise AgentSequenceError(
                        "Case has an unfinished Agent run",
                        code=AgentErrorCode.INVALID_SEQUENCE,
                    )
            if command.input_binding_json is not None:
                from lifecase.agent.question_context import require_current_binding

                require_current_binding(
                    self._connection, command.input_binding_json, command.case_id
                )
            budget = command.budget
            self._connection.execute(
                """
                INSERT INTO agent_run (
                    id, case_id, status, version, max_calls,
                    max_input_tokens, max_output_tokens, max_total_tokens,
                    max_cost_minor, cost_currency, created_at, updated_at,
                    input_binding_json, input_binding_sha256
                ) VALUES (?, ?, 'queued', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command.run_id,
                    command.case_id,
                    budget.max_calls,
                    budget.max_input_tokens,
                    budget.max_output_tokens,
                    budget.max_total_tokens,
                    budget.max_cost_minor,
                    budget.cost_currency,
                    timestamp,
                    timestamp,
                    command.input_binding_json,
                    command.input_binding_sha256,
                ),
            )
            self._connection.execute("COMMIT")
        except (AgentCaseNotFoundError, AgentSequenceError, AgentIntegrityError):
            self._rollback()
            raise
        except sqlite3.Error as error:
            self._rollback()
            raise AgentPersistenceError(
                "Agent run transaction was rolled back",
                code=AgentErrorCode.PERSISTENCE,
            ) from error
        return self.get_run(command.case_id, command.run_id)

    def _reserve_call(self, command: ExecuteStageCommand) -> _ReservedCall:
        # A caller-selected registry must not change the persisted run's contract.
        if tuple(item.stage for item in self._registry.definitions) != stage_order_for_run(
            command.run_id
        ):
            raise AgentSequenceError(
                "Agent registry does not match the run",
                code=AgentErrorCode.INVALID_SEQUENCE,
            )
        stage_input = command.stage_input
        definition = self._registry.get(stage_input.stage)
        if stage_input.case_id == "" or (
            stage_input.stage_version != definition.stage_version
            or stage_input.input_schema_version != definition.input_schema_version
        ):
            raise AgentValidationError("Agent stage input version is invalid")
        timestamp = self._utc_now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            run = self._required_run_row(stage_input.case_id, command.run_id)
            self._require_current_bound_inputs(run)
            run_version = int(run["version"])
            if run_version != command.expected_run_version:
                raise AgentVersionConflictError(run_version)
            status = AgentRunStatus(str(run["status"]))
            if status in {
                AgentRunStatus.RUNNING,
                AgentRunStatus.CANCELLED,
                AgentRunStatus.COMPLETED,
            }:
                raise AgentSequenceError(
                    "Agent run cannot start the requested stage",
                    code=AgentErrorCode.INVALID_SEQUENCE,
                )
            if bool(run["cancellation_requested"]):
                raise AgentSequenceError(
                    "Agent run is cancelled",
                    code=AgentErrorCode.INVALID_SEQUENCE,
                )
            self._validate_next_stage(run, stage_input.stage)
            self._validate_checkpoint_chain(command.run_id, stage_input)
            stage = self._connection.execute(
                """
                SELECT * FROM agent_stage_run
                WHERE run_id = ? AND case_id = ? AND stage = ?
                """,
                (command.run_id, stage_input.case_id, stage_input.stage.value),
            ).fetchone()
            if stage is None:
                if command.expected_stage_version is not None:
                    raise AgentVersionConflictError(0)
                stage_version = 1
                repair_count = 0
                stage_id = _stage_id(command.run_id, stage_input.stage)
            else:
                stage_version = int(stage["version"])
                if command.expected_stage_version != stage_version:
                    raise AgentVersionConflictError(stage_version)
                if AgentStageStatus(str(stage["status"])) is not AgentStageStatus.NEEDS_REVIEW:
                    raise AgentSequenceError(
                        "Agent stage cannot be resumed",
                        code=AgentErrorCode.INVALID_SEQUENCE,
                    )
                self._validate_existing_stage(stage, command)
                repair_count = int(stage["repair_count"])
                stage_id = str(stage["id"])

            if not self._has_capacity(run, stage, command):
                self._record_budget_stop(run, stage, command, definition, stage_id, timestamp)
                self._connection.execute("COMMIT")
                raise AgentBudgetError(
                    "Agent budget cannot reserve another call",
                    code=AgentErrorCode.BUDGET_EXHAUSTED,
                )

            reservation = command.reservation
            self._connection.execute(
                """
                UPDATE agent_run
                SET status = 'running', version = version + 1, current_stage = ?,
                    used_calls = used_calls + 1,
                    reserved_input_tokens = reserved_input_tokens + ?,
                    reserved_output_tokens = reserved_output_tokens + ?,
                    reserved_total_tokens = reserved_total_tokens + ?,
                    reserved_cost_minor = reserved_cost_minor + ?, updated_at = ?,
                    completed_at = NULL
                WHERE id = ? AND case_id = ? AND version = ?
                """,
                (
                    stage_input.stage.value,
                    reservation.input_tokens,
                    reservation.output_tokens,
                    reservation.total_tokens,
                    reservation.cost_minor,
                    timestamp,
                    command.run_id,
                    stage_input.case_id,
                    run_version,
                ),
            )
            if stage is None:
                budget = command.stage_budget
                self._connection.execute(
                    """
                    INSERT INTO agent_stage_run (
                        id, run_id, case_id, stage, stage_version,
                        input_schema_version, output_schema_version,
                        checkpoint_schema_version, input_scope_sha256,
                        status, version, max_calls, max_input_tokens,
                        max_output_tokens, max_total_tokens, max_cost_minor,
                        cost_currency, used_calls, reserved_input_tokens,
                        reserved_output_tokens, reserved_total_tokens,
                        reserved_cost_minor, started_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', 1,
                              ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stage_id,
                        command.run_id,
                        stage_input.case_id,
                        stage_input.stage.value,
                        definition.stage_version,
                        definition.input_schema_version,
                        definition.output_schema_version,
                        definition.checkpoint_schema_version,
                        stage_input.input_scope_sha256,
                        budget.max_calls,
                        budget.max_input_tokens,
                        budget.max_output_tokens,
                        budget.max_total_tokens,
                        budget.max_cost_minor,
                        budget.cost_currency,
                        reservation.input_tokens,
                        reservation.output_tokens,
                        reservation.total_tokens,
                        reservation.cost_minor,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                self._connection.execute(
                    """
                    UPDATE agent_stage_run
                    SET status = 'running', version = version + 1,
                        used_calls = used_calls + 1,
                        reserved_input_tokens = reserved_input_tokens + ?,
                        reserved_output_tokens = reserved_output_tokens + ?,
                        reserved_total_tokens = reserved_total_tokens + ?,
                        reserved_cost_minor = reserved_cost_minor + ?,
                        error_code = NULL, retryable = NULL,
                        updated_at = ?, completed_at = NULL
                    WHERE id = ? AND version = ? AND status = 'needs_review'
                    """,
                    (
                        reservation.input_tokens,
                        reservation.output_tokens,
                        reservation.total_tokens,
                        reservation.cost_minor,
                        timestamp,
                        stage_id,
                        stage_version,
                    ),
                )
                stage_version += 1
            self._connection.execute("COMMIT")
        except AgentBudgetError:
            self._rollback()
            raise
        except (
            AgentCaseNotFoundError,
            AgentIntegrityError,
            AgentRunNotFoundError,
            AgentSequenceError,
            AgentVersionConflictError,
        ):
            self._rollback()
            raise
        except sqlite3.Error as error:
            self._rollback()
            raise AgentPersistenceError(
                "Agent reservation transaction was rolled back",
                code=AgentErrorCode.PERSISTENCE,
            ) from error
        return _ReservedCall(
            run_id=command.run_id,
            case_id=stage_input.case_id,
            stage_id=stage_id,
            definition=definition,
            input_scope_sha256=stage_input.input_scope_sha256,
            input_json=stage_input.payload_json,
            run_version=run_version + 1,
            stage_version=stage_version,
            call_number=(int(stage["used_calls"]) if stage is not None else 0) + 1,
            repair_count=repair_count,
            reservation=command.reservation,
        )

    def _validate_next_stage(self, run: sqlite3.Row, requested: AgentStageName) -> None:
        order = stage_order_for_run(str(run["id"]))
        last = _optional_stage(run["last_completed_stage"])
        if last is not None and last not in order:
            raise AgentIntegrityError(
                "Agent checkpoint stage is inconsistent", code=AgentErrorCode.INTEGRITY
            )
        expected_index = 0 if last is None else order.index(last) + 1
        if expected_index >= len(order) or order[expected_index] is not requested:
            raise AgentSequenceError(
                "Agent stage order is invalid",
                code=AgentErrorCode.INVALID_SEQUENCE,
            )

    def _validate_existing_stage(
        self,
        row: sqlite3.Row,
        command: ExecuteStageCommand,
    ) -> None:
        stage_input = command.stage_input
        budget = command.stage_budget
        expected = (
            stage_input.stage_version,
            stage_input.input_schema_version,
            stage_input.input_scope_sha256,
            budget.max_calls,
            budget.max_input_tokens,
            budget.max_output_tokens,
            budget.max_total_tokens,
            budget.max_cost_minor,
            budget.cost_currency,
        )
        actual = (
            str(row["stage_version"]),
            str(row["input_schema_version"]),
            str(row["input_scope_sha256"]),
            int(row["max_calls"]),
            int(row["max_input_tokens"]),
            int(row["max_output_tokens"]),
            int(row["max_total_tokens"]),
            int(row["max_cost_minor"]),
            str(row["cost_currency"]),
        )
        if actual != expected:
            raise AgentSequenceError(
                "Agent stage resume scope is invalid",
                code=AgentErrorCode.INVALID_SEQUENCE,
            )

    def _validate_checkpoint_chain(self, run_id: str, stage_input: StageInput) -> None:
        payload = json.loads(stage_input.payload_json)
        supplied = tuple(str(value) for value in payload["prior_checkpoint_hashes"])
        order = stage_order_for_run(run_id)
        stage_index = order.index(stage_input.stage)
        expected_rows = self._connection.execute(
            """
            SELECT stage, checkpoint_sha256 FROM agent_stage_run
            WHERE run_id = ? AND case_id = ? AND status = 'checkpointed'
            ORDER BY CASE stage
                WHEN 'extract' THEN 0 WHEN 'normalize' THEN 1
                WHEN 'link_claims' THEN 2 WHEN 'classify_case' THEN 3
                WHEN 'check_gaps' THEN 4 WHEN 'propose_actions' THEN 5
                WHEN 'validate' THEN 6 END
            """,
            (run_id, stage_input.case_id),
        ).fetchall()
        expected_stages = order[:stage_index]
        if tuple(AgentStageName(str(row["stage"])) for row in expected_rows) != (expected_stages):
            raise AgentIntegrityError(
                "Agent checkpoint chain is inconsistent",
                code=AgentErrorCode.INTEGRITY,
            )
        expected = tuple(str(row["checkpoint_sha256"]) for row in expected_rows)
        if supplied != expected:
            raise AgentSequenceError(
                "Agent checkpoint chain does not match the stage input",
                code=AgentErrorCode.INVALID_SEQUENCE,
            )

    def _has_capacity(
        self,
        run: sqlite3.Row,
        stage: sqlite3.Row | None,
        command: ExecuteStageCommand,
    ) -> bool:
        reservation = command.reservation
        if not _row_has_capacity(run, reservation):
            return False
        if stage is None:
            budget = command.stage_budget
            return (
                reservation.input_tokens <= budget.max_input_tokens
                and reservation.output_tokens <= budget.max_output_tokens
                and reservation.total_tokens <= budget.max_total_tokens
                and reservation.cost_minor <= budget.max_cost_minor
            )
        return _row_has_capacity(stage, reservation)

    def _record_budget_stop(
        self,
        run: sqlite3.Row,
        stage: sqlite3.Row | None,
        command: ExecuteStageCommand,
        definition: StageDefinition,
        stage_id: str,
        timestamp: str,
    ) -> None:
        self._connection.execute(
            """
            UPDATE agent_run
            SET status = 'needs_review', version = version + 1,
                current_stage = ?, updated_at = ?
            WHERE id = ? AND case_id = ? AND version = ?
            """,
            (
                command.stage_input.stage.value,
                timestamp,
                command.run_id,
                command.stage_input.case_id,
                int(run["version"]),
            ),
        )
        if stage is not None:
            self._connection.execute(
                """
                UPDATE agent_stage_run
                SET status = 'needs_review', version = version + 1,
                    error_code = ?, retryable = 0, updated_at = ?, completed_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    AgentErrorCode.BUDGET_EXHAUSTED.value,
                    timestamp,
                    timestamp,
                    stage_id,
                    int(stage["version"]),
                ),
            )
            return
        budget = command.stage_budget
        self._connection.execute(
            """
            INSERT INTO agent_stage_run (
                id, run_id, case_id, stage, stage_version,
                input_schema_version, output_schema_version,
                checkpoint_schema_version, input_scope_sha256,
                status, version, max_calls, max_input_tokens, max_output_tokens,
                max_total_tokens, max_cost_minor, cost_currency, error_code,
                retryable, started_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'needs_review', 1,
                      ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                stage_id,
                command.run_id,
                command.stage_input.case_id,
                command.stage_input.stage.value,
                definition.stage_version,
                definition.input_schema_version,
                definition.output_schema_version,
                definition.checkpoint_schema_version,
                command.stage_input.input_scope_sha256,
                budget.max_calls,
                budget.max_input_tokens,
                budget.max_output_tokens,
                budget.max_total_tokens,
                budget.max_cost_minor,
                budget.cost_currency,
                AgentErrorCode.BUDGET_EXHAUSTED.value,
                timestamp,
                timestamp,
                timestamp,
            ),
        )

    def _provider_request(
        self, context: _ReservedCall, provider_input_json: str
    ) -> StructuredGenerationRequest:
        repair_instruction = _REPAIR_INSTRUCTION if context.repair_count else ""
        return StructuredGenerationRequest(
            request_id=f"agent-{context.request_scope_sha256[:32]}",
            request_scope_sha256=context.request_scope_sha256,
            system_instruction=context.definition.system_instruction,
            stage_instruction=context.definition.stage_instruction + repair_instruction,
            input_json=provider_input_json,
            output_schema_name=context.definition.output_schema_name,
            output_schema_json=context.definition.output_schema_json,
            max_output_tokens=context.reservation.output_tokens,
        )

    async def _await_provider(
        self,
        request: StructuredGenerationRequest,
        timeout_seconds: float,
        local_cancellation: asyncio.Event,
        external_cancellation: asyncio.Event | None,
    ) -> StructuredGenerationResult:
        provider_task = asyncio.create_task(self._provider.generate_structured(request))
        cancellation_tasks = [asyncio.create_task(local_cancellation.wait())]
        if external_cancellation is not None:
            cancellation_tasks.append(asyncio.create_task(external_cancellation.wait()))
        try:
            done, _ = await asyncio.wait(
                {provider_task, *cancellation_tasks},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if provider_task in done:
                return await provider_task
            provider_task.cancel()
            with suppress(asyncio.CancelledError):
                await provider_task
            if any(task in done and task.result() for task in cancellation_tasks):
                raise _CallCancelled
            raise _CallTimedOut
        finally:
            for task in cancellation_tasks:
                task.cancel()

    def _can_repair(self, context: _ReservedCall) -> bool:
        return (
            context.repair_count < context.definition.max_repair_attempts
            and context.call_number < 2
        )

    def _reserve_repair(
        self,
        context: _ReservedCall,
        result: StructuredGenerationResult,
    ) -> _ReservedCall:
        charged_input, charged_output, charged_cost, metadata_valid = self._usage_charge(
            context, result
        )
        if not metadata_valid:
            self._finalize_failure(
                context,
                code=AgentErrorCode.PROVIDER_FAILURE,
                retryable=False,
                result=result,
            )
            raise AgentProviderFailureError(
                "Agent provider accounting is invalid",
                code=AgentErrorCode.PROVIDER_FAILURE,
            )
        timestamp = self._utc_now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            run = self._required_run_row(context.case_id, context.run_id)
            stage = self._connection.execute(
                "SELECT * FROM agent_stage_run WHERE id = ? AND case_id = ?",
                (context.stage_id, context.case_id),
            ).fetchone()
            if stage is None or not self._reserved_state_matches(stage, context):
                raise AgentIntegrityError(
                    "Agent stage reservation is inconsistent",
                    code=AgentErrorCode.INTEGRITY,
                )
            if not self._reserved_state_matches(run, context, check_version=False):
                raise AgentIntegrityError(
                    "Agent run reservation is inconsistent",
                    code=AgentErrorCode.INTEGRITY,
                )
            # A changed question basis cannot be repaired by another model call.
            # Account for the completed call and release its reservation before
            # stopping; do not reserve another call against the stale input.
            try:
                self._require_current_bound_inputs(run)
            except AgentIntegrityError:
                self._rollback()
                self._finalize_failure(
                    context,
                    code=AgentErrorCode.INTEGRITY,
                    retryable=False,
                    result=result,
                )
                raise
            charge = (charged_input, charged_output, charged_cost)
            if not _row_can_account_and_reserve(stage, context.reservation, charge) or not (
                _row_can_account_and_reserve(run, context.reservation, charge)
            ):
                self._connection.execute("ROLLBACK")
                self._finalize_failure(
                    context,
                    code=AgentErrorCode.BUDGET_EXHAUSTED,
                    retryable=False,
                    result=result,
                )
                raise AgentBudgetError(
                    "Agent budget cannot reserve schema repair",
                    code=AgentErrorCode.BUDGET_EXHAUSTED,
                )
            metadata = result.metadata
            self._connection.execute(
                """
                UPDATE agent_stage_run
                SET version = version + 1, used_calls = used_calls + 1,
                    used_input_tokens = used_input_tokens + ?,
                    used_output_tokens = used_output_tokens + ?,
                    used_total_tokens = used_total_tokens + ?,
                    used_cost_minor = used_cost_minor + ?,
                    repair_count = repair_count + 1, provider = ?, model_id = ?,
                    latency_ms = latency_ms + ?, error_code = ?, retryable = 1,
                    updated_at = ?
                WHERE id = ? AND version = ? AND status = 'running'
                """,
                (
                    charged_input,
                    charged_output,
                    charged_input + charged_output,
                    charged_cost,
                    metadata.provider.value,
                    metadata.model_id,
                    metadata.latency_ms,
                    AgentErrorCode.SCHEMA_INVALID.value,
                    timestamp,
                    context.stage_id,
                    context.stage_version,
                ),
            )
            self._connection.execute(
                """
                UPDATE agent_run
                SET version = version + 1, used_calls = used_calls + 1,
                    used_input_tokens = used_input_tokens + ?,
                    used_output_tokens = used_output_tokens + ?,
                    used_total_tokens = used_total_tokens + ?,
                    used_cost_minor = used_cost_minor + ?, updated_at = ?
                WHERE id = ? AND case_id = ? AND status = 'running'
                """,
                (
                    charged_input,
                    charged_output,
                    charged_input + charged_output,
                    charged_cost,
                    timestamp,
                    context.run_id,
                    context.case_id,
                ),
            )
            self._connection.execute("COMMIT")
        except (AgentBudgetError, AgentIntegrityError):
            self._rollback()
            raise
        except sqlite3.Error as error:
            self._rollback()
            raise AgentPersistenceError(
                "Agent repair reservation was rolled back",
                code=AgentErrorCode.PERSISTENCE,
            ) from error
        return _ReservedCall(
            run_id=context.run_id,
            case_id=context.case_id,
            stage_id=context.stage_id,
            definition=context.definition,
            input_scope_sha256=context.input_scope_sha256,
            input_json=context.input_json,
            run_version=int(run["version"]) + 1,
            stage_version=context.stage_version + 1,
            call_number=context.call_number + 1,
            repair_count=context.repair_count + 1,
            reservation=context.reservation,
        )

    def _finalize_checkpoint(
        self,
        context: _ReservedCall,
        result: StructuredGenerationResult,
        checkpoint: AgentCheckpoint,
        prepared: object,
    ) -> AgentRunRecord:
        charged_input, charged_output, charged_cost, metadata_valid = self._usage_charge(
            context, result
        )
        if not metadata_valid:
            self._finalize_failure(
                context,
                code=AgentErrorCode.PROVIDER_FAILURE,
                retryable=False,
                result=result,
            )
            raise AgentProviderFailureError(
                "Agent provider accounting is invalid",
                code=AgentErrorCode.PROVIDER_FAILURE,
            )
        timestamp = self._utc_now()
        final_stage = context.definition.stage is AgentStageName.VALIDATE
        run_status = AgentRunStatus.COMPLETED if final_stage else AgentRunStatus.CHECKPOINTED
        metadata = result.metadata
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            stage = self._connection.execute(
                "SELECT * FROM agent_stage_run WHERE id = ? AND case_id = ?",
                (context.stage_id, context.case_id),
            ).fetchone()
            run = self._required_run_row(context.case_id, context.run_id)
            if stage is None or not self._reserved_state_matches(stage, context):
                raise AgentIntegrityError(
                    "Agent stage reservation is inconsistent",
                    code=AgentErrorCode.INTEGRITY,
                )
            if not self._reserved_state_matches(run, context, check_version=False):
                raise AgentIntegrityError(
                    "Agent run reservation is inconsistent",
                    code=AgentErrorCode.INTEGRITY,
                )
            if bool(run["cancellation_requested"]):
                self._connection.execute("ROLLBACK")
                self._finalize_failure(
                    context,
                    code=AgentErrorCode.CANCELLED,
                    retryable=False,
                    result=result,
                    cancelled=True,
                )
                raise AgentCancelledError(
                    "Agent stage was cancelled",
                    code=AgentErrorCode.CANCELLED,
                )
            self._inject_failure("before_stage_finalize")
            try:
                self._require_current_bound_inputs(run)
            except AgentIntegrityError:
                self._rollback()
                self._finalize_failure(
                    context,
                    code=AgentErrorCode.INTEGRITY,
                    retryable=False,
                    result=result,
                )
                raise
            if self._checkpoint_handler is not None:
                self._checkpoint_handler.commit(
                    connection=self._connection,
                    run_id=context.run_id,
                    case_id=context.case_id,
                    checkpoint=checkpoint,
                    prepared=prepared,
                )
            stage_updated = self._connection.execute(
                """
                UPDATE agent_stage_run
                SET status = 'checkpointed', version = version + 1,
                    used_input_tokens = used_input_tokens + ?,
                    used_output_tokens = used_output_tokens + ?,
                    used_total_tokens = used_total_tokens + ?,
                    used_cost_minor = used_cost_minor + ?,
                    reserved_input_tokens = 0, reserved_output_tokens = 0,
                    reserved_total_tokens = 0, reserved_cost_minor = 0,
                    provider = ?, model_id = ?, latency_ms = latency_ms + ?,
                    checkpoint_json = ?, checkpoint_sha256 = ?,
                    error_code = NULL, retryable = NULL,
                    updated_at = ?, completed_at = ?
                WHERE id = ? AND version = ? AND status = 'running'
                """,
                (
                    charged_input,
                    charged_output,
                    charged_input + charged_output,
                    charged_cost,
                    metadata.provider.value,
                    metadata.model_id,
                    metadata.latency_ms,
                    checkpoint.payload_json,
                    checkpoint.payload_sha256,
                    timestamp,
                    timestamp,
                    context.stage_id,
                    context.stage_version,
                ),
            )
            run_updated = self._connection.execute(
                """
                UPDATE agent_run
                SET status = ?, version = version + 1, last_completed_stage = ?,
                    used_input_tokens = used_input_tokens + ?,
                    used_output_tokens = used_output_tokens + ?,
                    used_total_tokens = used_total_tokens + ?,
                    used_cost_minor = used_cost_minor + ?,
                    reserved_input_tokens = 0, reserved_output_tokens = 0,
                    reserved_total_tokens = 0, reserved_cost_minor = 0,
                    updated_at = ?, completed_at = ?
                WHERE id = ? AND case_id = ? AND status = 'running'
                """,
                (
                    run_status.value,
                    context.definition.stage.value,
                    charged_input,
                    charged_output,
                    charged_input + charged_output,
                    charged_cost,
                    timestamp,
                    timestamp if final_stage else None,
                    context.run_id,
                    context.case_id,
                ),
            )
            if stage_updated.rowcount != 1 or run_updated.rowcount != 1:
                raise AgentIntegrityError(
                    "Agent checkpoint changed concurrently",
                    code=AgentErrorCode.INTEGRITY,
                )
            self._connection.execute("COMMIT")
        except (AgentCancelledError, AgentIntegrityError):
            self._rollback()
            raise
        except AgentValidationError as error:
            self._rollback()
            raise AgentIntegrityError(
                "Agent checkpoint authority changed before commit",
                code=AgentErrorCode.INTEGRITY,
            ) from error
        except (sqlite3.Error, RuntimeError) as error:
            self._rollback()
            raise AgentPersistenceError(
                "Agent checkpoint transaction was rolled back",
                code=AgentErrorCode.PERSISTENCE,
            ) from error
        return self.get_run(context.case_id, context.run_id)

    def _finalize_failure(
        self,
        context: _ReservedCall,
        *,
        code: AgentErrorCode,
        retryable: bool,
        result: StructuredGenerationResult | None = None,
        provider_error: ModelProviderError | None = None,
        cancelled: bool = False,
    ) -> None:
        charged_input = context.reservation.input_tokens
        charged_output = context.reservation.output_tokens
        charged_cost = context.reservation.cost_minor
        metadata: ProviderCallMetadata | None = None
        if result is not None:
            charged_input, charged_output, charged_cost, _ = self._usage_charge(context, result)
            metadata = result.metadata
        provider = metadata.provider.value if metadata is not None else None
        model_id = metadata.model_id if metadata is not None else None
        latency_ms = metadata.latency_ms if metadata is not None else 0
        if provider_error is not None and provider_error.metadata is not None:
            provider = str(provider_error.metadata.provider)
            model_id = provider_error.metadata.model_id
            latency_ms = provider_error.metadata.latency_ms
        timestamp = self._utc_now()
        stage_status = AgentStageStatus.CANCELLED if cancelled else AgentStageStatus.NEEDS_REVIEW
        run_status = AgentRunStatus.CANCELLED if cancelled else AgentRunStatus.NEEDS_REVIEW
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._inject_failure("before_stage_finalize")
            stage_updated = self._connection.execute(
                """
                UPDATE agent_stage_run
                SET status = ?, version = version + 1,
                    used_input_tokens = used_input_tokens + ?,
                    used_output_tokens = used_output_tokens + ?,
                    used_total_tokens = used_total_tokens + ?,
                    used_cost_minor = used_cost_minor + ?,
                    reserved_input_tokens = 0, reserved_output_tokens = 0,
                    reserved_total_tokens = 0, reserved_cost_minor = 0,
                    provider = ?, model_id = ?, latency_ms = latency_ms + ?,
                    error_code = ?, retryable = ?, updated_at = ?, completed_at = ?
                WHERE id = ? AND version = ? AND status = 'running'
                """,
                (
                    stage_status.value,
                    charged_input,
                    charged_output,
                    charged_input + charged_output,
                    charged_cost,
                    provider,
                    model_id,
                    latency_ms,
                    code.value,
                    int(retryable),
                    timestamp,
                    timestamp,
                    context.stage_id,
                    context.stage_version,
                ),
            )
            run_updated = self._connection.execute(
                """
                UPDATE agent_run
                SET status = ?, version = version + 1,
                    used_input_tokens = used_input_tokens + ?,
                    used_output_tokens = used_output_tokens + ?,
                    used_total_tokens = used_total_tokens + ?,
                    used_cost_minor = used_cost_minor + ?,
                    reserved_input_tokens = 0, reserved_output_tokens = 0,
                    reserved_total_tokens = 0, reserved_cost_minor = 0,
                    cancellation_requested = CASE WHEN ? THEN 1
                                                  ELSE cancellation_requested END,
                    updated_at = ?, completed_at = ?
                WHERE id = ? AND case_id = ? AND status = 'running'
                """,
                (
                    run_status.value,
                    charged_input,
                    charged_output,
                    charged_input + charged_output,
                    charged_cost,
                    int(cancelled),
                    timestamp,
                    timestamp if cancelled else None,
                    context.run_id,
                    context.case_id,
                ),
            )
            if stage_updated.rowcount != 1 or run_updated.rowcount != 1:
                raise AgentIntegrityError(
                    "Agent failure state changed concurrently",
                    code=AgentErrorCode.INTEGRITY,
                )
            self._connection.execute("COMMIT")
        except AgentIntegrityError:
            self._rollback()
            raise
        except (sqlite3.Error, RuntimeError) as error:
            self._rollback()
            raise AgentPersistenceError(
                "Agent failure transaction was rolled back",
                code=AgentErrorCode.PERSISTENCE,
            ) from error

    def _usage_charge(
        self,
        context: _ReservedCall,
        result: StructuredGenerationResult,
    ) -> tuple[int, int, int, bool]:
        usage = result.metadata.usage
        valid = (
            result.request_id == f"agent-{context.request_scope_sha256[:32]}"
            and result.metadata.request_scope_sha256 == context.request_scope_sha256
            and usage.input_tokens <= context.reservation.input_tokens
            and usage.output_tokens <= context.reservation.output_tokens
            and result.metadata.provider in context.definition.allowed_providers
        )
        cost = context.reservation.cost_minor
        if usage.cost_minor_units is not None:
            if (
                usage.cost_currency != "CNY"
                or usage.cost_minor_units > context.reservation.cost_minor
            ):
                valid = False
            else:
                cost = usage.cost_minor_units
        if not valid:
            return (
                context.reservation.input_tokens,
                context.reservation.output_tokens,
                context.reservation.cost_minor,
                False,
            )
        return usage.input_tokens, usage.output_tokens, cost, True

    def _reserved_state_matches(
        self,
        row: sqlite3.Row,
        context: _ReservedCall,
        *,
        check_version: bool = True,
    ) -> bool:
        return (
            str(row["status"]) == "running"
            and (not check_version or int(row["version"]) == context.stage_version)
            and int(row["reserved_input_tokens"]) == context.reservation.input_tokens
            and int(row["reserved_output_tokens"]) == context.reservation.output_tokens
            and int(row["reserved_cost_minor"]) == context.reservation.cost_minor
        )

    def _cancellation_requested(self, case_id: str, run_id: str) -> bool:
        row = self._connection.execute(
            """
            SELECT cancellation_requested FROM agent_run
            WHERE id = ? AND case_id = ?
            """,
            (run_id, case_id),
        ).fetchone()
        return row is not None and bool(row[0])

    def _inject_failure(self, event: str) -> None:
        if self._failure_injector is not None:
            self._failure_injector(event)

    def get_run(self, case_id: str, run_id: str) -> AgentRunRecord:
        """Read one run and its stages using the explicit Case scope."""

        try:
            row = self._connection.execute(
                "SELECT * FROM agent_run WHERE id = ? AND case_id = ?",
                (run_id, case_id),
            ).fetchone()
            if row is None:
                if not self._case_exists(case_id):
                    raise AgentCaseNotFoundError(
                        "Agent Case was not found",
                        code=AgentErrorCode.CASE_NOT_FOUND,
                    )
                raise AgentRunNotFoundError(
                    "Agent run was not found",
                    code=AgentErrorCode.RUN_NOT_FOUND,
                )
            stage_rows = self._connection.execute(
                """
                SELECT * FROM agent_stage_run
                WHERE run_id = ? AND case_id = ?
                ORDER BY CASE stage
                    WHEN 'extract' THEN 0 WHEN 'normalize' THEN 1
                    WHEN 'link_claims' THEN 2 WHEN 'classify_case' THEN 3
                    WHEN 'check_gaps' THEN 4 WHEN 'propose_actions' THEN 5
                    WHEN 'validate' THEN 6 END, id
                """,
                (run_id, case_id),
            ).fetchall()
            stages = tuple(self._stage_from_row(stage_row) for stage_row in stage_rows)
            return self._run_from_row(row, stages)
        except (AgentCaseNotFoundError, AgentRunNotFoundError, AgentIntegrityError):
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise AgentPersistenceError(
                "Agent run lookup failed",
                code=AgentErrorCode.PERSISTENCE,
            ) from error

    def request_cancellation(self, command: CancelAgentRunCommand) -> AgentRunRecord:
        """Record cancellation without relying on process-local task state."""

        timestamp = self._utc_now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._required_run_row(command.case_id, command.run_id)
            version = int(row["version"])
            if version != command.expected_version:
                raise AgentVersionConflictError(version)
            status = AgentRunStatus(str(row["status"]))
            if status in {AgentRunStatus.CANCELLED, AgentRunStatus.COMPLETED}:
                raise AgentSequenceError(
                    "Agent run cannot be cancelled",
                    code=AgentErrorCode.INVALID_SEQUENCE,
                )
            next_status = (
                AgentRunStatus.RUNNING
                if status is AgentRunStatus.RUNNING
                else AgentRunStatus.CANCELLED
            )
            completed_at = timestamp if next_status is AgentRunStatus.CANCELLED else None
            self._connection.execute(
                """
                UPDATE agent_run
                SET status = ?, version = version + 1, cancellation_requested = 1,
                    updated_at = ?, completed_at = ?
                WHERE id = ? AND case_id = ? AND version = ?
                """,
                (
                    next_status.value,
                    timestamp,
                    completed_at,
                    command.run_id,
                    command.case_id,
                    version,
                ),
            )
            self._connection.execute("COMMIT")
        except (
            AgentCaseNotFoundError,
            AgentRunNotFoundError,
            AgentSequenceError,
            AgentVersionConflictError,
        ):
            self._rollback()
            raise
        except sqlite3.Error as error:
            self._rollback()
            raise AgentPersistenceError(
                "Agent cancellation transaction was rolled back",
                code=AgentErrorCode.PERSISTENCE,
            ) from error
        active_cancellation = self._active_cancellations.get(command.run_id)
        if active_cancellation is not None:
            active_cancellation.set()
        return self.get_run(command.case_id, command.run_id)

    def recover_interrupted(self, command: RecoverAgentRunCommand) -> AgentRunRecord:
        """Conservatively charge an orphaned reservation and stop for review."""

        timestamp = self._utc_now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._required_run_row(command.case_id, command.run_id)
            version = int(row["version"])
            if version != command.expected_version:
                raise AgentVersionConflictError(version)
            if AgentRunStatus(str(row["status"])) is not AgentRunStatus.RUNNING:
                raise AgentSequenceError(
                    "Agent run has no interrupted stage",
                    code=AgentErrorCode.INVALID_SEQUENCE,
                )
            stage_row = self._connection.execute(
                """
                SELECT * FROM agent_stage_run
                WHERE run_id = ? AND case_id = ? AND status = 'running'
                """,
                (command.run_id, command.case_id),
            ).fetchone()
            if stage_row is None:
                raise AgentIntegrityError(
                    "Agent running state is inconsistent",
                    code=AgentErrorCode.INTEGRITY,
                )
            self._charge_interrupted_stage(stage_row, timestamp)
            self._connection.execute(
                """
                UPDATE agent_run
                SET status = 'needs_review', version = version + 1,
                    used_input_tokens = used_input_tokens + reserved_input_tokens,
                    used_output_tokens = used_output_tokens + reserved_output_tokens,
                    used_total_tokens = used_total_tokens + reserved_total_tokens,
                    used_cost_minor = used_cost_minor + reserved_cost_minor,
                    reserved_input_tokens = 0, reserved_output_tokens = 0,
                    reserved_total_tokens = 0, reserved_cost_minor = 0,
                    updated_at = ?
                WHERE id = ? AND case_id = ? AND version = ?
                """,
                (timestamp, command.run_id, command.case_id, version),
            )
            self._connection.execute("COMMIT")
        except (
            AgentCaseNotFoundError,
            AgentIntegrityError,
            AgentRunNotFoundError,
            AgentSequenceError,
            AgentVersionConflictError,
        ):
            self._rollback()
            raise
        except sqlite3.Error as error:
            self._rollback()
            raise AgentPersistenceError(
                "Agent recovery transaction was rolled back",
                code=AgentErrorCode.PERSISTENCE,
            ) from error
        return self.get_run(command.case_id, command.run_id)

    def _charge_interrupted_stage(self, row: sqlite3.Row, timestamp: str) -> None:
        updated = self._connection.execute(
            """
            UPDATE agent_stage_run
            SET status = 'needs_review', version = version + 1,
                used_input_tokens = used_input_tokens + reserved_input_tokens,
                used_output_tokens = used_output_tokens + reserved_output_tokens,
                used_total_tokens = used_total_tokens + reserved_total_tokens,
                used_cost_minor = used_cost_minor + reserved_cost_minor,
                reserved_input_tokens = 0, reserved_output_tokens = 0,
                reserved_total_tokens = 0, reserved_cost_minor = 0,
                error_code = ?, retryable = 1, updated_at = ?, completed_at = ?
            WHERE id = ? AND version = ? AND status = 'running'
            """,
            (
                AgentErrorCode.INTERRUPTED.value,
                timestamp,
                timestamp,
                str(row["id"]),
                int(row["version"]),
            ),
        )
        if updated.rowcount != 1:
            raise AgentIntegrityError(
                "Agent interrupted stage changed concurrently",
                code=AgentErrorCode.INTEGRITY,
            )

    def _required_run_row(self, case_id: str, run_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM agent_run WHERE id = ? AND case_id = ?",
            (run_id, case_id),
        ).fetchone()
        if row is not None:
            return cast(sqlite3.Row, row)
        if not self._case_exists(case_id):
            raise AgentCaseNotFoundError(
                "Agent Case was not found",
                code=AgentErrorCode.CASE_NOT_FOUND,
            )
        raise AgentRunNotFoundError(
            "Agent run was not found",
            code=AgentErrorCode.RUN_NOT_FOUND,
        )

    def _case_exists(self, case_id: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM case_record WHERE id = ?",
                (case_id,),
            ).fetchone()
            is not None
        )

    def _run_from_row(
        self,
        row: sqlite3.Row,
        stages: tuple[AgentStageRecord, ...],
    ) -> AgentRunRecord:
        binding_json, binding_sha256 = self._stored_binding(row)
        budget = AgentBudget(
            max_calls=int(row["max_calls"]),
            max_input_tokens=int(row["max_input_tokens"]),
            max_output_tokens=int(row["max_output_tokens"]),
            max_cost_minor=int(row["max_cost_minor"]),
            cost_currency=str(row["cost_currency"]),
        )
        return AgentRunRecord(
            id=str(row["id"]),
            case_id=str(row["case_id"]),
            status=AgentRunStatus(str(row["status"])),
            version=int(row["version"]),
            current_stage=_optional_stage(row["current_stage"]),
            last_completed_stage=_optional_stage(row["last_completed_stage"]),
            budget=budget,
            usage=_usage(row),
            cancellation_requested=bool(row["cancellation_requested"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=_optional_text(row["completed_at"]),
            stages=stages,
            input_binding_json=binding_json,
            input_binding_sha256=binding_sha256,
        )

    @staticmethod
    def _stored_binding(row: sqlite3.Row) -> tuple[str | None, str | None]:
        binding_json = _optional_text(row["input_binding_json"])
        binding_sha256 = _optional_text(row["input_binding_sha256"])
        try:
            validate_run_binding(str(row["id"]), str(row["case_id"]), binding_json, binding_sha256)
        except AgentValidationError as error:
            raise AgentIntegrityError(
                "Agent question input binding is invalid", code=AgentErrorCode.INTEGRITY
            ) from error
        return binding_json, binding_sha256

    def _require_current_bound_inputs(self, row: sqlite3.Row) -> None:
        binding_json, _binding_sha256 = self._stored_binding(row)
        if binding_json is not None:
            from lifecase.agent.question_context import require_current_binding

            require_current_binding(self._connection, binding_json, str(row["case_id"]))

    def _stage_from_row(self, row: sqlite3.Row) -> AgentStageRecord:
        stage = AgentStageName(str(row["stage"]))
        definition = self._registry.get(stage)
        if (
            str(row["stage_version"]) != definition.stage_version
            or str(row["input_schema_version"]) != definition.input_schema_version
            or str(row["output_schema_version"]) != definition.output_schema_version
            or str(row["checkpoint_schema_version"]) != definition.checkpoint_schema_version
        ):
            raise AgentIntegrityError(
                "Agent stage version is inconsistent",
                code=AgentErrorCode.INTEGRITY,
            )
        checkpoint = self._checkpoint_from_row(row, stage)
        return AgentStageRecord(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            case_id=str(row["case_id"]),
            stage=stage,
            stage_version=definition.stage_version,
            input_schema_version=definition.input_schema_version,
            output_schema_version=definition.output_schema_version,
            checkpoint_schema_version=definition.checkpoint_schema_version,
            input_scope_sha256=str(row["input_scope_sha256"]),
            status=AgentStageStatus(str(row["status"])),
            version=int(row["version"]),
            budget=StageBudget(
                max_calls=int(row["max_calls"]),
                max_input_tokens=int(row["max_input_tokens"]),
                max_output_tokens=int(row["max_output_tokens"]),
                max_cost_minor=int(row["max_cost_minor"]),
                cost_currency=str(row["cost_currency"]),
            ),
            usage=_usage(row),
            repair_count=int(row["repair_count"]),
            provider=_optional_text(row["provider"]),
            model_id=_optional_text(row["model_id"]),
            latency_ms=int(row["latency_ms"]),
            checkpoint=checkpoint,
            error_code=_optional_text(row["error_code"]),
            retryable=_optional_bool(row["retryable"]),
            started_at=str(row["started_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=_optional_text(row["completed_at"]),
        )

    def _checkpoint_from_row(
        self,
        row: sqlite3.Row,
        stage: AgentStageName,
    ) -> AgentCheckpoint | None:
        raw = _optional_text(row["checkpoint_json"])
        stored_hash = _optional_text(row["checkpoint_sha256"])
        if raw is None and stored_hash is None:
            return None
        if raw is None or stored_hash is None:
            raise AgentIntegrityError(
                "Agent checkpoint is inconsistent",
                code=AgentErrorCode.INTEGRITY,
            )
        try:
            canonical, calculated = checkpoint_payload(json.loads(raw))
        except (json.JSONDecodeError, ValueError) as error:
            raise AgentIntegrityError(
                "Agent checkpoint is invalid",
                code=AgentErrorCode.INTEGRITY,
            ) from error
        if canonical != raw or calculated != stored_hash:
            raise AgentIntegrityError(
                "Agent checkpoint hash is invalid",
                code=AgentErrorCode.INTEGRITY,
            )
        return AgentCheckpoint(
            stage=stage,
            stage_version=str(row["stage_version"]),
            schema_version=str(row["checkpoint_schema_version"]),
            input_scope_sha256=str(row["input_scope_sha256"]),
            payload_json=raw,
            payload_sha256=stored_hash,
        )

    def _utc_now(self) -> str:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise AgentPersistenceError(
                "Agent clock is invalid",
                code=AgentErrorCode.PERSISTENCE,
            )
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")


def _usage(row: sqlite3.Row) -> BudgetUsage:
    return BudgetUsage(
        calls=int(row["used_calls"]),
        input_tokens=int(row["used_input_tokens"]),
        output_tokens=int(row["used_output_tokens"]),
        cost_minor=int(row["used_cost_minor"]),
        reserved_input_tokens=int(row["reserved_input_tokens"]),
        reserved_output_tokens=int(row["reserved_output_tokens"]),
        reserved_cost_minor=int(row["reserved_cost_minor"]),
        cost_currency=str(row["cost_currency"]),
    )


def _optional_stage(value: object) -> AgentStageName | None:
    return None if value is None else AgentStageName(str(value))


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_bool(value: object) -> bool | None:
    return None if value is None else bool(value)


def _row_has_capacity(row: sqlite3.Row, reservation: CallReservation) -> bool:
    return (
        int(row["used_calls"]) + 1 <= int(row["max_calls"])
        and int(row["used_input_tokens"])
        + int(row["reserved_input_tokens"])
        + reservation.input_tokens
        <= int(row["max_input_tokens"])
        and int(row["used_output_tokens"])
        + int(row["reserved_output_tokens"])
        + reservation.output_tokens
        <= int(row["max_output_tokens"])
        and int(row["used_total_tokens"])
        + int(row["reserved_total_tokens"])
        + reservation.total_tokens
        <= int(row["max_total_tokens"])
        and int(row["used_cost_minor"]) + int(row["reserved_cost_minor"]) + reservation.cost_minor
        <= int(row["max_cost_minor"])
    )


def _row_can_account_and_reserve(
    row: sqlite3.Row,
    reservation: CallReservation,
    charge: tuple[int, int, int],
) -> bool:
    charged_input, charged_output, charged_cost = charge
    return (
        int(row["used_calls"]) + 1 <= int(row["max_calls"])
        and int(row["used_input_tokens"]) + charged_input + reservation.input_tokens
        <= int(row["max_input_tokens"])
        and int(row["used_output_tokens"]) + charged_output + reservation.output_tokens
        <= int(row["max_output_tokens"])
        and int(row["used_total_tokens"])
        + charged_input
        + charged_output
        + reservation.total_tokens
        <= int(row["max_total_tokens"])
        and int(row["used_cost_minor"]) + charged_cost + reservation.cost_minor
        <= int(row["max_cost_minor"])
    )


def _stage_id(run_id: str, stage: AgentStageName) -> str:
    digest = hashlib.sha256(f"{run_id}:{stage.value}".encode()).hexdigest()
    return f"agent-stage-{digest[:32]}"


def stage_call_scope_sha256(
    run_id: str,
    stage: AgentStageName,
    input_scope_sha256: str,
    call_number: int,
) -> str:
    """Build the deterministic fake-script scope for one bounded stage call."""

    if not isinstance(stage, AgentStageName) or call_number not in {1, 2}:
        raise AgentValidationError("Agent call scope is invalid")
    return request_scope_sha256(
        "agent_stage_call.v1",
        run_id,
        stage.value,
        input_scope_sha256,
        str(call_number),
    )
