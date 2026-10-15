"""Explicit review and save boundary for a question-linked repair Action."""

import sqlite3
from typing import NoReturn, Self

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lifecase.actions.analysis_draft import (
    AnalysisActionDraftService,
    AnalysisDraftConflictError,
    AnalysisDraftSaveCommand,
    AnalysisDraftStaleError,
)
from lifecase.actions.models import (
    ActionCommandValidationError,
    ActionStatus,
    validate_analysis_draft_text,
)
from lifecase.actions.service import (
    ActionIntegrityError,
    ActionServiceError,
    ActionTransitionNotAllowedError,
    ActionVersionConflictError,
)
from lifecase.agent.errors import AgentCaseNotFoundError, AgentRunNotFoundError, AgentServiceError
from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.cases.service import CaseNotFoundError, CaseServiceError
from lifecase.tools import ToolError

router = APIRouter()


class AnalysisActionSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_run_id: str = Field(min_length=1, max_length=200, strict=True)
    expected_run_version: int = Field(gt=0, strict=True)
    basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    draft_text: str = Field(min_length=1, max_length=500, strict=True)
    actor: str = Field(min_length=1, max_length=120, strict=True)
    reason: str = Field(min_length=1, max_length=1000, strict=True)
    expected_action_id: str | None = Field(default=None, min_length=1, max_length=200, strict=True)
    expected_action_version: int | None = Field(default=None, gt=0, strict=True)

    @field_validator("draft_text")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return validate_analysis_draft_text(value)

    @model_validator(mode="after")
    def paired_action(self) -> Self:
        if (self.expected_action_id is None) != (self.expected_action_version is None):
            raise ValueError("Action identity and version must be supplied together")
        return self


class SavedAnalysisActionResponse(BaseModel):
    id: str
    version: int
    draft_text: str
    status: ActionStatus


class AnalysisActionDraftResponse(BaseModel):
    case_id: str
    run_id: str
    run_version: int
    question: str
    basis_sha256: str
    answer_sha256: str
    draft_text: str
    saved_action: SavedAnalysisActionResponse | None


_ERRORS = (
    ActionServiceError,
    AgentServiceError,
    CaseServiceError,
    ToolError,
    sqlite3.Error,
    ValueError,
    TypeError,
    KeyError,
)


@router.get("/cases/{case_id}/analysis-action-draft", response_model=AnalysisActionDraftResponse)
def get_analysis_draft(
    case_id: str,
    database: DatabaseDependency,
    response: Response,
    run_id: str = Query(min_length=1, max_length=200),
) -> AnalysisActionDraftResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return AnalysisActionDraftResponse.model_validate(
            AnalysisActionDraftService(database).preview(case_id, run_id)
        )
    except _ERRORS as error:
        _raise_error(error)


@router.post("/cases/{case_id}/analysis-action-draft", response_model=AnalysisActionDraftResponse)
def save_analysis_draft(
    case_id: str,
    payload: AnalysisActionSaveRequest,
    database: DatabaseDependency,
    response: Response,
) -> AnalysisActionDraftResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        command = AnalysisDraftSaveCommand(case_id=case_id, **payload.model_dump())
        return AnalysisActionDraftResponse.model_validate(
            AnalysisActionDraftService(database).save(command)
        )
    except _ERRORS as error:
        _raise_error(error)


def _raise_error(error: Exception) -> NoReturn:
    if isinstance(error, (AgentCaseNotFoundError, AgentRunNotFoundError, CaseNotFoundError)):
        raise ApiError(
            status_code=404, code="analysis_action_not_found", message="Analysis was not found"
        ) from error
    if isinstance(error, ActionCommandValidationError):
        raise ApiError(
            status_code=422,
            code="analysis_action_invalid",
            message="Reviewed draft is invalid or exceeds its text budget",
        ) from error
    if isinstance(error, AnalysisDraftStaleError):
        raise ApiError(
            status_code=409,
            code="analysis_action_stale",
            message="Analysis question or supporting basis changed; analyze again",
        ) from error
    if isinstance(
        error,
        (AnalysisDraftConflictError, ActionVersionConflictError, ActionTransitionNotAllowedError),
    ):
        raise ApiError(
            status_code=409,
            code="analysis_action_conflict",
            message="Saved Action changed; reload before saving",
        ) from error
    if isinstance(error, ActionIntegrityError):
        raise ApiError(
            status_code=409,
            code="analysis_action_stale",
            message="Analysis authority is unavailable; reload before saving",
        ) from error
    raise ApiError(
        status_code=503,
        code="analysis_action_unavailable",
        message="Draft could not be read or saved",
        retryable=True,
    ) from error
