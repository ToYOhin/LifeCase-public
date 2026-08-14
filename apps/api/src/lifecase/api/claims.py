"""Thin HTTP contracts for M2-02 Claim decisions."""

from typing import NoReturn, cast

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.cases.service import CaseNotFoundError, CaseServiceError
from lifecase.claims.manual_service import ManualClaimService, ManualClaimValidationError
from lifecase.claims.models import (
    ClaimCommandValidationError,
    ClaimConfirmationBasis,
    ClaimCreatedBy,
    ClaimRecord,
    ClaimStatus,
    ClaimValue,
    ConfirmClaimCommand,
    CorrectClaimCommand,
    RejectClaimCommand,
)
from lifecase.claims.service import (
    ClaimCaseNotFoundError,
    ClaimNotFoundError,
    ClaimPersistenceError,
    ClaimService,
    ClaimServiceError,
    ClaimSourceInvalidError,
    ClaimSourceRequiredError,
    ClaimTransitionNotAllowedError,
    ClaimVersionConflictError,
)
from lifecase.templates.registry import TemplateRegistryError

router = APIRouter()
case_router = APIRouter()


class ClaimDecisionRequest(BaseModel):
    """Shared optimistic metadata for confirm and reject commands."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(gt=0, strict=True)
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class ClaimConfirmRequest(ClaimDecisionRequest):
    """Evidence-backed or explicit-user confirmation request."""

    basis: ClaimConfirmationBasis


class ClaimCorrectRequest(ClaimDecisionRequest):
    """Correction request retaining the authoritative prior value."""

    value: JsonValue


class ManualClaimRequest(BaseModel):
    """One user-selected fact and its exact supporting evidence quote."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    value: JsonValue
    segment_id: str = Field(min_length=1, max_length=200)
    quote_start: int = Field(ge=0, strict=True)
    quote_end: int = Field(gt=0, strict=True)
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class ClaimResponse(BaseModel):
    """Durable authoritative Claim projection."""

    id: str
    case_id: str
    field: str
    value: JsonValue
    status: ClaimStatus
    confidence: float | None
    created_by: ClaimCreatedBy
    contradiction: bool
    version: int
    created_at: str
    updated_at: str


@case_router.post(
    "/cases/{case_id}/claims",
    response_model=ClaimResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_manual_claim(
    case_id: str,
    payload: ManualClaimRequest,
    database: DatabaseDependency,
) -> ClaimResponse:
    """Create one candidate Claim from an explicit evidence selection."""

    try:
        record = ManualClaimService(database).create_evidence_candidate(
            case_id=case_id,
            field=payload.field,
            value=ClaimValue.from_input(payload.value),
            segment_id=payload.segment_id,
            quote_start=payload.quote_start,
            quote_end=payload.quote_end,
            actor=payload.actor,
            reason=payload.reason,
        )
    except (CaseNotFoundError, ClaimCaseNotFoundError) as error:
        raise ApiError(
            status_code=404,
            code="case_not_found",
            message="Case was not found",
        ) from error
    except ManualClaimValidationError as error:
        raise ApiError(
            status_code=422,
            code="claim_invalid",
            message="Claim request is invalid",
        ) from error
    except ClaimSourceInvalidError as error:
        _raise_claim_api_error(error)
    except (ClaimCommandValidationError, ClaimServiceError) as error:
        _raise_claim_api_error(error)
    except (CaseServiceError, TemplateRegistryError) as error:
        raise ApiError(
            status_code=503,
            code="claim_storage_failed",
            message="Claim operation could not be completed",
            retryable=True,
        ) from error
    return _claim_response(record)


@router.post("/{claim_id}/confirm", response_model=ClaimResponse)
def confirm_claim(
    claim_id: str,
    payload: ClaimConfirmRequest,
    database: DatabaseDependency,
) -> ClaimResponse:
    """Confirm one Claim through the domain service."""

    try:
        record = ClaimService(database).confirm_claim(
            ConfirmClaimCommand(
                claim_id=claim_id,
                expected_version=payload.expected_version,
                basis=payload.basis,
                actor=payload.actor,
                reason=payload.reason,
            )
        )
    except (ClaimCommandValidationError, ClaimServiceError) as error:
        _raise_claim_api_error(error)
    return _claim_response(record)


@router.post("/{claim_id}/correct", response_model=ClaimResponse)
def correct_claim(
    claim_id: str,
    payload: ClaimCorrectRequest,
    database: DatabaseDependency,
) -> ClaimResponse:
    """Correct one Claim through the domain service."""

    try:
        record = ClaimService(database).correct_claim(
            CorrectClaimCommand(
                claim_id=claim_id,
                expected_version=payload.expected_version,
                value=ClaimValue.from_input(payload.value),
                actor=payload.actor,
                reason=payload.reason,
            )
        )
    except (ClaimCommandValidationError, ClaimServiceError) as error:
        _raise_claim_api_error(error)
    return _claim_response(record)


@router.post("/{claim_id}/reject", response_model=ClaimResponse)
def reject_claim(
    claim_id: str,
    payload: ClaimDecisionRequest,
    database: DatabaseDependency,
) -> ClaimResponse:
    """Reject one Claim through the domain service."""

    try:
        record = ClaimService(database).reject_claim(
            RejectClaimCommand(
                claim_id=claim_id,
                expected_version=payload.expected_version,
                actor=payload.actor,
                reason=payload.reason,
            )
        )
    except (ClaimCommandValidationError, ClaimServiceError) as error:
        _raise_claim_api_error(error)
    return _claim_response(record)


def _claim_response(record: ClaimRecord) -> ClaimResponse:
    return ClaimResponse(
        id=record.id,
        case_id=record.case_id,
        field=record.field,
        value=cast(JsonValue, record.value.to_python()),
        status=record.status,
        confidence=record.confidence,
        created_by=record.created_by,
        contradiction=record.contradiction,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _raise_claim_api_error(error: Exception) -> NoReturn:
    if isinstance(error, ClaimCommandValidationError):
        raise ApiError(
            status_code=422,
            code="claim_invalid",
            message="Claim request is invalid",
        ) from error
    if isinstance(error, ClaimNotFoundError):
        raise ApiError(
            status_code=404,
            code="claim_not_found",
            message="Claim was not found",
        ) from error
    if isinstance(error, ClaimVersionConflictError):
        raise ApiError(
            status_code=409,
            code="claim_version_conflict",
            message="Claim version has changed",
            details={"current_version": error.current_version},
        ) from error
    if isinstance(error, ClaimSourceRequiredError):
        raise ApiError(
            status_code=409,
            code="claim_source_required",
            message="Claim confirmation requires evidence or explicit user confirmation",
        ) from error
    if isinstance(error, ClaimSourceInvalidError):
        raise ApiError(
            status_code=409,
            code="claim_source_invalid",
            message="Claim source is invalid",
        ) from error
    if isinstance(error, ClaimTransitionNotAllowedError):
        raise ApiError(
            status_code=409,
            code="claim_transition_not_allowed",
            message="Claim operation is not allowed",
        ) from error
    if isinstance(error, ClaimPersistenceError):
        raise ApiError(
            status_code=503,
            code="claim_storage_failed",
            message="Claim operation could not be completed",
            retryable=True,
        ) from error
    raise ApiError(
        status_code=422,
        code="claim_invalid",
        message="Claim request is invalid",
    ) from error
