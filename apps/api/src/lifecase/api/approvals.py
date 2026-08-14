"""Thin HTTP contracts for local Action approvals."""

from typing import NoReturn

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from lifecase.actions import ActionRecord, ActionService
from lifecase.api.actions import ActionResponse, action_response
from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.approvals import (
    ApprovalActionInvalidError,
    ApprovalActionNotFoundError,
    ApprovalActionVersionConflictError,
    ApprovalAlreadyActiveError,
    ApprovalCaseNotFoundError,
    ApprovalCommandValidationError,
    ApprovalDecisionCommand,
    ApprovalIntegrityError,
    ApprovalNotFoundError,
    ApprovalPersistenceError,
    ApprovalRecord,
    ApprovalService,
    ApprovalServiceError,
    ApprovalStatus,
    ApprovalSummary,
    ApprovalTransitionNotAllowedError,
    ApprovalVersionConflictError,
    RequestApprovalCommand,
)

router = APIRouter()


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_action_version: int = Field(gt=0, strict=True)
    payload_sha256: str = Field(min_length=64, max_length=64)
    summary: str = Field(min_length=1, max_length=1000)
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1, max_length=200)
    expected_approval_version: int = Field(gt=0, strict=True)
    expected_action_version: int = Field(gt=0, strict=True)
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class ApprovalResponse(BaseModel):
    id: str
    action_id: str
    case_id: str
    action_version: int
    payload_sha256: str
    summary: str
    summary_sha256: str
    status: ApprovalStatus
    version: int
    requested_by: str
    requested_reason: str
    created_at: str
    updated_at: str


class ApprovalDecisionResponse(BaseModel):
    approval: ApprovalResponse
    action: ActionResponse


@router.get("/cases/{case_id}/approvals", response_model=list[ApprovalResponse])
def list_approvals(case_id: str, database: DatabaseDependency) -> list[ApprovalResponse]:
    try:
        records = ApprovalService(database).list_case_approvals(case_id)
    except ApprovalServiceError as error:
        _raise_approval_error(error)
    return [_response(record) for record in records]


@router.post(
    "/actions/{action_id}/approval-requests",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_approval(
    action_id: str, payload: ApprovalRequest, database: DatabaseDependency
) -> ApprovalResponse:
    try:
        record = ApprovalService(database).request(
            RequestApprovalCommand(
                action_id=action_id,
                expected_action_version=payload.expected_action_version,
                payload_sha256=payload.payload_sha256,
                summary=ApprovalSummary.from_input(payload.summary),
                actor=payload.actor,
                reason=payload.reason,
            )
        )
    except (ApprovalServiceError, ApprovalCommandValidationError) as error:
        _raise_approval_error(error)
    return _response(record)


@router.post("/actions/{action_id}/approve", response_model=ApprovalDecisionResponse)
def approve_action(
    action_id: str, payload: ApprovalDecisionRequest, database: DatabaseDependency
) -> ApprovalDecisionResponse:
    try:
        service = ApprovalService(database)
        approval, action = service.approve(_decision(action_id, payload))
    except (ApprovalServiceError, ApprovalCommandValidationError) as error:
        _raise_approval_error(error)
    return _decision_response(database, approval, action)


@router.post("/actions/{action_id}/reject", response_model=ApprovalResponse)
def reject_action(
    action_id: str, payload: ApprovalDecisionRequest, database: DatabaseDependency
) -> ApprovalResponse:
    try:
        record = ApprovalService(database).reject(_decision(action_id, payload))
    except (ApprovalServiceError, ApprovalCommandValidationError) as error:
        _raise_approval_error(error)
    return _response(record)


@router.post("/actions/{action_id}/expire-approval", response_model=ApprovalResponse)
def expire_approval(
    action_id: str, payload: ApprovalDecisionRequest, database: DatabaseDependency
) -> ApprovalResponse:
    try:
        record = ApprovalService(database).expire(_decision(action_id, payload))
    except (ApprovalServiceError, ApprovalCommandValidationError) as error:
        _raise_approval_error(error)
    return _response(record)


def _decision(action_id: str, payload: ApprovalDecisionRequest) -> ApprovalDecisionCommand:
    return ApprovalDecisionCommand(
        approval_id=payload.approval_id,
        action_id=action_id,
        expected_approval_version=payload.expected_approval_version,
        expected_action_version=payload.expected_action_version,
        actor=payload.actor,
        reason=payload.reason,
    )


def _decision_response(
    database: DatabaseDependency,
    approval: ApprovalRecord,
    action: ActionRecord,
) -> ApprovalDecisionResponse:
    position = ActionService(database).get_topological_position(action.id)
    return ApprovalDecisionResponse(
        approval=_response(approval),
        action=action_response(action, position),
    )


def _response(record: ApprovalRecord) -> ApprovalResponse:
    return ApprovalResponse(
        id=record.id,
        action_id=record.action_id,
        case_id=record.case_id,
        action_version=record.action_version,
        payload_sha256=record.payload_sha256,
        summary=record.summary.text,
        summary_sha256=record.summary.sha256,
        status=record.status,
        version=record.version,
        requested_by=record.requested_by,
        requested_reason=record.requested_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _raise_approval_error(error: Exception) -> NoReturn:
    if isinstance(error, ApprovalCommandValidationError):
        raise ApiError(
            status_code=422,
            code="approval_invalid",
            message="Approval request is invalid",
        ) from error
    if isinstance(error, ApprovalCaseNotFoundError):
        raise ApiError(
            status_code=404, code="case_not_found", message="Case was not found"
        ) from error
    if isinstance(error, ApprovalActionNotFoundError):
        raise ApiError(
            status_code=404, code="action_not_found", message="Action was not found"
        ) from error
    if isinstance(error, ApprovalNotFoundError):
        raise ApiError(
            status_code=404,
            code="approval_not_found",
            message="Approval was not found",
        ) from error
    if isinstance(error, ApprovalActionVersionConflictError):
        raise ApiError(
            status_code=409,
            code="approval_action_version_conflict",
            message="Action version has changed",
            details={"current_version": error.current_version},
        ) from error
    if isinstance(error, ApprovalVersionConflictError):
        raise ApiError(
            status_code=409,
            code="approval_version_conflict",
            message="Approval version has changed",
            details={"current_version": error.current_version},
        ) from error
    if isinstance(error, ApprovalAlreadyActiveError):
        raise ApiError(
            status_code=409,
            code="approval_already_active",
            message="An active Approval already exists",
        ) from error
    if isinstance(error, ApprovalActionInvalidError):
        raise ApiError(
            status_code=409,
            code="approval_action_invalid",
            message="Action is not eligible for this Approval",
        ) from error
    if isinstance(error, ApprovalTransitionNotAllowedError):
        raise ApiError(
            status_code=409,
            code="approval_transition_not_allowed",
            message="Approval transition is not allowed",
        ) from error
    if isinstance(error, ApprovalIntegrityError):
        raise ApiError(
            status_code=409,
            code="approval_integrity_failed",
            message="Approval authority is inconsistent",
        ) from error
    if isinstance(error, ApprovalPersistenceError):
        raise ApiError(
            status_code=503,
            code="approval_storage_failed",
            message="Approval could not be read or saved",
            retryable=True,
        ) from error
    raise ApiError(
        status_code=503,
        code="approval_unavailable",
        message="Approval service is unavailable",
    ) from error
