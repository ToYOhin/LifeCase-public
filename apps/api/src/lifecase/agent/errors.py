"""Safe typed failures for durable Agent orchestration."""

from __future__ import annotations

from enum import StrEnum


class AgentErrorCode(StrEnum):
    """Stable error codes that contain no prompts, evidence, SQL, or paths."""

    INVALID_REQUEST = "agent_invalid_request"
    CASE_NOT_FOUND = "agent_case_not_found"
    RUN_NOT_FOUND = "agent_run_not_found"
    VERSION_CONFLICT = "agent_version_conflict"
    INVALID_SEQUENCE = "agent_invalid_sequence"
    BUDGET_EXHAUSTED = "agent_budget_exhausted"
    CANCELLED = "agent_cancelled"
    TIMEOUT = "agent_timeout"
    PROVIDER_FAILURE = "agent_provider_failure"
    SCHEMA_INVALID = "agent_schema_invalid"
    INTEGRITY = "agent_integrity_failure"
    PERSISTENCE = "agent_persistence_failure"
    INTERRUPTED = "agent_interrupted"


class AgentServiceError(RuntimeError):
    """Base class for errors crossing the Agent service boundary."""

    code: AgentErrorCode
    retryable: bool

    def __init__(
        self,
        message: str,
        *,
        code: AgentErrorCode,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AgentValidationError(ValueError):
    """Raised before persistence when a command violates stable boundaries."""


class AgentCaseNotFoundError(AgentServiceError):
    """Raised when the explicitly scoped Case does not exist."""


class AgentRunNotFoundError(AgentServiceError):
    """Raised when the Case-scoped durable run does not exist."""


class AgentVersionConflictError(AgentServiceError):
    """Raised when optimistic concurrency rejects stale state."""

    def __init__(self, current_version: int) -> None:
        super().__init__(
            "Agent run version does not match the command",
            code=AgentErrorCode.VERSION_CONFLICT,
        )
        self.current_version = current_version


class AgentSequenceError(AgentServiceError):
    """Raised when a stage would skip or replace the durable order."""


class AgentBudgetError(AgentServiceError):
    """Raised before a provider call that cannot be safely reserved."""


class AgentCancelledError(AgentServiceError):
    """Raised after durable cancellation has been recorded."""


class AgentStageTimeoutError(AgentServiceError):
    """Raised after one bounded call times out and is durably stopped."""


class AgentStageSchemaError(AgentServiceError):
    """Raised when one repair still cannot satisfy the stage schema."""


class AgentProviderFailureError(AgentServiceError):
    """Raised after a provider-neutral failure is durably classified."""


class AgentIntegrityError(AgentServiceError):
    """Raised when persisted authority cannot satisfy Agent invariants."""


class AgentPersistenceError(AgentServiceError):
    """Raised when an Agent transaction cannot be committed."""
