"""Typed Case aggregate and transition contracts."""

from dataclasses import dataclass
from enum import StrEnum


class CaseCommandValidationError(ValueError):
    """Raised when a Case transition command violates its boundary contract."""


class CaseStatus(StrEnum):
    """Durable lifecycle states for a Case."""

    INTAKE = "intake"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    WAITING_EXTERNAL = "waiting_external"
    BLOCKED = "blocked"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"
    ARCHIVED = "archived"


class CaseDeadlineRisk(StrEnum):
    """M4-01 never presents an unimplemented deadline calculation."""

    NOT_CALCULATED = "not_calculated"


class CaseActivityKind(StrEnum):
    """Authoritative timestamp categories admitted to the list projection."""

    CASE_UPDATED = "case_updated"
    SOURCE_IMPORTED = "source_imported"
    EXTRACTION_UPDATED = "extraction_updated"
    CLAIM_UPDATED = "claim_updated"
    AGENT_UPDATED = "agent_updated"
    ACTION_UPDATED = "action_updated"


@dataclass(frozen=True, slots=True)
class CaseNextActionSummary:
    """Bounded M5-02 summary selected by authoritative graph order."""

    id: str
    kind: str
    status: str
    risk_level: str
    blocker_count: int
    version: int


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """Portable authoritative projection of one persisted Case."""

    id: str
    type: str
    template_id: str
    template_version: int
    title: str
    goal: str
    status: CaseStatus
    priority: int
    version: int
    created_at: str
    updated_at: str
    closed_at: str | None


@dataclass(frozen=True, slots=True)
class CaseMissingEvidence:
    """Ordered M2-03 gaps shown without copying Claim values or source text."""

    required_claims: tuple[str, ...]
    evidence_requirements: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.required_claims) + len(self.evidence_requirements)


@dataclass(frozen=True, slots=True)
class CaseWorkbenchItem:
    """Read-only M4-01 Case list and Today projection."""

    case: CaseRecord
    is_today: bool
    next_action: CaseNextActionSummary | None
    deadline_risk: CaseDeadlineRisk
    missing_evidence: CaseMissingEvidence
    last_activity_at: str
    last_activity_kind: CaseActivityKind


@dataclass(frozen=True, slots=True)
class CaseTransitionCommand:
    """Validated intent to move one expected Case version to another state."""

    case_id: str
    expected_version: int
    target_status: CaseStatus
    actor: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str):
            raise CaseCommandValidationError("Case ID is invalid")
        case_id = self.case_id.strip()
        if not 1 <= len(case_id) <= 200:
            raise CaseCommandValidationError("Case ID is invalid")
        if (
            not isinstance(self.expected_version, int)
            or isinstance(self.expected_version, bool)
            or self.expected_version <= 0
        ):
            raise CaseCommandValidationError("Expected version is invalid")
        if not isinstance(self.target_status, CaseStatus):
            raise CaseCommandValidationError("Target status is invalid")
        if not isinstance(self.actor, str) or not isinstance(self.reason, str):
            raise CaseCommandValidationError("Transition metadata is invalid")
        actor = self.actor.strip()
        reason = self.reason.strip()
        if not 1 <= len(actor) <= 120 or not 1 <= len(reason) <= 1000:
            raise CaseCommandValidationError("Transition metadata is invalid")
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True, slots=True)
class CaseTransition:
    """One immutable transition-history record."""

    id: str
    case_id: str
    prior_status: CaseStatus
    new_status: CaseStatus
    prior_version: int
    new_version: int
    actor: str
    reason: str
    transitioned_at: str


@dataclass(frozen=True, slots=True)
class CaseTransitionResult:
    """The committed Case and matching append-only history entry."""

    case: CaseRecord
    transition: CaseTransition
