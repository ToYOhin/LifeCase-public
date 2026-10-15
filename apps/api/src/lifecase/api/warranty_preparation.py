"""Thin HTTP boundary for explicitly saving a warranty preparation intent."""

from dataclasses import asdict
from typing import NoReturn

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.cases.preparation import (
    PreparationIntentCaseNotFoundError,
    PreparationIntentError,
    PreparationIntentService,
    PreparationIntentUnsupportedError,
    PreparationIntentValidationError,
    PreparationIntentVersionConflictError,
    validate_preparation_intent,
)

router = APIRouter()


class PreparationIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(max_length=2000, strict=True)
    expected_version: int = Field(ge=0, strict=True)

    @field_validator("intent")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return validate_preparation_intent(value)


class PreparationIntentResponse(BaseModel):
    case_id: str
    intent: str = Field(max_length=2000)
    version: int = Field(ge=0)
    updated_at: str | None


@router.get("/cases/{case_id}/preparation-intent", response_model=PreparationIntentResponse)
def get_preparation_intent(
    case_id: str, database: DatabaseDependency, response: Response
) -> PreparationIntentResponse:
    try:
        result = PreparationIntentService(database).get(case_id)
    except PreparationIntentError as error:
        _raise_api_error(error)
    response.headers["Cache-Control"] = "no-store"
    return PreparationIntentResponse(**asdict(result))


@router.put("/cases/{case_id}/preparation-intent", response_model=PreparationIntentResponse)
def save_preparation_intent(
    case_id: str,
    payload: PreparationIntentRequest,
    database: DatabaseDependency,
    response: Response,
) -> PreparationIntentResponse:
    try:
        result = PreparationIntentService(database).save(
            case_id, intent=payload.intent, expected_version=payload.expected_version
        )
    except PreparationIntentError as error:
        _raise_api_error(error)
    response.headers["Cache-Control"] = "no-store"
    return PreparationIntentResponse(**asdict(result))


def _raise_api_error(error: PreparationIntentError) -> NoReturn:
    if isinstance(error, PreparationIntentCaseNotFoundError):
        raise ApiError(
            status_code=404, code="case_not_found", message="Case was not found"
        ) from error
    if isinstance(error, PreparationIntentUnsupportedError):
        raise ApiError(
            status_code=422,
            code="preparation_intent_unsupported",
            message="Preparation intent is supported for warranty Cases only",
        ) from error
    if isinstance(error, PreparationIntentVersionConflictError):
        raise ApiError(
            status_code=409,
            code="preparation_intent_version_conflict",
            message="Preparation intent has changed; reload before saving again",
            details={"current_version": error.current_version},
        ) from error
    if isinstance(error, PreparationIntentValidationError):
        raise ApiError(
            status_code=422, code="validation_error", message="Preparation intent is invalid"
        ) from error
    raise ApiError(
        status_code=503,
        code="preparation_intent_unavailable",
        message="Preparation intent could not be read or saved",
        retryable=True,
    ) from error
