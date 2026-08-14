"""Thin HTTP contracts for Case-scoped Deadline authority."""

from typing import NoReturn

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.deadlines import (
    CreateCalculatedDeadlineCommand,
    CreateExplicitDeadlineCommand,
    CreateSuggestedDeadlineCommand,
    DeadlineBasisClaimNotFoundError,
    DeadlineBasisInput,
    DeadlineBasisInvalidError,
    DeadlineBasisStaleError,
    DeadlineCaseNotFoundError,
    DeadlineCommandValidationError,
    DeadlineDate,
    DeadlineDateKind,
    DeadlineIntegrityError,
    DeadlineKind,
    DeadlineNotFoundError,
    DeadlinePersistenceError,
    DeadlineRecord,
    DeadlineRuleInvalidError,
    DeadlineRuleParameters,
    DeadlineService,
    DeadlineServiceError,
    DeadlineStatus,
    DeadlineStatusCommand,
    DeadlineTransitionNotAllowedError,
    DeadlineUncertainty,
    DeadlineVersionConflictError,
)

router = APIRouter()


class DeadlineBasisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=200)
    expected_claim_version: int = Field(gt=0, strict=True)
    value_sha256: str = Field(min_length=64, max_length=64)
    date_kind: DeadlineDateKind


class ExplicitDeadlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    basis: DeadlineBasisRequest
    timezone: str = Field(min_length=1, max_length=100)
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class CalculatedDeadlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bases: list[DeadlineBasisRequest] = Field(min_length=1, max_length=10)
    timezone: str = Field(min_length=1, max_length=100)
    rule_id: str = Field(min_length=1, max_length=64)
    rule_version: int = Field(gt=0, strict=True)
    rule_parameters: dict[str, object]
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class SuggestedDeadlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_kind: DeadlineDateKind
    due_value: str = Field(min_length=1, max_length=40)
    timezone: str = Field(min_length=1, max_length=100)
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class DeadlineStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(gt=0, strict=True)
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class DeadlineBasisResponse(BaseModel):
    claim_id: str
    claim_version: int
    value_sha256: str
    field: str
    date_kind: DeadlineDateKind


class DeadlineRuleResponse(BaseModel):
    id: str
    version: int
    parameters: dict[str, object]
    parameters_sha256: str


class DeadlineResponse(BaseModel):
    id: str
    case_id: str
    kind: DeadlineKind
    date_kind: DeadlineDateKind
    due_value: str
    timezone: str
    status: DeadlineStatus
    uncertainty: DeadlineUncertainty
    confidence: float | None
    rule: DeadlineRuleResponse | None
    bases: list[DeadlineBasisResponse]
    version: int
    definition_sha256: str
    created_at: str
    updated_at: str


@router.get("/cases/{case_id}/deadlines", response_model=list[DeadlineResponse])
def list_deadlines(case_id: str, database: DatabaseDependency) -> list[DeadlineResponse]:
    try:
        records = DeadlineService(database).list_case_deadlines(case_id)
    except DeadlineServiceError as error:
        _raise_deadline_error(error)
    return [_response(record) for record in records]


@router.post(
    "/cases/{case_id}/deadlines/explicit",
    response_model=DeadlineResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_explicit(
    case_id: str, payload: ExplicitDeadlineRequest, database: DatabaseDependency
) -> DeadlineResponse:
    try:
        record = DeadlineService(database).create_explicit(
            CreateExplicitDeadlineCommand(
                case_id=case_id,
                basis=_basis(payload.basis),
                timezone=payload.timezone,
                actor=payload.actor,
                reason=payload.reason,
            )
        )
    except (DeadlineServiceError, DeadlineCommandValidationError) as error:
        _raise_deadline_error(error)
    return _response(record)


@router.post(
    "/cases/{case_id}/deadlines/calculated",
    response_model=DeadlineResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_calculated(
    case_id: str, payload: CalculatedDeadlineRequest, database: DatabaseDependency
) -> DeadlineResponse:
    try:
        record = DeadlineService(database).create_calculated(
            CreateCalculatedDeadlineCommand(
                case_id=case_id,
                bases=tuple(_basis(item) for item in payload.bases),
                timezone=payload.timezone,
                rule_id=payload.rule_id,
                rule_version=payload.rule_version,
                rule_parameters=DeadlineRuleParameters.from_input(payload.rule_parameters),
                actor=payload.actor,
                reason=payload.reason,
            )
        )
    except (DeadlineServiceError, DeadlineCommandValidationError) as error:
        _raise_deadline_error(error)
    return _response(record)


@router.post(
    "/cases/{case_id}/deadlines/suggested",
    response_model=DeadlineResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_suggested(
    case_id: str, payload: SuggestedDeadlineRequest, database: DatabaseDependency
) -> DeadlineResponse:
    try:
        record = DeadlineService(database).create_suggested(
            CreateSuggestedDeadlineCommand(
                case_id=case_id,
                due=DeadlineDate(
                    kind=payload.date_kind,
                    value=payload.due_value,
                    timezone=payload.timezone,
                ),
                actor=payload.actor,
                reason=payload.reason,
            )
        )
    except (DeadlineServiceError, DeadlineCommandValidationError) as error:
        _raise_deadline_error(error)
    return _response(record)


@router.post("/deadlines/{deadline_id}/confirm", response_model=DeadlineResponse)
def confirm_deadline(
    deadline_id: str, payload: DeadlineStatusRequest, database: DatabaseDependency
) -> DeadlineResponse:
    return _status_change(database, deadline_id, payload, "confirm")


@router.post("/deadlines/{deadline_id}/pass", response_model=DeadlineResponse)
def pass_deadline(
    deadline_id: str, payload: DeadlineStatusRequest, database: DatabaseDependency
) -> DeadlineResponse:
    return _status_change(database, deadline_id, payload, "pass")


@router.post("/deadlines/{deadline_id}/complete", response_model=DeadlineResponse)
def complete_deadline(
    deadline_id: str, payload: DeadlineStatusRequest, database: DatabaseDependency
) -> DeadlineResponse:
    return _status_change(database, deadline_id, payload, "complete")


@router.post("/deadlines/{deadline_id}/dismiss", response_model=DeadlineResponse)
def dismiss_deadline(
    deadline_id: str, payload: DeadlineStatusRequest, database: DatabaseDependency
) -> DeadlineResponse:
    return _status_change(database, deadline_id, payload, "dismiss")


def _status_change(
    database: DatabaseDependency,
    deadline_id: str,
    payload: DeadlineStatusRequest,
    operation: str,
) -> DeadlineResponse:
    try:
        service = DeadlineService(database)
        command = DeadlineStatusCommand(
            deadline_id=deadline_id,
            expected_version=payload.expected_version,
            actor=payload.actor,
            reason=payload.reason,
        )
        if operation == "confirm":
            record = service.confirm(command)
        elif operation == "pass":
            record = service.mark_passed(command)
        elif operation == "complete":
            record = service.complete(command)
        else:
            record = service.dismiss(command)
    except (DeadlineServiceError, DeadlineCommandValidationError) as error:
        _raise_deadline_error(error)
    return _response(record)


def _basis(payload: DeadlineBasisRequest) -> DeadlineBasisInput:
    return DeadlineBasisInput(
        claim_id=payload.claim_id,
        expected_claim_version=payload.expected_claim_version,
        value_sha256=payload.value_sha256,
        date_kind=payload.date_kind,
    )


def _response(record: DeadlineRecord) -> DeadlineResponse:
    rule = None
    if record.rule_id and record.rule_version and record.rule_parameters:
        rule = DeadlineRuleResponse(
            id=record.rule_id,
            version=record.rule_version,
            parameters=record.rule_parameters.to_dict(),
            parameters_sha256=record.rule_parameters.sha256,
        )
    return DeadlineResponse(
        id=record.id,
        case_id=record.case_id,
        kind=record.kind,
        date_kind=record.due.kind,
        due_value=record.due.value,
        timezone=record.due.timezone,
        status=record.status,
        uncertainty=record.uncertainty,
        confidence=record.confidence,
        rule=rule,
        bases=[
            DeadlineBasisResponse(
                claim_id=item.claim_id,
                claim_version=item.claim_version,
                value_sha256=item.value_sha256,
                field=item.field,
                date_kind=item.date_kind,
            )
            for item in record.bases
        ],
        version=record.version,
        definition_sha256=record.definition_sha256,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _raise_deadline_error(error: Exception) -> NoReturn:
    if isinstance(error, DeadlineCommandValidationError):
        raise ApiError(
            status_code=422, code="deadline_invalid", message="Deadline request is invalid"
        ) from error
    if isinstance(error, DeadlineCaseNotFoundError):
        raise ApiError(
            status_code=404, code="case_not_found", message="Case was not found"
        ) from error
    if isinstance(error, DeadlineBasisClaimNotFoundError):
        raise ApiError(
            status_code=404,
            code="deadline_basis_claim_not_found",
            message="Basis Claim was not found",
        ) from error
    if isinstance(error, DeadlineNotFoundError):
        raise ApiError(
            status_code=404, code="deadline_not_found", message="Deadline was not found"
        ) from error
    if isinstance(error, DeadlineBasisStaleError):
        raise ApiError(
            status_code=409, code="deadline_basis_stale", message="Basis Claim has changed"
        ) from error
    if isinstance(error, DeadlineBasisInvalidError):
        raise ApiError(
            status_code=409,
            code="deadline_basis_invalid",
            message="Basis Claim is not authoritative",
        ) from error
    if isinstance(error, DeadlineRuleInvalidError):
        raise ApiError(
            status_code=422, code="deadline_rule_invalid", message="Deadline rule is invalid"
        ) from error
    if isinstance(error, DeadlineVersionConflictError):
        raise ApiError(
            status_code=409,
            code="deadline_version_conflict",
            message="Deadline version has changed",
            details={"current_version": error.current_version},
        ) from error
    if isinstance(error, DeadlineTransitionNotAllowedError):
        raise ApiError(
            status_code=409,
            code="deadline_transition_not_allowed",
            message="Deadline transition is not allowed",
        ) from error
    if isinstance(error, DeadlineIntegrityError):
        raise ApiError(
            status_code=409,
            code="deadline_integrity_failed",
            message="Deadline authority is inconsistent",
        ) from error
    if isinstance(error, DeadlinePersistenceError):
        raise ApiError(
            status_code=503,
            code="deadline_storage_failed",
            message="Deadline could not be read or saved",
            retryable=True,
        ) from error
    raise ApiError(
        status_code=503,
        code="deadline_unavailable",
        message="Deadline service is unavailable",
    ) from error
