"""Thin HTTP contract for the Case-scoped Timeline."""

from typing import NoReturn

from fastapi import APIRouter
from pydantic import BaseModel

from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.timeline import (
    TimelineAuthorityType,
    TimelineCaseNotFoundError,
    TimelineError,
    TimelineEvent,
    TimelineEventKind,
    TimelineFeatureAvailability,
    TimelineIntegrityError,
    TimelineOrigin,
    TimelinePersistenceError,
    TimelineResult,
    TimelineService,
    TimelineValidationError,
)

router = APIRouter()


class TimelineEventResponse(BaseModel):
    """Safe reference to one authoritative persisted row."""

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


class TimelineAvailabilityResponse(BaseModel):
    """Later-milestone event families not yet authoritative."""

    approvals: TimelineFeatureAvailability
    communications: TimelineFeatureAvailability


class TimelineResponse(BaseModel):
    """Deterministic M4-03 Timeline for one Case."""

    case_id: str
    events: list[TimelineEventResponse]
    availability: TimelineAvailabilityResponse


@router.get("/cases/{case_id}/timeline", response_model=TimelineResponse)
def list_timeline(case_id: str, database: DatabaseDependency) -> TimelineResponse:
    """Return admitted persisted activity without exposing private bodies."""

    try:
        result = TimelineService(database).list_case_timeline(case_id)
    except TimelineError as error:
        _raise_timeline_api_error(error)
    return _timeline_response(result)


def _timeline_response(result: TimelineResult) -> TimelineResponse:
    return TimelineResponse(
        case_id=result.case_id,
        events=[_event_response(event) for event in result.events],
        availability=TimelineAvailabilityResponse(
            approvals=result.availability.approvals,
            communications=result.availability.communications,
        ),
    )


def _event_response(event: TimelineEvent) -> TimelineEventResponse:
    return TimelineEventResponse(
        event_id=event.event_id,
        case_id=event.case_id,
        kind=event.kind,
        occurred_at=event.occurred_at,
        authority_type=event.authority_type,
        authority_id=event.authority_id,
        subject_id=event.subject_id,
        parent_id=event.parent_id,
        origin=event.origin,
        actor=event.actor,
        label=event.label,
        status=event.status,
        prior_status=event.prior_status,
        version=event.version,
        reason=event.reason,
    )


def _raise_timeline_api_error(error: TimelineError) -> NoReturn:
    if isinstance(error, TimelineValidationError):
        raise ApiError(
            status_code=422,
            code="timeline_invalid",
            message="Timeline request is invalid",
        ) from error
    if isinstance(error, TimelineCaseNotFoundError):
        raise ApiError(
            status_code=404,
            code="case_not_found",
            message="Case was not found",
        ) from error
    if isinstance(error, TimelineIntegrityError):
        raise ApiError(
            status_code=409,
            code="timeline_integrity_failed",
            message="Timeline authority is inconsistent",
        ) from error
    if isinstance(error, TimelinePersistenceError):
        raise ApiError(
            status_code=503,
            code="timeline_unavailable",
            message="Timeline could not be read",
            retryable=True,
        ) from error
    raise ApiError(
        status_code=503,
        code="timeline_unavailable",
        message="Timeline is unavailable",
    ) from error
