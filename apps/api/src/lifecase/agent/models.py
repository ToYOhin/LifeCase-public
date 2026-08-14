"""Immutable contracts for bounded, resumable Agent stages."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum

from lifecase.agent.errors import AgentValidationError
from lifecase.providers.models import canonical_json_object, request_scope_sha256

MAX_CHECKPOINT_BYTES = 65_536
MAX_STAGE_INPUT_BYTES = 100_000
MAX_AGENT_CALLS = 14
MAX_STAGE_CALLS = 2
MAX_CASE_COST_MINOR_CNY = 100

_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}\Z")
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class AgentStageName(StrEnum):
    """Fixed M3-02 stage order."""

    EXTRACT = "extract"
    NORMALIZE = "normalize"
    LINK_CLAIMS = "link_claims"
    CLASSIFY_CASE = "classify_case"
    CHECK_GAPS = "check_gaps"
    PROPOSE_ACTIONS = "propose_actions"
    VALIDATE = "validate"


STAGE_ORDER: tuple[AgentStageName, ...] = tuple(AgentStageName)


class AgentRunStatus(StrEnum):
    """Durable lifecycle of one Case-scoped pipeline run."""

    QUEUED = "queued"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    NEEDS_REVIEW = "needs_review"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class AgentStageStatus(StrEnum):
    """Durable lifecycle of one stage attempt series."""

    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    NEEDS_REVIEW = "needs_review"
    CANCELLED = "cancelled"


class StageStopCondition(StrEnum):
    """Explicit reasons that forbid further automatic orchestration."""

    INVALID_SCHEMA_AFTER_REPAIR = "invalid_schema_after_repair"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_FAILURE = "provider_failure"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"
    PERSISTENCE_FAILURE = "persistence_failure"


@dataclass(frozen=True, slots=True)
class AgentBudget:
    """Whole-Case ceilings, including the default CNY 1 hard cap."""

    max_calls: int = MAX_AGENT_CALLS
    max_input_tokens: int = 28_000
    max_output_tokens: int = 14_000
    max_cost_minor: int = MAX_CASE_COST_MINOR_CNY
    cost_currency: str = "CNY"

    def __post_init__(self) -> None:
        _bounded_int(self.max_calls, 1, MAX_AGENT_CALLS, "Agent budget")
        _bounded_int(self.max_input_tokens, 1, 1_000_000, "Agent budget")
        _bounded_int(self.max_output_tokens, 1, 1_000_000, "Agent budget")
        _bounded_int(self.max_cost_minor, 0, MAX_CASE_COST_MINOR_CNY, "Agent budget")
        if self.cost_currency != "CNY":
            raise AgentValidationError("Agent budget currency is invalid")

    @property
    def max_total_tokens(self) -> int:
        return self.max_input_tokens + self.max_output_tokens


@dataclass(frozen=True, slots=True)
class StageBudget:
    """Per-stage ceilings; at most one initial call and one repair."""

    max_calls: int = MAX_STAGE_CALLS
    max_input_tokens: int = 4_000
    max_output_tokens: int = 2_000
    max_cost_minor: int = 15
    cost_currency: str = "CNY"

    def __post_init__(self) -> None:
        _bounded_int(self.max_calls, 1, MAX_STAGE_CALLS, "Stage budget")
        _bounded_int(self.max_input_tokens, 1, 100_000, "Stage budget")
        _bounded_int(self.max_output_tokens, 1, 100_000, "Stage budget")
        _bounded_int(self.max_cost_minor, 0, MAX_CASE_COST_MINOR_CNY, "Stage budget")
        if self.cost_currency != "CNY":
            raise AgentValidationError("Stage budget currency is invalid")

    @property
    def max_total_tokens(self) -> int:
        return self.max_input_tokens + self.max_output_tokens


@dataclass(frozen=True, slots=True)
class CallReservation:
    """Required pre-call charge ceiling when provider cost may be absent."""

    input_tokens: int
    output_tokens: int
    cost_minor: int
    cost_currency: str = "CNY"

    def __post_init__(self) -> None:
        _bounded_int(self.input_tokens, 1, 100_000, "Call reservation")
        _bounded_int(self.output_tokens, 1, 100_000, "Call reservation")
        _bounded_int(self.cost_minor, 0, MAX_CASE_COST_MINOR_CNY, "Call reservation")
        if self.cost_currency != "CNY":
            raise AgentValidationError("Call reservation currency is invalid")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class CreateAgentRunCommand:
    """Create one durable Agent pipeline, with history enabled only explicitly."""

    run_id: str
    case_id: str
    budget: AgentBudget = field(default_factory=AgentBudget)
    allow_case_history: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "Run ID"))
        object.__setattr__(self, "case_id", _identifier(self.case_id, "Case ID"))
        if not isinstance(self.budget, AgentBudget):
            raise AgentValidationError("Agent budget is invalid")
        if not isinstance(self.allow_case_history, bool):
            raise AgentValidationError("Agent run history setting is invalid")


@dataclass(frozen=True, slots=True)
class StageInput:
    """Bounded stage input; only its opaque scope hash is persisted."""

    case_id: str
    stage: AgentStageName
    stage_version: str
    input_schema_version: str
    input_scope_sha256: str
    payload_json: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _identifier(self.case_id, "Case ID"))
        if not isinstance(self.stage, AgentStageName):
            raise AgentValidationError("Agent stage is invalid")
        object.__setattr__(self, "stage_version", _version(self.stage_version))
        object.__setattr__(self, "input_schema_version", _version(self.input_schema_version))
        try:
            parsed = json.loads(self.payload_json)
            payload_json = canonical_json_object(parsed, maximum_bytes=MAX_STAGE_INPUT_BYTES)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise AgentValidationError("Stage input is invalid") from error
        _validate_stage_input_envelope(parsed, self.case_id)
        object.__setattr__(self, "payload_json", payload_json)
        expected_scope = stage_input_scope_sha256(
            self.case_id,
            self.stage,
            self.stage_version,
            self.input_schema_version,
            payload_json,
        )
        if self.input_scope_sha256 != expected_scope:
            raise AgentValidationError("Stage input scope is invalid")


@dataclass(frozen=True, slots=True)
class ExecuteStageCommand:
    """Optimistically execute or explicitly resume one fixed stage."""

    run_id: str
    expected_run_version: int
    expected_stage_version: int | None
    stage_input: StageInput
    stage_budget: StageBudget
    reservation: CallReservation

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "Run ID"))
        _bounded_int(self.expected_run_version, 1, 2_147_483_647, "Expected version")
        if self.expected_stage_version is not None:
            _bounded_int(
                self.expected_stage_version,
                1,
                2_147_483_647,
                "Expected version",
            )
        if not isinstance(self.stage_input, StageInput):
            raise AgentValidationError("Stage input is invalid")
        if not isinstance(self.stage_budget, StageBudget):
            raise AgentValidationError("Stage budget is invalid")
        if not isinstance(self.reservation, CallReservation):
            raise AgentValidationError("Call reservation is invalid")


@dataclass(frozen=True, slots=True)
class CancelAgentRunCommand:
    """Optimistically request cancellation of one Case-scoped run."""

    run_id: str
    case_id: str
    expected_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "Run ID"))
        object.__setattr__(self, "case_id", _identifier(self.case_id, "Case ID"))
        _bounded_int(self.expected_version, 1, 2_147_483_647, "Expected version")


@dataclass(frozen=True, slots=True)
class RecoverAgentRunCommand:
    """Reconcile a pre-call reservation left by an interrupted process."""

    run_id: str
    case_id: str
    expected_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "Run ID"))
        object.__setattr__(self, "case_id", _identifier(self.case_id, "Case ID"))
        _bounded_int(self.expected_version, 1, 2_147_483_647, "Expected version")


@dataclass(frozen=True, slots=True)
class AgentCheckpoint:
    """Validated, bounded stage state; never a raw provider response."""

    stage: AgentStageName
    stage_version: str
    schema_version: str
    input_scope_sha256: str
    payload_json: str
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """Committed and in-flight accounting read from SQLite authority."""

    calls: int
    input_tokens: int
    output_tokens: int
    cost_minor: int
    reserved_input_tokens: int
    reserved_output_tokens: int
    reserved_cost_minor: int
    cost_currency: str = "CNY"

    def __post_init__(self) -> None:
        for value in (
            self.calls,
            self.input_tokens,
            self.output_tokens,
            self.cost_minor,
            self.reserved_input_tokens,
            self.reserved_output_tokens,
            self.reserved_cost_minor,
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise AgentValidationError("Agent usage is invalid")
        if self.cost_currency != "CNY":
            raise AgentValidationError("Agent usage currency is invalid")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def reserved_total_tokens(self) -> int:
        return self.reserved_input_tokens + self.reserved_output_tokens


@dataclass(frozen=True, slots=True)
class AgentStageRecord:
    """Stable durable projection of one stage."""

    id: str
    run_id: str
    case_id: str
    stage: AgentStageName
    stage_version: str
    input_schema_version: str
    output_schema_version: str
    checkpoint_schema_version: str
    input_scope_sha256: str
    status: AgentStageStatus
    version: int
    budget: StageBudget
    usage: BudgetUsage
    repair_count: int
    provider: str | None
    model_id: str | None
    latency_ms: int
    checkpoint: AgentCheckpoint | None
    error_code: str | None
    retryable: bool | None
    started_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class AgentRunRecord:
    """Stable Case-scoped pipeline projection with ordered stages."""

    id: str
    case_id: str
    status: AgentRunStatus
    version: int
    current_stage: AgentStageName | None
    last_completed_stage: AgentStageName | None
    budget: AgentBudget
    usage: BudgetUsage
    cancellation_requested: bool
    created_at: str
    updated_at: str
    completed_at: str | None
    stages: tuple[AgentStageRecord, ...]


def checkpoint_payload(value: object) -> tuple[str, str]:
    """Validate and canonicalize persisted stage state."""

    if not isinstance(value, dict):
        raise AgentValidationError("Agent checkpoint is invalid")
    _validate_checkpoint_tree(value)
    try:
        payload_json = canonical_json_object(value, maximum_bytes=MAX_CHECKPOINT_BYTES)
    except ValueError as error:
        raise AgentValidationError("Agent checkpoint is invalid") from error
    return payload_json, hashlib.sha256(payload_json.encode()).hexdigest()


def stage_input_scope_sha256(
    case_id: str,
    stage: AgentStageName,
    stage_version: str,
    input_schema_version: str,
    payload_json: str,
) -> str:
    """Hash the complete canonical input scope without persisting its body."""

    if not isinstance(stage, AgentStageName):
        raise AgentValidationError("Agent stage is invalid")
    try:
        canonical = canonical_json_object(
            json.loads(payload_json), maximum_bytes=MAX_STAGE_INPUT_BYTES
        )
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise AgentValidationError("Stage input is invalid") from error
    return request_scope_sha256(
        "agent_stage_input.v1",
        _identifier(case_id, "Case ID"),
        stage.value,
        _version(stage_version),
        _version(input_schema_version),
        canonical,
    )


def _validate_checkpoint_tree(value: object) -> None:
    prohibited = {
        "prompt",
        "rawprompt",
        "fullevidence",
        "sourcetext",
        "sdkresponse",
        "credentials",
        "apikey",
    }
    remaining: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while remaining:
        current, depth = remaining.pop()
        nodes += 1
        if nodes > 2_000 or depth > 12:
            raise AgentValidationError("Agent checkpoint is invalid")
        if isinstance(current, dict):
            for key, child in current.items():
                if not isinstance(key, str) or not 1 <= len(key) <= 100:
                    raise AgentValidationError("Agent checkpoint is invalid")
                normalized_key = re.sub(r"[^a-z0-9]", "", key.strip().lower())
                if normalized_key in prohibited:
                    raise AgentValidationError("Agent checkpoint is invalid")
                remaining.append((child, depth + 1))
        elif isinstance(current, list):
            if len(current) > 500:
                raise AgentValidationError("Agent checkpoint is invalid")
            remaining.extend((child, depth + 1) for child in current)
        elif isinstance(current, str):
            if len(current) > 4_000:
                raise AgentValidationError("Agent checkpoint is invalid")
        elif current is not None:
            if not isinstance(current, (bool, int, float)):
                raise AgentValidationError("Agent checkpoint is invalid")
            if isinstance(current, float) and not math.isfinite(current):
                raise AgentValidationError("Agent checkpoint is invalid")


def _validate_stage_input_envelope(value: object, case_id: str) -> None:
    if not isinstance(value, dict) or set(value) != {
        "case_id",
        "prior_checkpoint_hashes",
        "stage_payload",
    }:
        raise AgentValidationError("Stage input is invalid")
    if value["case_id"] != case_id or not isinstance(value["stage_payload"], dict):
        raise AgentValidationError("Stage input is invalid")
    hashes = value["prior_checkpoint_hashes"]
    if (
        not isinstance(hashes, list)
        or len(hashes) > len(STAGE_ORDER)
        or any(
            not isinstance(item, str) or _SHA256_PATTERN.fullmatch(item) is None for item in hashes
        )
    ):
        raise AgentValidationError("Stage input is invalid")


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise AgentValidationError(f"{label} is invalid")
    clean = value.strip()
    if _IDENTIFIER_PATTERN.fullmatch(clean) is None:
        raise AgentValidationError(f"{label} is invalid")
    return clean


def _version(value: object) -> str:
    if not isinstance(value, str):
        raise AgentValidationError("Agent version is invalid")
    clean = value.strip()
    if _VERSION_PATTERN.fullmatch(clean) is None:
        raise AgentValidationError("Agent version is invalid")
    return clean


def _bounded_int(value: object, minimum: int, maximum: int, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise AgentValidationError(f"{label} is invalid")
