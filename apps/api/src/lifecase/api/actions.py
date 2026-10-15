"""Thin HTTP contracts for Case-scoped Action authority."""

from typing import NoReturn

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from lifecase.actions import (
    ActionCaseNotFoundError,
    ActionCommandValidationError,
    ActionCompletionEvidenceInvalidError,
    ActionDeadlineInvalidError,
    ActionDependencyInvalidError,
    ActionDependencyStaleError,
    ActionIntegrityError,
    ActionKind,
    ActionNotFoundError,
    ActionPayload,
    ActionPersistenceError,
    ActionRecord,
    ActionRequirementInvalidError,
    ActionRequirementStaleError,
    ActionRiskLevel,
    ActionService,
    ActionServiceError,
    ActionStatus,
    ActionStatusCommand,
    ActionTargetInvalidError,
    ActionTransitionNotAllowedError,
    ActionVersionConflictError,
    ClaimRequirementInput,
    CompleteActionCommand,
    CompletionAuthorityType,
    CompletionEvidenceInput,
    CreateActionCommand,
    DeadlineLinkInput,
    DependencyInput,
    ReviseActionCommand,
)
from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError

router = APIRouter()


class ActionRequirementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=200)
    expected_claim_version: int = Field(gt=0, strict=True)
    value_sha256: str = Field(min_length=64, max_length=64)
    field: str = Field(min_length=1, max_length=64)


class ActionDependencyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1, max_length=200)
    expected_action_version: int = Field(gt=0, strict=True)


class ActionDeadlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deadline_id: str = Field(min_length=1, max_length=200)
    expected_deadline_version: int = Field(gt=0, strict=True)
    definition_sha256: str = Field(min_length=64, max_length=64)


class ActionDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    risk_level: ActionRiskLevel
    payload: dict[str, object]
    requirements: list[ActionRequirementRequest] = Field(default_factory=list, max_length=32)
    dependencies: list[ActionDependencyRequest] = Field(default_factory=list, max_length=32)
    deadline: ActionDeadlineRequest | None = None
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class ReviseActionRequest(ActionDefinitionRequest):
    expected_version: int = Field(gt=0, strict=True)


class ActionStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(gt=0, strict=True)
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class CompletionEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authority_type: CompletionAuthorityType
    authority_id: str = Field(min_length=1, max_length=200)
    expected_version: int | None = Field(default=None, gt=0, strict=True)
    authority_sha256: str = Field(min_length=64, max_length=64)


class CompleteActionRequest(ActionStatusRequest):
    evidence: list[CompletionEvidenceRequest] = Field(min_length=1, max_length=32)


class ActionBlockerResponse(BaseModel):
    code: str
    field: str | None
    authority_id: str | None


class ActionRequirementResponse(BaseModel):
    claim_id: str
    claim_version: int
    value_sha256: str
    field: str


class ActionDependencyResponse(BaseModel):
    action_id: str
    action_version: int
    status: ActionStatus


class ActionDeadlineResponse(BaseModel):
    deadline_id: str
    deadline_version: int
    definition_sha256: str
    date_kind: str
    due_value: str
    timezone: str
    status: str


class CompletionEvidenceResponse(BaseModel):
    authority_type: CompletionAuthorityType
    authority_id: str
    authority_version: int | None
    authority_sha256: str


class ActionResponse(BaseModel):
    id: str
    case_id: str
    kind: ActionKind
    status: ActionStatus
    risk_level: ActionRiskLevel
    payload: dict[str, str]
    payload_sha256: str
    blockers: list[ActionBlockerResponse]
    blockers_sha256: str
    requirements: list[ActionRequirementResponse]
    dependencies: list[ActionDependencyResponse]
    deadline: ActionDeadlineResponse | None
    completion_evidence: list[CompletionEvidenceResponse]
    communication_draft: str | None
    topological_position: int
    version: int
    created_at: str
    updated_at: str
    analysis_basis_status: str | None = None


@router.get("/cases/{case_id}/actions", response_model=list[ActionResponse])
def list_actions(case_id: str, database: DatabaseDependency) -> list[ActionResponse]:
    try:
        records = ActionService(database).list_case_actions(case_id)
    except ActionServiceError as error:
        _raise_action_error(error)
    return [action_response(record, position) for position, record in enumerate(records)]


@router.post(
    "/cases/{case_id}/actions",
    response_model=ActionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_action(
    case_id: str, payload: ActionDefinitionRequest, database: DatabaseDependency
) -> ActionResponse:
    try:
        service = ActionService(database)
        record = service.create_action(_definition(case_id, payload))
        position = _position(service, record)
    except (ActionServiceError, ActionCommandValidationError) as error:
        _raise_action_error(error)
    return action_response(record, position)


@router.post("/actions/{action_id}/revise", response_model=ActionResponse)
def revise_action(
    action_id: str, payload: ReviseActionRequest, database: DatabaseDependency
) -> ActionResponse:
    try:
        service = ActionService(database)
        definition = _definition(service.get_action_case_id(action_id), payload)
        record = service.revise(
            ReviseActionCommand(
                action_id=action_id,
                expected_version=payload.expected_version,
                definition=definition,
            )
        )
        position = _position(service, record)
    except (ActionServiceError, ActionCommandValidationError) as error:
        _raise_action_error(error)
    return action_response(record, position)


@router.post("/actions/{action_id}/revalidate", response_model=ActionResponse)
def revalidate_action(
    action_id: str, payload: ActionStatusRequest, database: DatabaseDependency
) -> ActionResponse:
    return _status_change(database, action_id, payload, "revalidate")


@router.post("/actions/{action_id}/start", response_model=ActionResponse)
def start_action(
    action_id: str, payload: ActionStatusRequest, database: DatabaseDependency
) -> ActionResponse:
    return _status_change(database, action_id, payload, "start")


@router.post("/actions/{action_id}/fail", response_model=ActionResponse)
def fail_action(
    action_id: str, payload: ActionStatusRequest, database: DatabaseDependency
) -> ActionResponse:
    return _status_change(database, action_id, payload, "fail")


@router.post("/actions/{action_id}/cancel", response_model=ActionResponse)
def cancel_action(
    action_id: str, payload: ActionStatusRequest, database: DatabaseDependency
) -> ActionResponse:
    return _status_change(database, action_id, payload, "cancel")


@router.post("/actions/{action_id}/complete", response_model=ActionResponse)
def complete_action(
    action_id: str, payload: CompleteActionRequest, database: DatabaseDependency
) -> ActionResponse:
    try:
        service = ActionService(database)
        record = service.complete(
            CompleteActionCommand(
                action_id=action_id,
                expected_version=payload.expected_version,
                evidence=tuple(_completion_evidence(item) for item in payload.evidence),
                actor=payload.actor,
                reason=payload.reason,
            )
        )
        position = _position(service, record)
    except (ActionServiceError, ActionCommandValidationError) as error:
        _raise_action_error(error)
    return action_response(record, position)


def _status_change(
    database: DatabaseDependency,
    action_id: str,
    payload: ActionStatusRequest,
    operation: str,
) -> ActionResponse:
    try:
        service = ActionService(database)
        command = ActionStatusCommand(
            action_id=action_id,
            expected_version=payload.expected_version,
            actor=payload.actor,
            reason=payload.reason,
        )
        if operation == "revalidate":
            record = service.revalidate(command)
        elif operation == "start":
            record = service.start(command)
        elif operation == "fail":
            record = service.fail(command)
        else:
            record = service.cancel(command)
        position = _position(service, record)
    except (ActionServiceError, ActionCommandValidationError) as error:
        _raise_action_error(error)
    return action_response(record, position)


def _definition(case_id: str, payload: ActionDefinitionRequest) -> CreateActionCommand:
    return CreateActionCommand(
        case_id=case_id,
        kind=payload.kind,
        risk_level=payload.risk_level,
        payload=ActionPayload.from_input(payload.kind, payload.payload),
        requirements=tuple(
            ClaimRequirementInput(
                item.claim_id,
                item.expected_claim_version,
                item.value_sha256,
                item.field,
            )
            for item in payload.requirements
        ),
        dependencies=tuple(
            DependencyInput(item.action_id, item.expected_action_version)
            for item in payload.dependencies
        ),
        deadline=(
            DeadlineLinkInput(
                payload.deadline.deadline_id,
                payload.deadline.expected_deadline_version,
                payload.deadline.definition_sha256,
            )
            if payload.deadline is not None
            else None
        ),
        actor=payload.actor,
        reason=payload.reason,
    )


def _completion_evidence(payload: CompletionEvidenceRequest) -> CompletionEvidenceInput:
    return CompletionEvidenceInput(
        authority_type=payload.authority_type,
        authority_id=payload.authority_id,
        expected_version=payload.expected_version,
        authority_sha256=payload.authority_sha256,
    )


def _position(service: ActionService, record: ActionRecord) -> int:
    return service.get_topological_position(record.id)


def action_response(record: ActionRecord, position: int) -> ActionResponse:
    return ActionResponse(
        id=record.id,
        case_id=record.case_id,
        kind=record.kind,
        status=record.status,
        risk_level=record.risk_level,
        payload=record.payload.to_dict(),
        payload_sha256=record.payload.sha256,
        analysis_basis_status=record.analysis_basis_status,
        blockers=[
            ActionBlockerResponse(
                code=item.code.value,
                field=item.field,
                authority_id=item.authority_id,
            )
            for item in record.blockers
        ],
        blockers_sha256=record.blockers_sha256,
        requirements=[
            ActionRequirementResponse(
                claim_id=item.claim_id,
                claim_version=item.claim_version,
                value_sha256=item.value_sha256,
                field=item.field,
            )
            for item in record.requirements
        ],
        dependencies=[
            ActionDependencyResponse(
                action_id=item.action_id,
                action_version=item.action_version,
                status=item.status,
            )
            for item in record.dependencies
        ],
        deadline=(
            ActionDeadlineResponse(
                deadline_id=record.deadline.deadline_id,
                deadline_version=record.deadline.deadline_version,
                definition_sha256=record.deadline.definition_sha256,
                date_kind=record.deadline.date_kind,
                due_value=record.deadline.due_value,
                timezone=record.deadline.timezone,
                status=record.deadline.status,
            )
            if record.deadline is not None
            else None
        ),
        completion_evidence=[
            CompletionEvidenceResponse(
                authority_type=item.authority_type,
                authority_id=item.authority_id,
                authority_version=item.authority_version,
                authority_sha256=item.authority_sha256,
            )
            for item in record.completion_evidence
        ],
        communication_draft=record.communication_draft,
        topological_position=position,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _raise_action_error(error: Exception) -> NoReturn:
    if isinstance(error, ActionCommandValidationError):
        raise ApiError(
            status_code=422, code="action_invalid", message="Action request is invalid"
        ) from error
    if isinstance(error, ActionCaseNotFoundError):
        raise ApiError(
            status_code=404, code="case_not_found", message="Case was not found"
        ) from error
    if isinstance(error, ActionNotFoundError):
        raise ApiError(
            status_code=404, code="action_not_found", message="Action was not found"
        ) from error
    if isinstance(error, ActionVersionConflictError):
        raise ApiError(
            status_code=409,
            code="action_version_conflict",
            message="Action version has changed",
            details={"current_version": error.current_version},
        ) from error
    if isinstance(error, ActionRequirementStaleError):
        raise ApiError(
            status_code=409, code="action_claim_stale", message="Action Claim has changed"
        ) from error
    if isinstance(error, ActionRequirementInvalidError):
        raise ApiError(
            status_code=409,
            code="action_claim_invalid",
            message="Action Claim is not authoritative",
        ) from error
    if isinstance(error, ActionDependencyStaleError):
        raise ApiError(
            status_code=409,
            code="action_dependency_stale",
            message="Action dependency has changed",
        ) from error
    if isinstance(error, ActionDependencyInvalidError):
        raise ApiError(
            status_code=409,
            code="action_dependency_invalid",
            message="Action dependency is invalid",
        ) from error
    if isinstance(error, ActionDeadlineInvalidError):
        raise ApiError(
            status_code=409,
            code="action_deadline_invalid",
            message="Action Deadline is unavailable or stale",
        ) from error
    if isinstance(error, ActionTargetInvalidError):
        raise ApiError(
            status_code=409,
            code="action_target_invalid",
            message="Collection target is not currently missing",
        ) from error
    if isinstance(error, ActionCompletionEvidenceInvalidError):
        raise ApiError(
            status_code=409,
            code="action_completion_evidence_invalid",
            message="Completion evidence is unavailable or stale",
        ) from error
    if isinstance(error, ActionTransitionNotAllowedError):
        raise ApiError(
            status_code=409,
            code="action_transition_not_allowed",
            message="Action transition is not allowed",
        ) from error
    if isinstance(error, ActionIntegrityError):
        raise ApiError(
            status_code=409,
            code="action_integrity_failed",
            message="Action authority is inconsistent",
        ) from error
    if isinstance(error, ActionPersistenceError):
        raise ApiError(
            status_code=503,
            code="action_storage_failed",
            message="Action could not be read or saved",
            retryable=True,
        ) from error
    raise ApiError(
        status_code=503,
        code="action_unavailable",
        message="Action service is unavailable",
    ) from error
