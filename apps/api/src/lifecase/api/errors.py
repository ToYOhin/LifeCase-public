"""Stable safe HTTP error contract."""

from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, JsonValue


class ApiErrorDetailResponse(BaseModel):
    """Generated-OpenAPI model for the existing safe error detail."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, JsonValue]
    retryable: bool
    request_id: str


class ApiErrorResponse(BaseModel):
    """Generated-OpenAPI model for the existing safe error envelope."""

    model_config = ConfigDict(extra="forbid")

    error: ApiErrorDetailResponse


class ApiError(RuntimeError):
    """Typed transport failure with a safe public payload."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable


def install_error_handlers(application: FastAPI) -> None:
    """Install handlers that never serialize exception internals or request bodies."""

    @application.exception_handler(ApiError)
    async def handle_api_error(_request: Request, error: ApiError) -> JSONResponse:
        return _error_response(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            details=error.details,
            retryable=error.retryable,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "location": ".".join(str(part) for part in item["loc"]),
                "message": str(item["msg"]),
                "type": str(item["type"]),
            }
            for item in error.errors()
        ]
        return _error_response(
            status_code=422,
            code="validation_error",
            message="Request validation failed",
            details={"fields": fields},
            retryable=False,
        )


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any],
    retryable: bool,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "retryable": retryable,
                "request_id": f"req_{uuid4().hex}",
            }
        },
    )
