"""Explicit human progress notes for one Case-scoped repair preparation Action."""

from dataclasses import asdict
from typing import NoReturn

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from lifecase.actions.models import ActionCommandValidationError
from lifecase.actions.progress import (
    ActionProgressConflictError,
    ActionProgressService,
    ActionProgressView,
    ProgressKind,
    RecordActionProgressCommand,
    validate_progress_text,
)
from lifecase.actions.service import (
    ActionCaseNotFoundError,
    ActionIntegrityError,
    ActionNotFoundError,
    ActionServiceError,
    ActionTransitionNotAllowedError,
    ActionVersionConflictError,
)
from lifecase.api.actions import ActionResponse, action_response
from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError

router = APIRouter()


class ActionProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(gt=0, strict=True)
    expected_last_progress_id: str | None = Field(min_length=1, max_length=200, strict=True)
    kind: ProgressKind
    text: str = Field(min_length=1, max_length=2000, strict=True)
    actor: str = Field(min_length=1, max_length=120, strict=True)
    reason: str = Field(min_length=1, max_length=1000, strict=True)

    @field_validator("text")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return validate_progress_text(value)


class ActionProgressEntryResponse(BaseModel):
    id: str
    action_id: str
    case_id: str
    action_version: int
    kind: ProgressKind
    text: str
    actor: str
    reason: str
    created_at: str


class ActionProgressResponse(BaseModel):
    action: ActionResponse
    entries: list[ActionProgressEntryResponse]


@router.get("/cases/{case_id}/actions/{action_id}/progress", response_model=ActionProgressResponse)
def read_progress(
    case_id: str, action_id: str, database: DatabaseDependency, response: Response
) -> ActionProgressResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return _response(ActionProgressService(database).read(case_id, action_id))
    except ActionServiceError as error:
        _raise_error(error)


@router.post(
    "/cases/{case_id}/actions/{action_id}/progress",
    response_model=ActionProgressResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_progress(
    case_id: str,
    action_id: str,
    payload: ActionProgressRequest,
    database: DatabaseDependency,
    response: Response,
) -> ActionProgressResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        command = RecordActionProgressCommand(
            case_id=case_id, action_id=action_id, **payload.model_dump()
        )
        return _response(ActionProgressService(database).append(command))
    except ActionCommandValidationError as error:
        raise ApiError(
            status_code=422,
            code="action_progress_invalid",
            message="Human progress metadata or text is invalid",
        ) from error
    except ActionServiceError as error:
        _raise_error(error)


def _response(view: ActionProgressView) -> ActionProgressResponse:
    return ActionProgressResponse(
        action=action_response(view.action, view.position),
        entries=[ActionProgressEntryResponse(**asdict(entry)) for entry in view.entries],
    )


def _raise_error(error: ActionServiceError) -> NoReturn:
    if isinstance(error, (ActionNotFoundError, ActionCaseNotFoundError)):
        raise ApiError(
            status_code=404,
            code="action_progress_not_found",
            message="Repair Action was not found in this Case",
        ) from error
    if isinstance(error, (ActionVersionConflictError, ActionProgressConflictError)):
        raise ApiError(
            status_code=409,
            code="action_progress_conflict",
            message="Action or human progress changed; read again before saving",
        ) from error
    if isinstance(error, (ActionIntegrityError, ActionTransitionNotAllowedError)):
        raise ApiError(
            status_code=409,
            code="action_progress_stale",
            message="Repair Action is closed or its supporting analysis is unavailable or changed",
        ) from error
    raise ApiError(
        status_code=503,
        code="action_progress_unavailable",
        message="Human progress could not be read or saved",
        retryable=True,
    ) from error
