"""Case HTTP contracts and thin lifecycle routes."""

from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.api.sources import SourceResponse, list_case_source_responses
from lifecase.cases import (
    CaseActivityKind,
    CaseCommandValidationError,
    CaseDeadlineRisk,
    CaseNextActionSummary,
    CaseNotFoundError,
    CasePersistenceError,
    CaseRecord,
    CaseService,
    CaseStatus,
    CaseTemplateBindingError,
    CaseTransition,
    CaseTransitionCommand,
    CaseTransitionNotAllowedError,
    CaseValidationError,
    CaseVersionConflictError,
    CaseWorkbenchItem,
)

router = APIRouter()


class CaseCreateRequest(BaseModel):
    """Validated minimal Case intake request."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=1000)
    template_id: Literal[
        "warranty",
        "return_refund",
        "reimbursement",
        "subscription_cancellation",
    ]


class CaseResponse(BaseModel):
    """Durable authoritative Case projection."""

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
    sources: list[SourceResponse]


class CaseMissingEvidenceResponse(BaseModel):
    """Counts and stable identifiers for unresolved template requirements."""

    required_claims: list[str]
    evidence_requirements: list[str]
    total: int


class CaseNextActionResponse(BaseModel):
    """Bounded Action summary; full authority remains on Action routes."""

    id: str
    kind: str
    status: str
    risk_level: str
    blocker_count: int
    version: int


class CaseWorkbenchResponse(BaseModel):
    """Privacy-bounded read model for the Case list and Today view."""

    id: str
    type: str
    template_id: str
    template_version: int
    title: str
    goal: str
    status: CaseStatus
    priority: int
    version: int
    is_today: bool
    next_action: CaseNextActionResponse | None
    deadline_risk: CaseDeadlineRisk
    missing_evidence: CaseMissingEvidenceResponse
    last_activity_at: str
    last_activity_kind: CaseActivityKind
    created_at: str
    updated_at: str
    closed_at: str | None


class CaseTransitionRequest(BaseModel):
    """Concurrency-bound request for one lifecycle transition."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(gt=0, strict=True)
    target_status: CaseStatus
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class CaseTransitionResponse(BaseModel):
    """One append-only Case transition record."""

    id: str
    case_id: str
    prior_status: CaseStatus
    new_status: CaseStatus
    prior_version: int
    new_version: int
    actor: str
    reason: str
    transitioned_at: str


@router.get("", response_model=list[CaseWorkbenchResponse])
def list_cases(database: DatabaseDependency) -> list[CaseWorkbenchResponse]:
    """Return the deterministic read-only Case list and Today projection."""

    try:
        items = CaseService(database).list_workbench_cases()
    except (CasePersistenceError, CaseTemplateBindingError) as error:
        raise ApiError(
            status_code=503,
            code="case_storage_failed",
            message="Case list could not be read",
            retryable=True,
        ) from error
    return [_workbench_response(item) for item in items]


@router.post("", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
def create_case(payload: CaseCreateRequest, database: DatabaseDependency) -> CaseResponse:
    """Create one persisted intake Case."""

    try:
        record = CaseService(database).create_case(
            title=payload.title,
            goal=payload.goal,
            template_id=payload.template_id,
        )
    except CaseValidationError as error:
        raise ApiError(
            status_code=422,
            code="case_invalid",
            message="Case title, goal, or template is invalid",
        ) from error
    except (CasePersistenceError, CaseTemplateBindingError) as error:
        raise ApiError(
            status_code=503,
            code="case_storage_failed",
            message="Case could not be saved",
            retryable=True,
        ) from error
    return _case_response(record, list_case_source_responses(database, record.id))


@router.post("/{case_id}/transitions", response_model=CaseTransitionResponse)
def transition_case(
    case_id: str,
    payload: CaseTransitionRequest,
    database: DatabaseDependency,
) -> CaseTransitionResponse:
    """Validate and commit one optimistic Case lifecycle transition."""

    try:
        command = CaseTransitionCommand(
            case_id=case_id,
            expected_version=payload.expected_version,
            target_status=payload.target_status,
            actor=payload.actor,
            reason=payload.reason,
        )
        result = CaseService(database).transition_case(command)
    except CaseCommandValidationError as error:
        raise ApiError(
            status_code=422,
            code="case_transition_invalid",
            message="Case transition request is invalid",
        ) from error
    except CaseNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="case_not_found",
            message="Case was not found",
        ) from error
    except CaseTransitionNotAllowedError as error:
        raise ApiError(
            status_code=409,
            code="case_transition_not_allowed",
            message="Case transition is not allowed",
        ) from error
    except CaseVersionConflictError as error:
        raise ApiError(
            status_code=409,
            code="case_version_conflict",
            message="Case version has changed",
            details={"current_version": error.current_version},
        ) from error
    except (CasePersistenceError, CaseTemplateBindingError) as error:
        raise ApiError(
            status_code=503,
            code="case_storage_failed",
            message="Case transition could not be saved",
            retryable=True,
        ) from error
    return _transition_response(result.transition)


@router.get("/{case_id}/transitions", response_model=list[CaseTransitionResponse])
def list_case_transitions(
    case_id: str,
    database: DatabaseDependency,
) -> list[CaseTransitionResponse]:
    """Read deterministic append-only Case transition history."""

    try:
        transitions = CaseService(database).list_case_transitions(case_id)
    except CaseValidationError as error:
        raise ApiError(
            status_code=422,
            code="case_transition_invalid",
            message="Case transition history request is invalid",
        ) from error
    except CaseNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="case_not_found",
            message="Case was not found",
        ) from error
    except CasePersistenceError as error:
        raise ApiError(
            status_code=503,
            code="case_storage_failed",
            message="Case transition history could not be read",
            retryable=True,
        ) from error
    return [_transition_response(transition) for transition in transitions]


@router.get("/{case_id}", response_model=CaseResponse)
def get_case(case_id: str, database: DatabaseDependency) -> CaseResponse:
    """Return a Case from authoritative SQLite state."""

    try:
        record = CaseService(database).get_case(case_id)
    except CaseNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="case_not_found",
            message="Case was not found",
        ) from error
    except (CasePersistenceError, CaseTemplateBindingError) as error:
        raise ApiError(
            status_code=503,
            code="case_storage_failed",
            message="Case could not be read",
            retryable=True,
        ) from error
    return _case_response(record, list_case_source_responses(database, record.id))


def _case_response(record: CaseRecord, sources: list[SourceResponse]) -> CaseResponse:
    return CaseResponse(
        id=record.id,
        type=record.type,
        template_id=record.template_id,
        template_version=record.template_version,
        title=record.title,
        goal=record.goal,
        status=record.status,
        priority=record.priority,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
        closed_at=record.closed_at,
        sources=sources,
    )


def _transition_response(transition: CaseTransition) -> CaseTransitionResponse:
    return CaseTransitionResponse(
        id=transition.id,
        case_id=transition.case_id,
        prior_status=transition.prior_status,
        new_status=transition.new_status,
        prior_version=transition.prior_version,
        new_version=transition.new_version,
        actor=transition.actor,
        reason=transition.reason,
        transitioned_at=transition.transitioned_at,
    )


def _workbench_response(item: CaseWorkbenchItem) -> CaseWorkbenchResponse:
    record = item.case
    return CaseWorkbenchResponse(
        id=record.id,
        type=record.type,
        template_id=record.template_id,
        template_version=record.template_version,
        title=record.title,
        goal=record.goal,
        status=record.status,
        priority=record.priority,
        version=record.version,
        is_today=item.is_today,
        next_action=(
            _next_action_response(item.next_action) if item.next_action is not None else None
        ),
        deadline_risk=item.deadline_risk,
        missing_evidence=CaseMissingEvidenceResponse(
            required_claims=list(item.missing_evidence.required_claims),
            evidence_requirements=list(item.missing_evidence.evidence_requirements),
            total=item.missing_evidence.total,
        ),
        last_activity_at=item.last_activity_at,
        last_activity_kind=item.last_activity_kind,
        created_at=record.created_at,
        updated_at=record.updated_at,
        closed_at=record.closed_at,
    )


def _next_action_response(item: CaseNextActionSummary) -> CaseNextActionResponse:
    return CaseNextActionResponse(
        id=item.id,
        kind=item.kind,
        status=item.status,
        risk_level=item.risk_level,
        blocker_count=item.blocker_count,
        version=item.version,
    )
