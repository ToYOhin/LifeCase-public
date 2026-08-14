"""Immutable Case-scoped Timeline projections."""

from dataclasses import dataclass
from enum import StrEnum


class TimelineEventKind(StrEnum):
    """Closed event vocabulary admitted by M4-03."""

    SOURCE_IMPORTED = "source_imported"
    SOURCE_EXTRACTION_STATUS = "source_extraction_status"
    ARTIFACT_CREATED = "artifact_created"
    CASE_TRANSITIONED = "case_transitioned"
    CLAIM_CREATED = "claim_created"
    CLAIM_SOURCE_ATTACHED = "claim_source_attached"
    CLAIM_CONFIRMED = "claim_confirmed"
    CLAIM_CORRECTED = "claim_corrected"
    CLAIM_REJECTED = "claim_rejected"
    AGENT_RUN_STATUS = "agent_run_status"
    AGENT_STAGE_STATUS = "agent_stage_status"
    DEADLINE_CREATED = "deadline_created"
    DEADLINE_CONFIRMED = "deadline_confirmed"
    DEADLINE_PASSED = "deadline_passed"
    DEADLINE_COMPLETED = "deadline_completed"
    DEADLINE_DISMISSED = "deadline_dismissed"
    ACTION_CREATED = "action_created"
    ACTION_REVISED = "action_revised"
    ACTION_REVALIDATED = "action_revalidated"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"
    ACTION_CANCELLED = "action_cancelled"
    ACTION_APPROVED = "action_approved"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_INVALIDATED = "approval_invalidated"


class TimelineAuthorityType(StrEnum):
    """Authoritative row type behind one projection item."""

    SOURCE = "source"
    SOURCE_EXTRACTION = "source_extraction"
    ARTIFACT = "artifact"
    CASE_TRANSITION = "case_transition"
    CLAIM_EVENT = "claim_event"
    AGENT_RUN = "agent_run"
    AGENT_STAGE_RUN = "agent_stage_run"
    DEADLINE_EVENT = "deadline_event"
    ACTION_EVENT = "action_event"
    APPROVAL_EVENT = "approval_event"


class TimelineOrigin(StrEnum):
    """Persisted origin semantics without asserting authentication."""

    LOCAL_IMPORT = "local_import"
    EXTRACTOR = "extractor"
    RECORDED_ACTOR = "recorded_actor"
    AGENT = "agent"
    ARTIFACT_SERVICE = "artifact_service"


class TimelineFeatureAvailability(StrEnum):
    """Truthful availability for later-milestone event families."""

    NOT_AVAILABLE_UNTIL_M5 = "not_available_until_m5"
    AVAILABLE = "available"
    AVAILABLE_AS_ACTION_PROJECTION = "available_as_action_projection"


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """One safe reference to an authoritative persisted row."""

    event_id: str
    case_id: str
    kind: TimelineEventKind
    occurred_at: str
    authority_type: TimelineAuthorityType
    authority_id: str
    subject_id: str
    parent_id: str | None
    origin: TimelineOrigin
    actor: str | None
    label: str | None
    status: str | None
    prior_status: str | None
    version: int | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class TimelineAvailability:
    """Explicit M4/M5 product boundary returned with every Timeline."""

    approvals: TimelineFeatureAvailability = TimelineFeatureAvailability.AVAILABLE
    communications: TimelineFeatureAvailability = (
        TimelineFeatureAvailability.AVAILABLE_AS_ACTION_PROJECTION
    )


@dataclass(frozen=True, slots=True)
class TimelineResult:
    """Deterministic ordered Timeline for exactly one Case."""

    case_id: str
    events: tuple[TimelineEvent, ...]
    availability: TimelineAvailability = TimelineAvailability()
