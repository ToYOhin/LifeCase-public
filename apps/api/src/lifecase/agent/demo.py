"""P1-01 deterministic, Case-scoped Portfolio Agent Demo composition."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import cast

from lifecase.agent.extraction import (
    WARRANTY_EXTRACTION_SCHEMA_VERSION,
    ExtractionEvidenceSegment,
    WarrantyExtractionField,
    warranty_extraction_stage_input,
)
from lifecase.agent.models import (
    LIVE_TWO_STAGE_RUN_PREFIX,
    AgentBudget,
    AgentRunRecord,
    AgentRunStatus,
    AgentStageName,
    AgentStageRecord,
    CallReservation,
    CancelAgentRunCommand,
    CreateAgentRunCommand,
    ExecuteStageCommand,
    RecoverAgentRunCommand,
    StageBudget,
    StageInput,
    stage_input_scope_sha256,
    stage_order_for_run,
)
from lifecase.agent.question_binding import QUESTION_RUN_PREFIX, validate_question_binding
from lifecase.agent.question_context import binding_status, require_current_binding
from lifecase.agent.rag import (
    EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION,
    EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
    QUESTION_RAG_REGISTRY,
    TWO_STAGE_RAG_REGISTRY,
    EvidenceBackedRagCheckpointHandler,
    evidence_backed_rag_stage_input,
)
from lifecase.agent.registry import StageRegistry
from lifecase.agent.service import StageRunner, stage_call_scope_sha256
from lifecase.cases import CaseNotFoundError, CaseService
from lifecase.claims import ClaimCreatedBy, ClaimService
from lifecase.evidence.context import (
    EvidenceContextBudget,
    EvidenceContextRequest,
    EvidencePolicy,
    EvidenceTrustLabel,
)
from lifecase.providers import (
    FakeModelProvider,
    FakeProviderScript,
    ModelProvider,
    ModelProviderConfig,
    ModelProviderKind,
    ProviderUsage,
    canonical_json_object,
)
from lifecase.tools import (
    EVIDENCE_CONTEXT_TOOL_NAME,
    EvidenceContextReadTool,
    EvidenceContextToolRequest,
    ReadOnlyToolBudget,
)

PORTFOLIO_AGENT_DEMO_VERSION = "portfolio_agent_demo.v1"
_GENERIC_CHECKPOINT_VERSION = "portfolio_agent_demo_stage.v1"
_RESERVATION = CallReservation(input_tokens=64, output_tokens=64, cost_minor=0)


class PortfolioAgentDemoError(RuntimeError):
    """Safe application-level failure for the deterministic demo."""


class PortfolioAgentDemoDisabledError(PortfolioAgentDemoError):
    """Raised when explicit demo opt-in is absent."""


class PortfolioAgentRunExistsError(PortfolioAgentDemoError):
    """Raised when the Case already owns its single durable run."""


class PortfolioAgentRunMissingError(PortfolioAgentDemoError):
    """Raised when the Case has no durable run."""


class PortfolioAgentRunNotExecutableError(PortfolioAgentDemoError):
    """Raised when lifecycle state forbids execution or retry."""


class PortfolioAgentRunChangedError(PortfolioAgentDemoError):
    """Raised when a command no longer targets the displayed run."""


class PortfolioAgentRunModeError(PortfolioAgentDemoError):
    """Raised when a route attempts to operate on another product run mode."""


class PortfolioAgentCaseUnsupportedError(PortfolioAgentDemoError):
    """Raised when the only reviewed extraction schema does not fit the Case."""


@dataclass(frozen=True, slots=True)
class PortfolioAgentDemoState:
    """Safe user-visible state reconstructed from SQLite authority."""

    enabled: bool
    run: AgentRunRecord | None
    claims: tuple[dict[str, object], ...]
    answer: dict[str, object] | None
    tool_calls: tuple[dict[str, object], ...]
    history: tuple[dict[str, object], ...]
    analysis: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class PortfolioAgentEvidenceScope:
    """Exact extraction and validate Context Segments for one Case."""

    extraction: tuple[ExtractionEvidenceSegment, ...]
    validation: tuple[ExtractionEvidenceSegment, ...]

    @property
    def unique_segments(self) -> tuple[ExtractionEvidenceSegment, ...]:
        by_id: dict[str, ExtractionEvidenceSegment] = {}
        for segment in (*self.extraction, *self.validation):
            existing = by_id.get(segment.segment_id)
            if existing is not None and existing != segment:
                raise ValueError("Agent evidence scope is inconsistent")
            by_id.setdefault(segment.segment_id, segment)
        return tuple(by_id.values())


class PortfolioAgentDemoService:
    """Compose existing runner, extraction, RAG, and Claim authority."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        enabled: bool,
        evaluation_provider: ModelProvider | None = None,
        call_reservation: CallReservation = _RESERVATION,
        stage_budget: StageBudget | None = None,
        stage_registry: StageRegistry = EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
        agent_budget: AgentBudget | None = None,
        run_id_prefix: str = "portfolio-demo-",
        allow_new_runs: bool = False,
        enable_retrieval_fallback: bool = False,
    ) -> None:
        self._connection = connection
        self._enabled = enabled
        self._evaluation_provider = evaluation_provider
        self._call_reservation = call_reservation
        self._stage_budget = stage_budget or StageBudget()
        self._stage_registry = stage_registry
        self._agent_budget = agent_budget or AgentBudget()
        self._run_id_prefix = run_id_prefix
        self._allow_new_runs = allow_new_runs
        self._enable_retrieval_fallback = enable_retrieval_fallback

    def get_state(self, case_id: str) -> PortfolioAgentDemoState:
        """Read the latest matching Case run without exposing checkpoint bodies."""

        CaseService(self._connection).get_case(case_id)
        rows = self._connection.execute(
            "SELECT id FROM agent_run WHERE case_id = ? ORDER BY created_at DESC, rowid DESC",
            (case_id,),
        ).fetchall()
        matching = [row for row in rows if str(row["id"]).startswith(self._run_id_prefix)]
        if rows and not matching:
            raise PortfolioAgentRunModeError("Case Agent run belongs to another mode")
        history_runs = tuple(
            self._reader(str(row["id"])).get_run(case_id, str(row["id"])) for row in matching
        )
        run = None if not history_runs else history_runs[0]
        return PortfolioAgentDemoState(
            enabled=self._enabled,
            run=run,
            claims=self._agent_claims(case_id, None if run is None else run.id),
            answer=self._answer(run),
            tool_calls=self._tool_calls(run),
            history=tuple(
                self._history_item(item, len(history_runs) - index)
                for index, item in enumerate(history_runs)
            ),
            analysis=self._analysis(run),
        )

    def create(
        self,
        case_id: str,
        *,
        run_id: str | None = None,
        input_binding_json: str | None = None,
    ) -> PortfolioAgentDemoState:
        """Create, but do not yet execute, the cancellable durable run."""

        self._require_enabled()
        self._require_supported_case(case_id)
        existing = self.get_state(case_id)
        if existing.run is not None and not self._allow_new_runs:
            raise PortfolioAgentRunExistsError("Case already has an Agent run")
        if existing.run is not None and existing.run.status not in {
            AgentRunStatus.CANCELLED,
            AgentRunStatus.COMPLETED,
            AgentRunStatus.NEEDS_REVIEW,
        }:
            raise PortfolioAgentRunExistsError("Case has an unfinished Agent run")
        resolved_run_id = run_id or _run_id(case_id)
        if not resolved_run_id.startswith(self._run_id_prefix):
            raise ValueError("Agent run mode is invalid")
        self._reader(resolved_run_id).create_run(
            CreateAgentRunCommand(
                run_id=resolved_run_id,
                case_id=case_id,
                budget=self._agent_budget,
                allow_case_history=self._allow_new_runs,
                input_binding_json=input_binding_json,
                input_binding_sha256=(
                    None
                    if input_binding_json is None
                    else hashlib.sha256(input_binding_json.encode()).hexdigest()
                ),
            )
        )
        return self.get_state(case_id)

    async def execute(
        self,
        case_id: str,
        *,
        retry_only: bool = False,
        expected_run_id: str | None = None,
    ) -> PortfolioAgentDemoState:
        """Execute remaining fixed stages until completion or a fail-closed stop."""

        self._require_enabled()
        self._require_supported_case(case_id)
        state = self.get_state(case_id)
        run = state.run
        if run is None:
            raise PortfolioAgentRunMissingError("Agent run was not found")
        _require_expected_run(run, expected_run_id)
        if retry_only:
            if not _can_retry(run):
                raise PortfolioAgentRunNotExecutableError("Agent run is not retryable")
        elif run.status is AgentRunStatus.NEEDS_REVIEW:
            raise PortfolioAgentRunNotExecutableError("Agent run requires explicit retry")
        if run.status in {
            AgentRunStatus.RUNNING,
            AgentRunStatus.CANCELLED,
            AgentRunStatus.COMPLETED,
        }:
            raise PortfolioAgentRunNotExecutableError("Agent run is not executable")

        while run.status not in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.CANCELLED,
            AgentRunStatus.NEEDS_REVIEW,
        } or _can_retry(run):
            stage = _next_stage(run)
            stage_input = self._stage_input(case_id, run, stage)
            existing_stage = next((item for item in run.stages if item.stage is stage), None)
            call_number = 1 if existing_stage is None else existing_stage.usage.calls + 1
            provider = self._evaluation_provider
            if provider is None:
                script = FakeProviderScript(
                    request_scope_sha256=stage_call_scope_sha256(
                        run.id, stage, stage_input.input_scope_sha256, call_number
                    ),
                    output_json=self._fixed_output(stage_input),
                    usage=ProviderUsage(
                        input_tokens=10,
                        output_tokens=5,
                        total_tokens=15,
                        cost_minor_units=0,
                        cost_currency="CNY",
                    ),
                )
                provider = FakeModelProvider(
                    ModelProviderConfig(provider=ModelProviderKind.FAKE), (script,)
                )
            runner = StageRunner(
                self._connection,
                provider,
                registry=self._registry_for_run(run.id),
                checkpoint_handler=EvidenceBackedRagCheckpointHandler(),
            )
            run = await runner.execute_stage(
                ExecuteStageCommand(
                    run_id=run.id,
                    expected_run_version=run.version,
                    expected_stage_version=(
                        existing_stage.version if existing_stage is not None else None
                    ),
                    stage_input=stage_input,
                    stage_budget=(
                        existing_stage.budget if existing_stage is not None else self._stage_budget
                    ),
                    reservation=self._call_reservation,
                )
            )
            if run.status is AgentRunStatus.NEEDS_REVIEW:
                break
        return self.get_state(case_id)

    def cancel(
        self, case_id: str, *, expected_run_id: str | None = None
    ) -> PortfolioAgentDemoState:
        """Cancel a queued or otherwise unfinished run through StageRunner authority."""

        self._require_enabled()
        run = self._required_run(case_id)
        _require_expected_run(run, expected_run_id)
        self._reader(run.id).request_cancellation(
            CancelAgentRunCommand(
                run_id=run.id,
                case_id=case_id,
                expected_version=run.version,
            )
        )
        return self.get_state(case_id)

    def recover(
        self, case_id: str, *, expected_run_id: str | None = None
    ) -> PortfolioAgentDemoState:
        """Charge and stop an interrupted in-flight reservation for explicit retry."""

        self._require_enabled()
        run = self._required_run(case_id)
        _require_expected_run(run, expected_run_id)
        if run.status is not AgentRunStatus.RUNNING:
            raise PortfolioAgentRunNotExecutableError("Agent run does not need recovery")
        self._reader(run.id).recover_interrupted(
            RecoverAgentRunCommand(
                run_id=run.id,
                case_id=case_id,
                expected_version=run.version,
            )
        )
        return self.get_state(case_id)

    def _stage_input(
        self,
        case_id: str,
        run: AgentRunRecord,
        stage: AgentStageName,
    ) -> StageInput:
        prior = tuple(
            item.checkpoint.payload_sha256 for item in run.stages if item.checkpoint is not None
        )
        if stage is AgentStageName.EXTRACT:
            return warranty_extraction_stage_input(
                case_id,
                self.evidence(case_id),
                prior_checkpoint_hashes=prior,
            )
        if stage is AgentStageName.VALIDATE:
            if run.input_binding_json is not None:
                require_current_binding(self._connection, run.input_binding_json, case_id)
                binding = validate_question_binding(run.input_binding_json, case_id)
                basis = cast(dict[str, object], binding["basis"])
                question = str(binding["question"])
                return evidence_backed_rag_stage_input(
                    self._connection,
                    _evidence_tool_request(
                        self._connection,
                        case_id,
                        question,
                        enable_fallback=self._enable_retrieval_fallback,
                        retrieval_query=str(binding["retrieval_query"]),
                    ),
                    prior_checkpoint_hashes=prior,
                    question=question,
                    current_facts=cast(dict[str, object], basis["facts"]),
                )
            case = CaseService(self._connection).get_case(case_id)
            return evidence_backed_rag_stage_input(
                self._connection,
                _evidence_tool_request(
                    self._connection,
                    case_id,
                    case.goal,
                    enable_fallback=self._enable_retrieval_fallback,
                ),
                prior_checkpoint_hashes=prior,
                two_stage=run.id.startswith(LIVE_TWO_STAGE_RUN_PREFIX),
            )
        definition = self._registry_for_run(run.id).get(stage)
        payload_json = canonical_json_object(
            {
                "case_id": case_id,
                "prior_checkpoint_hashes": list(prior),
                "stage_payload": {
                    "demo_version": PORTFOLIO_AGENT_DEMO_VERSION,
                    "read_only": True,
                },
            },
            maximum_bytes=100_000,
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

    def _fixed_output(self, stage_input: StageInput) -> str:
        definition = EVIDENCE_BACKED_RAG_STAGE_REGISTRY.get(stage_input.stage)
        if stage_input.stage is AgentStageName.EXTRACT:
            checkpoint = _extraction_checkpoint(self.evidence(stage_input.case_id))
        elif stage_input.stage is AgentStageName.VALIDATE:
            checkpoint = _answer_checkpoint(stage_input.payload_json)
        else:
            checkpoint = {
                "schema_version": _GENERIC_CHECKPOINT_VERSION,
                "demo_version": PORTFOLIO_AGENT_DEMO_VERSION,
                "summary": f"{stage_input.stage.value} completed without external action",
                "read_only": True,
            }
        return canonical_json_object(
            {
                "stage": stage_input.stage.value,
                "stage_version": definition.stage_version,
                "checkpoint_schema_version": definition.checkpoint_schema_version,
                "checkpoint": checkpoint,
            },
            maximum_bytes=65_536,
        )

    def evidence(self, case_id: str) -> tuple[ExtractionEvidenceSegment, ...]:
        """Read the exact bounded Segment scope used by extraction."""
        rows = self._connection.execute(
            """
            SELECT s.case_id, s.id AS source_id, ss.id AS segment_id,
                   ss.sequence, ss.page, ss.text, ss.text_sha256
            FROM source AS s
            JOIN source_segment AS ss ON ss.source_id = s.id
            WHERE s.case_id = ?
            ORDER BY s.imported_at, s.id, ss.sequence, ss.id
            LIMIT 32
            """,
            (case_id,),
        ).fetchall()
        return tuple(
            ExtractionEvidenceSegment(
                case_id=str(row["case_id"]),
                source_id=str(row["source_id"]),
                segment_id=str(row["segment_id"]),
                sequence=int(row["sequence"]),
                page=None if row["page"] is None else int(row["page"]),
                text=str(row["text"]),
                text_sha256=str(row["text_sha256"]),
            )
            for row in rows
        )

    def evidence_scope(
        self,
        case_id: str,
        *,
        question: str | None = None,
        retrieval_query: str | None = None,
    ) -> PortfolioAgentEvidenceScope:
        """Preview every unique Segment sent by extraction or validate RAG."""

        case = CaseService(self._connection).get_case(case_id)
        definition = EVIDENCE_BACKED_RAG_STAGE_REGISTRY.get(AgentStageName.VALIDATE)
        result = EvidenceContextReadTool(
            self._connection,
            allowed_tools=definition.allowed_tools,
        ).execute(
            _evidence_tool_request(
                self._connection,
                case_id,
                case.goal if question is None else question,
                enable_fallback=self._enable_retrieval_fallback,
                retrieval_query=retrieval_query,
            )
        )
        validation = tuple(
            ExtractionEvidenceSegment(
                case_id=item.citation.case_id,
                source_id=item.citation.source_id,
                segment_id=item.citation.segment_id,
                sequence=item.citation.sequence,
                page=item.citation.page,
                text=item.text,
                text_sha256=item.citation.text_sha256,
            )
            for item in result.context.items
        )
        return PortfolioAgentEvidenceScope(
            extraction=self.evidence(case_id) if question is None else (),
            validation=validation,
        )

    def _registry_for_run(self, run_id: str) -> StageRegistry:
        if run_id.startswith(QUESTION_RUN_PREFIX):
            return QUESTION_RAG_REGISTRY
        if run_id.startswith(LIVE_TWO_STAGE_RUN_PREFIX):
            return TWO_STAGE_RAG_REGISTRY
        return self._stage_registry

    def _reader(self, run_id: str) -> StageRunner:
        return StageRunner(
            self._connection,
            FakeModelProvider(ModelProviderConfig(provider=ModelProviderKind.FAKE), ()),
            registry=self._registry_for_run(run_id),
            checkpoint_handler=EvidenceBackedRagCheckpointHandler(),
        )

    def _required_run(self, case_id: str) -> AgentRunRecord:
        state = self.get_state(case_id)
        if state.run is None:
            raise PortfolioAgentRunMissingError("Agent run was not found")
        return state.run

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise PortfolioAgentDemoDisabledError("Portfolio Agent Demo is disabled")

    def _require_supported_case(self, case_id: str) -> None:
        if CaseService(self._connection).get_case(case_id).type != "warranty":
            raise PortfolioAgentCaseUnsupportedError(
                "Portfolio Agent Demo supports warranty Cases only"
            )

    def _agent_claims(self, case_id: str, run_id: str | None) -> tuple[dict[str, object], ...]:
        try:
            claims = ClaimService(self._connection).list_case_claims(case_id)
        except CaseNotFoundError:
            return ()
        if run_id is None:
            return ()
        claim_ids = {
            str(row["claim_id"])
            for row in self._connection.execute(
                """
                SELECT claim_id FROM claim_event
                WHERE event_type = 'created' AND actor = ? AND new_version = 1
                """,
                (run_id,),
            ).fetchall()
        }
        return tuple(
            {
                "id": claim.id,
                "field": claim.field,
                "value": claim.value.to_python(),
                "status": claim.status.value,
                "confidence": claim.confidence,
                "created_by": claim.created_by.value,
                "version": claim.version,
            }
            for claim in claims
            if claim.created_by is ClaimCreatedBy.AGENT and claim.id in claim_ids
        )

    def _history_item(self, run: AgentRunRecord, version: int) -> dict[str, object]:
        provider, model_id = _run_provider_identity(run)
        answer = self._answer(run)
        analysis = self._analysis(run)
        return {
            "version": version,
            "id": run.id,
            "stage_order": [stage.value for stage in stage_order_for_run(run.id)],
            "status": run.status.value,
            "provider": provider,
            "model_id": model_id,
            "answer_status": None if answer is None else answer["status"],
            "calls": run.usage.calls,
            "total_tokens": run.usage.total_tokens,
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "question": None if analysis is None else analysis["question"],
            "basis_status": "legacy" if analysis is None else analysis["basis_status"],
            "answer": answer,
        }

    def _analysis(self, run: AgentRunRecord | None) -> dict[str, object] | None:
        if run is None or run.input_binding_json is None:
            return None
        binding = validate_question_binding(run.input_binding_json, run.case_id)
        facts = cast(dict[str, object], cast(dict[str, object], binding["basis"])["facts"])
        return {
            "question": binding["question"],
            "basis_sha256": binding["basis_sha256"],
            "basis_status": binding_status(self._connection, run.input_binding_json, run.case_id),
            "facts": facts["items"],
        }

    def _answer(self, run: AgentRunRecord | None) -> dict[str, object] | None:
        stage = _completed_stage(run, AgentStageName.VALIDATE)
        if stage is None or stage.checkpoint is None:
            return None
        payload = json.loads(stage.checkpoint.payload_json)
        citations = [
            reference
            for statement in payload["statements"]
            for reference in statement["source_refs"]
        ]
        fact_references: list[dict[str, object]] = []
        if run is not None and run.input_binding_json is not None:
            binding = validate_question_binding(run.input_binding_json, run.case_id)
            facts = cast(dict[str, object], cast(dict[str, object], binding["basis"])["facts"])
            references = {
                (reference["claim_id"], reference["version"], reference["value_sha256"])
                for statement in payload["statements"]
                for reference in statement.get("fact_refs", [])
            }
            fact_references = [
                item
                for item in cast(list[dict[str, object]], facts["items"])
                if (item["claim_id"], item["version"], item["value_sha256"]) in references
            ]
        return {
            "status": payload["status"],
            "statements": [item["text"] for item in payload["statements"]],
            "citations": citations,
            "uncertainty_reason": payload["uncertainty_reason"],
            "fact_references": fact_references,
        }

    def _tool_calls(self, run: AgentRunRecord | None) -> tuple[dict[str, object], ...]:
        stage = _completed_stage(run, AgentStageName.VALIDATE)
        if stage is None or stage.checkpoint is None:
            return ()
        payload = json.loads(stage.checkpoint.payload_json)
        return (
            {
                "name": payload["tool"]["name"],
                "risk": "A",
                "call_count": payload["tool"]["call_count"],
                "result": "match" if payload["status"] == "answered" else "no_match",
            },
        )


def _run_id(case_id: str) -> str:
    return f"portfolio-demo-{hashlib.sha256(case_id.encode()).hexdigest()[:32]}"


def _require_expected_run(run: AgentRunRecord, expected_run_id: str | None) -> None:
    if (expected_run_id is not None and expected_run_id != run.id) or (
        run.id.startswith(QUESTION_RUN_PREFIX) and expected_run_id is None
    ):
        raise PortfolioAgentRunChangedError("The displayed Agent run has changed")


_RETRIEVAL_TASK_STOPWORDS = frozenset(
    {
        "and",
        "check",
        "confirm",
        "determine",
        "facts",
        "for",
        "organize",
        "prepare",
        "the",
        "to",
        "validate",
        "verify",
        "whether",
    }
)

_WARRANTY_ALIAS_GROUPS = (
    ("保修", "warranty", ("warranty", "保修期")),
    ("维修", "repair", ("repair", "维修单")),
    ("购买", "purchase", ("purchase", "购买日")),
    ("故障", "fault", ("fault", "故障现")),
    ("报修", "service", ("service", "报修回")),
    ("合成", "synthetic", ("synthetic", "合成演")),
)


def _safe_retrieval_query(goal: str) -> str:
    """Treat the Case goal as text, never as caller-controlled FTS syntax."""

    normalized = " ".join(goal.split())
    identifier_terms: list[str] = []
    ascii_terms: list[str] = []
    cjk_terms: list[str] = []
    for token in re.findall(
        r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*|[\u3400-\u9fff]+",
        normalized,
    ):
        if re.fullmatch(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*", token):
            lowered = token.lower()
            if lowered in _RETRIEVAL_TASK_STOPWORDS:
                continue
            if (
                len(lowered) >= 3
                and any(character.isalpha() for character in lowered)
                and any(character.isdigit() for character in lowered)
            ):
                identifier_terms.append(lowered)
            elif len(lowered) >= 3:
                ascii_terms.append(lowered)
            continue
        if len(token) <= 4:
            cjk_terms.append(token)
        else:
            cjk_terms.extend(token[index : index + 3] for index in range(len(token) - 2))
    terms = [*identifier_terms, *cjk_terms, *ascii_terms]
    return _join_retrieval_terms(terms or ["case", "goal"], operator="OR")


def _warranty_alias_terms(goal: str) -> tuple[str, ...]:
    normalized = " ".join(goal.split())
    english_tokens = frozenset(re.findall(r"[a-z]+", normalized.casefold()))
    return tuple(
        alias
        for chinese_marker, english_marker, aliases in _WARRANTY_ALIAS_GROUPS
        if chinese_marker in normalized or english_marker in english_tokens
        for alias in aliases
    )


def _join_retrieval_terms(terms: list[str], *, operator: str) -> str:
    unique_terms = tuple(dict.fromkeys(terms))
    if not unique_terms:
        unique_terms = ("case", "goal")
    query_parts: list[str] = []
    for term in unique_terms:
        quoted_term = f'"{term}"'
        # An oversized identifier must not suppress later usable goal terms.
        if len(quoted_term) > 256:
            continue
        candidate = f" {operator} ".join((*query_parts, quoted_term))
        if len(candidate) > 256:
            break
        query_parts.append(quoted_term)
    return f" {operator} ".join(query_parts or ['"case"', '"goal"'])


def _run_provider_identity(run: AgentRunRecord) -> tuple[str | None, str | None]:
    identities = [
        (stage.provider, stage.model_id)
        for stage in run.stages
        if stage.provider is not None and stage.model_id is not None
    ]
    return (None, None) if not identities else identities[-1]


def _evidence_tool_request(
    connection: sqlite3.Connection,
    case_id: str,
    goal: str,
    *,
    enable_fallback: bool,
    retrieval_query: str | None = None,
) -> EvidenceContextToolRequest:
    return EvidenceContextToolRequest(
        tool_name=EVIDENCE_CONTEXT_TOOL_NAME,
        context_request=EvidenceContextRequest(
            case_id=case_id,
            query=retrieval_query
            or (
                _retrieval_query(connection, case_id, goal)
                if enable_fallback
                else _safe_retrieval_query(goal)
            ),
            retrieval_limit=5,
            adjacent_before=1,
            adjacent_after=1,
            budget=EvidenceContextBudget(
                max_text_bytes=20_000,
                max_item_text_bytes=8_000,
            ),
            policy=EvidencePolicy(frozenset({EvidenceTrustLabel.UNTRUSTED})),
        ),
        budget=ReadOnlyToolBudget(),
    )


def _retrieval_query(connection: sqlite3.Connection, case_id: str, goal: str) -> str:
    goal_query = _safe_retrieval_query(goal)
    matched = connection.execute(
        """
        SELECT 1
        FROM source_segment_fts
        JOIN source_segment AS segment
          ON segment.rowid = source_segment_fts.rowid
         AND segment.id = source_segment_fts.segment_id
         AND segment.source_id = source_segment_fts.source_id
        JOIN source ON source.id = segment.source_id
        WHERE source_segment_fts MATCH ? AND source.case_id = ?
        LIMIT 1
        """,
        (goal_query, case_id),
    ).fetchone()
    if matched is not None:
        return goal_query

    aliases = _warranty_alias_terms(goal)
    if not aliases:
        return goal_query
    return _join_retrieval_terms(list(aliases), operator="OR")


def _next_stage(run: AgentRunRecord) -> AgentStageName:
    if run.status is AgentRunStatus.NEEDS_REVIEW and run.current_stage is not None:
        return run.current_stage
    order = stage_order_for_run(run.id)
    completed = 0 if run.last_completed_stage is None else order.index(run.last_completed_stage) + 1
    if completed >= len(order):
        raise PortfolioAgentRunNotExecutableError("Agent run has no remaining stage")
    return order[completed]


def _can_retry(run: AgentRunRecord) -> bool:
    if run.status is not AgentRunStatus.NEEDS_REVIEW or not run.stages:
        return False
    stage = run.stages[-1]
    return stage.retryable is True and stage.usage.calls < stage.budget.max_calls


def _completed_stage(
    run: AgentRunRecord | None,
    stage: AgentStageName,
) -> AgentStageRecord | None:
    if run is None:
        return None
    return next((item for item in run.stages if item.stage is stage), None)


def _extraction_checkpoint(
    evidence: tuple[ExtractionEvidenceSegment, ...],
) -> dict[str, object]:
    patterns: dict[WarrantyExtractionField, tuple[re.Pattern[str], str, str]] = {
        WarrantyExtractionField.MERCHANT_OR_MANUFACTURER: (
            re.compile(r"\bMerchant\s+([^.\r\n]{1,160})", re.IGNORECASE),
            "text",
            "extracted",
        ),
        WarrantyExtractionField.PRODUCT_NAME: (
            re.compile(r"\bProduct\s+([^.\r\n]{1,160})", re.IGNORECASE),
            "text",
            "extracted",
        ),
        WarrantyExtractionField.PURCHASE_DATE: (
            re.compile(r"\bPurchase date\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
            "text",
            "extracted",
        ),
        WarrantyExtractionField.ORDER_NUMBER: (
            re.compile(r"\bOrder\s+([A-Za-z0-9._-]{1,100})", re.IGNORECASE),
            "text",
            "extracted",
        ),
        WarrantyExtractionField.AMOUNT: (
            re.compile(r"\b(CNY\s+\d{1,9}(?:\.\d{1,2})?)", re.IGNORECASE),
            "money",
            "extracted",
        ),
        WarrantyExtractionField.SERIAL_NUMBER: (
            re.compile(r"\bSerial prefix\s+([A-Za-z0-9._-]{1,100})", re.IGNORECASE),
            "text",
            "inferred",
        ),
    }
    facts: list[dict[str, object]] = []
    for field in WarrantyExtractionField:
        found = _find_fact(evidence, patterns[field])
        if found is None:
            facts.append(
                {
                    "field": field.value,
                    "origin": "unknown",
                    "value": None,
                    "confidence": None,
                    "uncertainty_reason": "The deterministic demo found no reviewed marker.",
                    "source_refs": [],
                }
            )
            continue
        segment, match, kind, origin = found
        quote = match.group(0)
        raw_value = match.group(1).strip()
        value: object = raw_value
        if kind == "money":
            try:
                amount = Decimal(raw_value.split(maxsplit=1)[1])
                value = {"amount_minor": int(amount * 100), "currency": "CNY"}
            except (InvalidOperation, IndexError):
                value = raw_value
        facts.append(
            {
                "field": field.value,
                "origin": origin,
                "value": value,
                "confidence": 1.0,
                "uncertainty_reason": (
                    None
                    if origin == "extracted"
                    else "The cited material identifies only a serial prefix."
                ),
                "source_refs": [
                    {
                        "source_id": segment.source_id,
                        "segment_id": segment.segment_id,
                        "segment_text_sha256": segment.text_sha256,
                        "quote_start": match.start(),
                        "quote_end": match.end(),
                        "quote_hash": hashlib.sha256(quote.encode()).hexdigest(),
                    }
                ],
            }
        )
    return {"schema_version": WARRANTY_EXTRACTION_SCHEMA_VERSION, "facts": facts}


def _find_fact(
    evidence: tuple[ExtractionEvidenceSegment, ...],
    pattern: tuple[re.Pattern[str], str, str],
) -> tuple[ExtractionEvidenceSegment, re.Match[str], str, str] | None:
    expression, kind, origin = pattern
    for segment in evidence:
        match = expression.search(segment.text)
        if match is not None:
            return segment, match, kind, origin
    return None


def _answer_checkpoint(payload_json: str) -> dict[str, object]:
    stage_payload = json.loads(payload_json)["stage_payload"]
    tool_result = stage_payload["tool_result"]
    items = tool_result["context"]["items"]
    tool = {
        "name": EVIDENCE_CONTEXT_TOOL_NAME,
        "call_count": 1,
        "context_sha256": tool_result["context_sha256"],
    }
    if not items:
        return {
            "schema_version": EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION,
            "status": "unknown",
            "statements": [],
            "uncertainty_reason": "No authoritative Case evidence matched the Case goal.",
            "tool": tool,
        }
    item = items[0]
    text = str(item["text"])
    return {
        "schema_version": EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION,
        "status": "answered",
        "statements": [
            {
                "text": "The Case goal has support in the authoritative local evidence.",
                "source_refs": [
                    {
                        **item["citation"],
                        "quote_start": 0,
                        "quote_end": len(text),
                        "quote_hash": hashlib.sha256(text.encode()).hexdigest(),
                    }
                ],
            }
        ],
        "uncertainty_reason": None,
        "tool": tool,
    }
