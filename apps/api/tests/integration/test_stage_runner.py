"""M3-02 durable stage orchestration integration coverage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lifecase.agent import (
    BUILT_IN_STAGE_REGISTRY,
    MAX_AGENT_CALLS,
    MAX_CASE_COST_MINOR_CNY,
    STAGE_ORDER,
    AgentBudget,
    AgentBudgetError,
    AgentCancelledError,
    AgentCaseNotFoundError,
    AgentCheckpoint,
    AgentIntegrityError,
    AgentPersistenceError,
    AgentProviderFailureError,
    AgentRunNotFoundError,
    AgentRunStatus,
    AgentSequenceError,
    AgentStageName,
    AgentStageSchemaError,
    AgentStageStatus,
    AgentStageTimeoutError,
    AgentValidationError,
    AgentVersionConflictError,
    CallReservation,
    CancelAgentRunCommand,
    CreateAgentRunCommand,
    ExecuteStageCommand,
    RecoverAgentRunCommand,
    StageBudget,
    StageInput,
    StageRegistry,
    StageRunner,
    stage_call_scope_sha256,
    stage_input_scope_sha256,
    validate_stage_output,
)
from lifecase.cases.service import CaseService
from lifecase.providers import (
    DisabledModelProvider,
    ModelProviderKind,
    ProviderCallMetadata,
    ProviderErrorCode,
    ProviderNetworkError,
    ProviderUsage,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)
from lifecase.storage import apply_migrations, connect_database


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _database(path: Path) -> tuple[sqlite3.Connection, str]:
    connection = connect_database(path)
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic warranty",
        goal="Exercise durable orchestration",
        template_id="warranty",
    )
    return connection, case.id


def _clock() -> datetime:
    return datetime(2026, 8, 2, 0, 0, tzinfo=UTC)


class _ValidProvider:
    def __init__(self, usage: ProviderUsage | None = None) -> None:
        self.usage = usage or ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15)
        self.requests: list[StructuredGenerationRequest] = []

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        self.requests.append(request)
        properties = request.output_schema()["properties"]
        output = {
            "stage": properties["stage"]["const"],
            "stage_version": properties["stage_version"]["const"],
            "checkpoint_schema_version": properties["checkpoint_schema_version"]["const"],
            "checkpoint": {
                "synthetic_stage": properties["stage"]["const"],
                "fixture_id": "warranty-001",
            },
        }
        return _result(request, output, self.usage)


class _ScriptedProvider:
    def __init__(
        self,
        scripts: dict[str, tuple[dict[str, object], ProviderUsage]],
    ) -> None:
        self.scripts = scripts
        self.requests: list[StructuredGenerationRequest] = []

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        self.requests.append(request)
        output, usage = self.scripts[request.request_scope_sha256]
        return _result(request, output, usage)


class _BlockingProvider:
    def __init__(self) -> None:
        self.started = False

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        self.started = True
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _RetryableFailureProvider:
    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        raise ProviderNetworkError(
            "synthetic network failure",
            code=ProviderErrorCode.NETWORK,
            retryable=True,
        )


def _result(
    request: StructuredGenerationRequest,
    output: dict[str, object],
    usage: ProviderUsage,
) -> StructuredGenerationResult:
    return StructuredGenerationResult(
        request_id=request.request_id,
        output_json=json.dumps(output),
        metadata=ProviderCallMetadata(
            provider=ModelProviderKind.FAKE,
            model_id="lifecase-fake-v1",
            request_scope_sha256=request.request_scope_sha256,
            latency_ms=2,
            usage=usage,
        ),
    )


def _stage_input(
    case_id: str,
    stage: AgentStageName,
    prior_checkpoint_hashes: tuple[str, ...] = (),
    marker: str = "input-only-not-persisted",
) -> StageInput:
    definition = BUILT_IN_STAGE_REGISTRY.get(stage)
    payload_json = json.dumps(
        {
            "case_id": case_id,
            "prior_checkpoint_hashes": list(prior_checkpoint_hashes),
            "stage_payload": {"synthetic_marker": marker},
        }
    )
    return StageInput(
        case_id=case_id,
        stage=stage,
        stage_version=definition.stage_version,
        input_schema_version=definition.input_schema_version,
        input_scope_sha256=stage_input_scope_sha256(
            case_id,
            stage,
            definition.stage_version,
            definition.input_schema_version,
            payload_json,
        ),
        payload_json=payload_json,
    )


def _stage_command(
    run_version: int,
    stage_input: StageInput,
    *,
    stage_version: int | None = None,
    reservation: CallReservation | None = None,
    budget: StageBudget | None = None,
) -> ExecuteStageCommand:
    return ExecuteStageCommand(
        run_id="run-1",
        expected_run_version=run_version,
        expected_stage_version=stage_version,
        stage_input=stage_input,
        stage_budget=budget or StageBudget(),
        reservation=reservation or CallReservation(100, 50, 1),
    )


def test_stage_registry_is_fixed_versioned_and_has_no_tools() -> None:
    definitions = BUILT_IN_STAGE_REGISTRY.definitions

    assert tuple(definition.stage for definition in definitions) == STAGE_ORDER
    assert len(definitions) == 7
    assert all(definition.allowed_tools == () for definition in definitions)
    assert all(
        definition.allowed_providers == tuple(ModelProviderKind) for definition in definitions
    )
    assert all(definition.max_repair_attempts == 1 for definition in definitions)
    assert all(definition.timeout_seconds == 30.0 for definition in definitions)
    for definition in definitions:
        assert definition.stage_version == f"{definition.stage.value}.v1"
        assert definition.input_schema_version.endswith(".input.v1")
        assert definition.output_schema_version.endswith(".output.v1")
        assert definition.checkpoint_schema_version.endswith(".checkpoint.v1")
        schema = json.loads(definition.output_schema_json)
        assert schema["additionalProperties"] is False
        input_schema = json.loads(definition.input_schema_json)
        assert input_schema["additionalProperties"] is False

    with pytest.raises(FrozenInstanceError):
        definitions[0].stage_version = "changed"  # type: ignore[misc]


def test_budget_and_stage_input_boundaries_are_fail_closed() -> None:
    assert AgentBudget().max_calls == MAX_AGENT_CALLS
    assert AgentBudget().max_cost_minor == MAX_CASE_COST_MINOR_CNY
    assert StageBudget().max_calls == 2
    assert CallReservation(1, 1, 0).total_tokens == 2

    with pytest.raises(AgentValidationError):
        AgentBudget(max_cost_minor=101)
    with pytest.raises(AgentValidationError):
        StageBudget(max_calls=3)
    with pytest.raises(AgentValidationError):
        CallReservation(1, 1, 1, cost_currency="USD")
    with pytest.raises(AgentValidationError):
        StageInput(
            case_id="case-1",
            stage=AgentStageName.EXTRACT,
            stage_version="extract.v1",
            input_schema_version="extract.input.v1",
            input_scope_sha256="bad",
            payload_json="{}",
        )


def test_stage_output_requires_exact_envelope_and_bounded_checkpoint() -> None:
    definition = BUILT_IN_STAGE_REGISTRY.get(AgentStageName.EXTRACT)
    scope = _sha256("synthetic-scope")
    output = json.dumps(
        {
            "stage": "extract",
            "stage_version": "extract.v1",
            "checkpoint_schema_version": "extract.checkpoint.v1",
            "checkpoint": {"synthetic_marker": "warranty-001", "items": [1, 2]},
        }
    )

    checkpoint = validate_stage_output(
        definition,
        output,
        input_scope_sha256=scope,
    )

    assert checkpoint.stage is AgentStageName.EXTRACT
    assert checkpoint.input_scope_sha256 == scope
    assert json.loads(checkpoint.payload_json) == {
        "items": [1, 2],
        "synthetic_marker": "warranty-001",
    }
    assert checkpoint.payload_sha256 == _sha256(checkpoint.payload_json)

    invalid_values: tuple[dict[str, object], ...] = (
        {},
        {
            "stage": "normalize",
            "stage_version": "extract.v1",
            "checkpoint_schema_version": "extract.checkpoint.v1",
            "checkpoint": {},
        },
        {
            "stage": "extract",
            "stage_version": "extract.v1",
            "checkpoint_schema_version": "extract.checkpoint.v1",
            "checkpoint": {"raw_prompt": "must not persist"},
        },
    )
    for value in invalid_values:
        with pytest.raises(AgentValidationError):
            validate_stage_output(
                definition,
                json.dumps(value),
                input_scope_sha256=scope,
            )


def test_create_run_is_case_scoped_idempotent_and_restart_safe(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    runner = StageRunner(connection, DisabledModelProvider(), clock=_clock)
    command = CreateAgentRunCommand(run_id="run-1", case_id=case_id)
    try:
        created = runner.create_run(command)
        repeated = runner.create_run(command)

        assert created == repeated
        assert created.status is AgentRunStatus.QUEUED
        assert created.version == 1
        assert created.case_id == case_id
        assert created.stages == ()
        assert created.usage.calls == 0
        assert created.budget.max_cost_minor == 100

        connection.close()
        connection = connect_database(tmp_path / "lifecase.sqlite3")
        restarted = StageRunner(connection, DisabledModelProvider(), clock=_clock)
        assert restarted.get_run(case_id, "run-1") == created
    finally:
        connection.close()


def test_create_run_rejects_missing_case_and_second_pipeline(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    runner = StageRunner(connection, DisabledModelProvider(), clock=_clock)
    try:
        with pytest.raises(AgentCaseNotFoundError):
            runner.create_run(CreateAgentRunCommand(run_id="missing", case_id="missing-case"))

        runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
        with pytest.raises(AgentSequenceError):
            runner.create_run(CreateAgentRunCommand(run_id="run-2", case_id=case_id))
        assert runner.get_run(case_id, "run-1").id == "run-1"
    finally:
        connection.close()


def test_queued_cancellation_is_versioned_and_durable(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    runner = StageRunner(connection, DisabledModelProvider(), clock=_clock)
    try:
        created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
        with pytest.raises(AgentVersionConflictError):
            runner.request_cancellation(
                CancelAgentRunCommand(
                    run_id="run-1",
                    case_id=case_id,
                    expected_version=created.version + 1,
                )
            )

        cancelled = runner.request_cancellation(
            CancelAgentRunCommand(
                run_id="run-1",
                case_id=case_id,
                expected_version=created.version,
            )
        )

        assert cancelled.status is AgentRunStatus.CANCELLED
        assert cancelled.version == created.version + 1
        assert cancelled.cancellation_requested is True
        assert cancelled.completed_at == "2026-08-02T00:00:00.000000Z"
        with pytest.raises(AgentSequenceError):
            runner.request_cancellation(
                CancelAgentRunCommand(
                    run_id="run-1",
                    case_id=case_id,
                    expected_version=cancelled.version,
                )
            )
    finally:
        connection.close()


def test_all_stages_checkpoint_in_order_with_case_budget_and_no_business_writes(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    provider = _ValidProvider()
    runner = StageRunner(connection, provider, clock=_clock)
    run = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    before = tuple(
        connection.execute(
            "SELECT version, status FROM case_record WHERE id = ?", (case_id,)
        ).fetchone()
    )
    try:
        for stage in STAGE_ORDER:
            prior_hashes = tuple(
                item.checkpoint.payload_sha256 for item in run.stages if item.checkpoint is not None
            )
            run = asyncio.run(
                runner.execute_stage(
                    _stage_command(run.version, _stage_input(case_id, stage, prior_hashes))
                )
            )

        assert run.status is AgentRunStatus.COMPLETED
        assert run.last_completed_stage is AgentStageName.VALIDATE
        assert tuple(stage.stage for stage in run.stages) == STAGE_ORDER
        assert all(stage.status is AgentStageStatus.CHECKPOINTED for stage in run.stages)
        assert all(isinstance(stage.checkpoint, AgentCheckpoint) for stage in run.stages)
        assert run.usage.calls == 7
        assert run.usage.input_tokens == 70
        assert run.usage.output_tokens == 35
        assert run.usage.cost_minor == 7
        assert run.usage.reserved_total_tokens == 0
        assert len(provider.requests) == 7
        assert (
            tuple(
                connection.execute(
                    "SELECT version, status FROM case_record WHERE id = ?", (case_id,)
                ).fetchone()
            )
            == before
        )
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(agent_stage_run)")}
        assert "payload_json" not in columns
        assert "prompt" not in columns
        assert all(
            "input-only-not-persisted" not in str(row[0])
            for row in connection.execute("SELECT checkpoint_json FROM agent_stage_run")
        )
    finally:
        connection.close()


def test_invalid_stage_output_gets_exactly_one_repair(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    stage_input = _stage_input(case_id, AgentStageName.EXTRACT)
    first_scope = stage_call_scope_sha256(
        "run-1", AgentStageName.EXTRACT, stage_input.input_scope_sha256, 1
    )
    second_scope = stage_call_scope_sha256(
        "run-1", AgentStageName.EXTRACT, stage_input.input_scope_sha256, 2
    )
    usage = ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    valid = {
        "stage": "extract",
        "stage_version": "extract.v1",
        "checkpoint_schema_version": "extract.checkpoint.v1",
        "checkpoint": {"fixture_id": "repaired-warranty-001"},
    }
    provider = _ScriptedProvider({first_scope: ({}, usage), second_scope: (valid, usage)})
    runner = StageRunner(connection, provider, clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    try:
        run = asyncio.run(runner.execute_stage(_stage_command(created.version, stage_input)))

        assert run.status is AgentRunStatus.CHECKPOINTED
        assert run.usage.calls == 2
        assert len(provider.requests) == 2
        stage = run.stages[0]
        assert stage.repair_count == 1
        assert stage.usage.calls == 2
        assert stage.version == 3
        assert stage.error_code is None
        assert stage.checkpoint is not None
        assert json.loads(stage.checkpoint.payload_json)["fixture_id"] == ("repaired-warranty-001")
        assert "{}" not in stage.checkpoint.payload_json
    finally:
        connection.close()


def test_second_invalid_output_stops_without_checkpoint_or_third_call(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    stage_input = _stage_input(case_id, AgentStageName.EXTRACT)
    usage = ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    scopes = (
        stage_call_scope_sha256(
            "run-1", AgentStageName.EXTRACT, stage_input.input_scope_sha256, call
        )
        for call in (1, 2)
    )
    provider = _ScriptedProvider({scope: ({}, usage) for scope in scopes})
    runner = StageRunner(connection, provider, clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    try:
        with pytest.raises(AgentStageSchemaError):
            asyncio.run(runner.execute_stage(_stage_command(created.version, stage_input)))

        failed = runner.get_run(case_id, "run-1")
        assert failed.status is AgentRunStatus.NEEDS_REVIEW
        assert failed.usage.calls == 2
        assert len(provider.requests) == 2
        assert failed.stages[0].repair_count == 1
        assert failed.stages[0].checkpoint is None
        assert failed.stages[0].error_code == "agent_schema_invalid"
    finally:
        connection.close()


def test_budget_exhaustion_stops_before_provider_and_is_durable(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    provider = _ValidProvider()
    runner = StageRunner(connection, provider, clock=_clock)
    created = runner.create_run(
        CreateAgentRunCommand(
            run_id="run-1",
            case_id=case_id,
            budget=AgentBudget(max_cost_minor=0),
        )
    )
    try:
        with pytest.raises(AgentBudgetError):
            asyncio.run(
                runner.execute_stage(
                    _stage_command(created.version, _stage_input(case_id, AgentStageName.EXTRACT))
                )
            )

        stopped = runner.get_run(case_id, "run-1")
        assert stopped.status is AgentRunStatus.NEEDS_REVIEW
        assert stopped.usage.calls == 0
        assert stopped.usage.cost_minor == 0
        assert stopped.stages[0].error_code == "agent_budget_exhausted"
        assert provider.requests == []
    finally:
        connection.close()


def test_missing_cost_charges_reservation_and_excess_cost_fails_closed(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "missing-cost.sqlite3")
    provider = _ValidProvider(ProviderUsage(input_tokens=2, output_tokens=1, total_tokens=3))
    runner = StageRunner(connection, provider, clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    try:
        completed = asyncio.run(
            runner.execute_stage(
                _stage_command(
                    created.version,
                    _stage_input(case_id, AgentStageName.EXTRACT),
                    reservation=CallReservation(10, 5, 3),
                )
            )
        )
        assert completed.usage.input_tokens == 2
        assert completed.usage.output_tokens == 1
        assert completed.usage.cost_minor == 3
    finally:
        connection.close()

    connection, case_id = _database(tmp_path / "excess-cost.sqlite3")
    provider = _ValidProvider(
        ProviderUsage(
            input_tokens=2,
            output_tokens=1,
            total_tokens=3,
            cost_minor_units=2,
            cost_currency="CNY",
        )
    )
    runner = StageRunner(connection, provider, clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    try:
        with pytest.raises(AgentProviderFailureError):
            asyncio.run(
                runner.execute_stage(
                    _stage_command(
                        created.version,
                        _stage_input(case_id, AgentStageName.EXTRACT),
                        reservation=CallReservation(10, 5, 1),
                    )
                )
            )
        stopped = runner.get_run(case_id, "run-1")
        assert stopped.status is AgentRunStatus.NEEDS_REVIEW
        assert stopped.usage.input_tokens == 10
        assert stopped.usage.output_tokens == 5
        assert stopped.usage.cost_minor == 1
        assert stopped.stages[0].checkpoint is None
    finally:
        connection.close()


def test_stage_order_scope_and_optimistic_versions_fail_without_calls(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    provider = _ValidProvider()
    runner = StageRunner(connection, provider, clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    other_case = CaseService(connection).create_case(
        title="Other synthetic warranty",
        goal="Prove Case isolation",
        template_id="warranty",
    )
    try:
        with pytest.raises(AgentSequenceError):
            asyncio.run(
                runner.execute_stage(
                    _stage_command(created.version, _stage_input(case_id, AgentStageName.NORMALIZE))
                )
            )
        with pytest.raises(AgentVersionConflictError):
            asyncio.run(
                runner.execute_stage(
                    _stage_command(
                        created.version + 1,
                        _stage_input(case_id, AgentStageName.EXTRACT),
                    )
                )
            )
        with pytest.raises(AgentRunNotFoundError):
            asyncio.run(
                runner.execute_stage(
                    _stage_command(
                        created.version,
                        _stage_input(other_case.id, AgentStageName.EXTRACT),
                    )
                )
            )
        assert provider.requests == []
        assert runner.get_run(case_id, "run-1") == created
    finally:
        connection.close()


def test_retryable_provider_failure_is_recorded_without_implicit_retry(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    runner = StageRunner(connection, _RetryableFailureProvider(), clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    try:
        with pytest.raises(AgentProviderFailureError) as caught:
            asyncio.run(
                runner.execute_stage(
                    _stage_command(created.version, _stage_input(case_id, AgentStageName.EXTRACT))
                )
            )
        stopped = runner.get_run(case_id, "run-1")
        assert caught.value.retryable is True
        assert stopped.status is AgentRunStatus.NEEDS_REVIEW
        assert stopped.usage.calls == 1
        assert stopped.stages[0].retryable is True
        assert stopped.stages[0].checkpoint is None
    finally:
        connection.close()


def test_disabled_provider_is_non_retryable_and_changes_only_agent_tables(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    runner = StageRunner(connection, DisabledModelProvider(), clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    case_before = tuple(
        connection.execute(
            "SELECT status, version FROM case_record WHERE id = ?", (case_id,)
        ).fetchone()
    )
    try:
        with pytest.raises(AgentProviderFailureError) as caught:
            asyncio.run(
                runner.execute_stage(
                    _stage_command(created.version, _stage_input(case_id, AgentStageName.EXTRACT))
                )
            )
        stopped = runner.get_run(case_id, "run-1")
        assert caught.value.retryable is False
        assert stopped.status is AgentRunStatus.NEEDS_REVIEW
        assert stopped.stages[0].retryable is False
        assert (
            tuple(
                connection.execute(
                    "SELECT status, version FROM case_record WHERE id = ?", (case_id,)
                ).fetchone()
            )
            == case_before
        )
    finally:
        connection.close()


def test_running_stage_can_be_cancelled_and_reservation_is_reconciled(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    provider = _BlockingProvider()
    runner = StageRunner(connection, provider, clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))

    async def scenario() -> None:
        task = asyncio.create_task(
            runner.execute_stage(
                _stage_command(created.version, _stage_input(case_id, AgentStageName.EXTRACT))
            )
        )
        while not provider.started:
            await asyncio.sleep(0)
        running = runner.get_run(case_id, "run-1")
        requested = runner.request_cancellation(
            CancelAgentRunCommand(
                run_id="run-1",
                case_id=case_id,
                expected_version=running.version,
            )
        )
        assert requested.status is AgentRunStatus.RUNNING
        assert requested.cancellation_requested is True
        with pytest.raises(AgentCancelledError):
            await task

    try:
        asyncio.run(scenario())
        cancelled = runner.get_run(case_id, "run-1")
        assert cancelled.status is AgentRunStatus.CANCELLED
        assert cancelled.cancellation_requested is True
        assert cancelled.usage.calls == 1
        assert cancelled.usage.input_tokens == 100
        assert cancelled.usage.output_tokens == 50
        assert cancelled.usage.cost_minor == 1
        assert cancelled.usage.reserved_total_tokens == 0
        assert cancelled.stages[0].status is AgentStageStatus.CANCELLED
        assert cancelled.stages[0].checkpoint is None
    finally:
        connection.close()


def test_stage_timeout_is_bounded_and_stops_for_review(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    definitions = BUILT_IN_STAGE_REGISTRY.definitions
    registry = StageRegistry((replace(definitions[0], timeout_seconds=0.01), *definitions[1:]))
    runner = StageRunner(connection, _BlockingProvider(), registry=registry, clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    try:
        with pytest.raises(AgentStageTimeoutError):
            asyncio.run(
                runner.execute_stage(
                    _stage_command(created.version, _stage_input(case_id, AgentStageName.EXTRACT))
                )
            )
        stopped = runner.get_run(case_id, "run-1")
        assert stopped.status is AgentRunStatus.NEEDS_REVIEW
        assert stopped.stages[0].error_code == "agent_timeout"
        assert stopped.stages[0].retryable is True
        assert stopped.usage.reserved_total_tokens == 0
    finally:
        connection.close()


def test_interrupted_reservation_recovers_after_restart_without_recalling_provider(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection, case_id = _database(database_path)

    def interrupt(event: str) -> None:
        if event == "after_stage_reservation":
            raise RuntimeError("synthetic process interruption")

    provider = _ValidProvider()
    runner = StageRunner(connection, provider, clock=_clock, failure_injector=interrupt)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    with pytest.raises(RuntimeError, match="synthetic process interruption"):
        asyncio.run(
            runner.execute_stage(
                _stage_command(created.version, _stage_input(case_id, AgentStageName.EXTRACT))
            )
        )
    interrupted = runner.get_run(case_id, "run-1")
    assert interrupted.status is AgentRunStatus.RUNNING
    assert interrupted.usage.calls == 1
    assert interrupted.usage.reserved_total_tokens == 150
    assert provider.requests == []
    connection.close()

    connection = connect_database(database_path)
    try:
        restarted = StageRunner(connection, provider, clock=_clock)
        recovered = restarted.recover_interrupted(
            RecoverAgentRunCommand(
                run_id="run-1",
                case_id=case_id,
                expected_version=interrupted.version,
            )
        )
        assert recovered.status is AgentRunStatus.NEEDS_REVIEW
        assert recovered.usage.input_tokens == 100
        assert recovered.usage.output_tokens == 50
        assert recovered.usage.cost_minor == 1
        assert recovered.usage.reserved_total_tokens == 0
        assert recovered.stages[0].error_code == "agent_interrupted"
        assert recovered.stages[0].checkpoint is None
        assert provider.requests == []
    finally:
        connection.close()


def test_checkpoint_database_failure_rolls_back_and_preserves_recoverable_reservation(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    before_case = tuple(
        connection.execute(
            "SELECT status, version, updated_at FROM case_record WHERE id = ?", (case_id,)
        ).fetchone()
    )

    def fail_finalize(event: str) -> None:
        if event == "before_stage_finalize":
            raise RuntimeError("synthetic finalize failure")

    runner = StageRunner(
        connection,
        _ValidProvider(),
        clock=_clock,
        failure_injector=fail_finalize,
    )
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    try:
        with pytest.raises(AgentPersistenceError):
            asyncio.run(
                runner.execute_stage(
                    _stage_command(created.version, _stage_input(case_id, AgentStageName.EXTRACT))
                )
            )
        interrupted = runner.get_run(case_id, "run-1")
        assert interrupted.status is AgentRunStatus.RUNNING
        assert interrupted.usage.reserved_total_tokens == 150
        assert interrupted.stages[0].checkpoint is None
        assert (
            tuple(
                connection.execute(
                    "SELECT status, version, updated_at FROM case_record WHERE id = ?", (case_id,)
                ).fetchone()
            )
            == before_case
        )

        recovered = StageRunner(connection, _ValidProvider(), clock=_clock).recover_interrupted(
            RecoverAgentRunCommand(
                run_id="run-1",
                case_id=case_id,
                expected_version=interrupted.version,
            )
        )
        assert recovered.status is AgentRunStatus.NEEDS_REVIEW
        assert recovered.usage.reserved_total_tokens == 0
    finally:
        connection.close()


def test_failed_stage_resumes_after_restart_without_changing_prior_checkpoint(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection, case_id = _database(database_path)
    initial_runner = StageRunner(connection, _ValidProvider(), clock=_clock)
    run = initial_runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    extract_input = _stage_input(case_id, AgentStageName.EXTRACT)
    run = asyncio.run(initial_runner.execute_stage(_stage_command(run.version, extract_input)))
    extract_checkpoint = run.stages[0].checkpoint

    failing_runner = StageRunner(connection, _RetryableFailureProvider(), clock=_clock)
    assert extract_checkpoint is not None
    normalize_input = _stage_input(
        case_id,
        AgentStageName.NORMALIZE,
        (extract_checkpoint.payload_sha256,),
    )
    with pytest.raises(AgentProviderFailureError):
        asyncio.run(failing_runner.execute_stage(_stage_command(run.version, normalize_input)))
    failed = failing_runner.get_run(case_id, "run-1")
    assert failed.stages[0].checkpoint == extract_checkpoint
    assert failed.stages[1].status is AgentStageStatus.NEEDS_REVIEW
    connection.close()

    connection = connect_database(database_path)
    try:
        restarted = StageRunner(connection, _ValidProvider(), clock=_clock)
        restored = restarted.get_run(case_id, "run-1")
        resumed = asyncio.run(
            restarted.execute_stage(
                _stage_command(
                    restored.version,
                    normalize_input,
                    stage_version=restored.stages[1].version,
                )
            )
        )
        assert resumed.status is AgentRunStatus.CHECKPOINTED
        assert resumed.last_completed_stage is AgentStageName.NORMALIZE
        assert resumed.stages[0].checkpoint == extract_checkpoint
        assert resumed.stages[1].status is AgentStageStatus.CHECKPOINTED
        assert resumed.stages[1].usage.calls == 2
        assert resumed.stages[1].repair_count == 0
    finally:
        connection.close()


def test_resume_rejects_changed_input_scope_and_checkpoint_chain(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    failing_runner = StageRunner(connection, _RetryableFailureProvider(), clock=_clock)
    created = failing_runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    original_input = _stage_input(case_id, AgentStageName.EXTRACT)
    with pytest.raises(AgentProviderFailureError):
        asyncio.run(failing_runner.execute_stage(_stage_command(created.version, original_input)))
    failed = failing_runner.get_run(case_id, "run-1")
    changed_input = _stage_input(
        case_id,
        AgentStageName.EXTRACT,
        marker="changed-input-must-not-resume",
    )
    try:
        with pytest.raises(AgentSequenceError):
            asyncio.run(
                StageRunner(connection, _ValidProvider(), clock=_clock).execute_stage(
                    _stage_command(
                        failed.version,
                        changed_input,
                        stage_version=failed.stages[0].version,
                    )
                )
            )
        assert failing_runner.get_run(case_id, "run-1") == failed
    finally:
        connection.close()

    connection, case_id = _database(tmp_path / "chain.sqlite3")
    runner = StageRunner(connection, _ValidProvider(), clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    checkpointed = asyncio.run(
        runner.execute_stage(
            _stage_command(created.version, _stage_input(case_id, AgentStageName.EXTRACT))
        )
    )
    try:
        with pytest.raises(AgentSequenceError):
            asyncio.run(
                runner.execute_stage(
                    _stage_command(
                        checkpointed.version,
                        _stage_input(
                            case_id,
                            AgentStageName.NORMALIZE,
                            ("0" * 64,),
                        ),
                    )
                )
            )
        assert runner.get_run(case_id, "run-1") == checkpointed
    finally:
        connection.close()


def test_two_competing_commands_allow_at_most_one_provider_call(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection, case_id = _database(database_path)
    created = StageRunner(connection, _ValidProvider(), clock=_clock).create_run(
        CreateAgentRunCommand(run_id="run-1", case_id=case_id)
    )
    connection.close()
    stage_input = _stage_input(case_id, AgentStageName.EXTRACT)

    def execute() -> str:
        worker_connection = connect_database(database_path)
        try:
            runner = StageRunner(worker_connection, _ValidProvider(), clock=_clock)
            asyncio.run(runner.execute_stage(_stage_command(created.version, stage_input)))
            return "completed"
        except AgentVersionConflictError:
            return "conflict"
        finally:
            worker_connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _: execute(), range(2)))

    assert sorted(outcomes) == ["completed", "conflict"]
    connection = connect_database(database_path)
    try:
        durable = StageRunner(connection, _ValidProvider(), clock=_clock).get_run(case_id, "run-1")
        assert durable.usage.calls == 1
        assert len(durable.stages) == 1
        assert durable.stages[0].status is AgentStageStatus.CHECKPOINTED
    finally:
        connection.close()


def test_checkpoint_tampering_fails_closed_without_exposing_body(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    runner = StageRunner(connection, _ValidProvider(), clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    run = asyncio.run(
        runner.execute_stage(
            _stage_command(created.version, _stage_input(case_id, AgentStageName.EXTRACT))
        )
    )
    assert run.stages[0].checkpoint is not None
    connection.execute(
        "UPDATE agent_stage_run SET checkpoint_json = ? WHERE run_id = ?",
        ('{"tampered":"private body"}', "run-1"),
    )
    try:
        with pytest.raises(AgentIntegrityError) as caught:
            runner.get_run(case_id, "run-1")
        message = str(caught.value)
        assert "private body" not in message
        assert "UPDATE" not in message
        assert str(tmp_path) not in message
    finally:
        connection.close()


def test_corrupt_checkpoint_chain_rolls_back_reservation_transaction(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    runner = StageRunner(connection, _ValidProvider(), clock=_clock)
    created = runner.create_run(CreateAgentRunCommand(run_id="run-1", case_id=case_id))
    checkpointed = asyncio.run(
        runner.execute_stage(
            _stage_command(created.version, _stage_input(case_id, AgentStageName.EXTRACT))
        )
    )
    checkpoint = checkpointed.stages[0].checkpoint
    assert checkpoint is not None
    connection.execute(
        "UPDATE agent_stage_run SET stage = 'normalize' WHERE run_id = ?",
        ("run-1",),
    )
    try:
        with pytest.raises(AgentIntegrityError):
            asyncio.run(
                runner.execute_stage(
                    _stage_command(
                        checkpointed.version,
                        _stage_input(
                            case_id,
                            AgentStageName.NORMALIZE,
                            (checkpoint.payload_sha256,),
                        ),
                    )
                )
            )
        assert connection.in_transaction is False
        row = connection.execute(
            "SELECT status, used_calls, reserved_total_tokens FROM agent_run WHERE id = ?",
            ("run-1",),
        ).fetchone()
        assert tuple(row) == ("checkpointed", 1, 0)
    finally:
        connection.close()
