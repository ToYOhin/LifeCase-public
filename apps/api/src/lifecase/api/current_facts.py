"""Thin read-only HTTP projection for a current communication preparation sheet."""

from dataclasses import asdict
from typing import NoReturn, cast

from fastapi import APIRouter, Response
from pydantic import BaseModel, JsonValue

from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.api.reviews import ReviewCitationResponse, ReviewClaimResponse
from lifecase.claims.current_facts import (
    ConfirmationBasis,
    CurrentFact,
    CurrentFactsCaseNotFoundError,
    CurrentFactsError,
    CurrentFactsService,
    CurrentFactsValidationError,
    EvidenceStatus,
)

router = APIRouter()


class CurrentFactConfirmationResponse(BaseModel):
    actor: str
    reason: str
    occurred_at: str


class CurrentFactResponse(BaseModel):
    claim: ReviewClaimResponse
    value_sha256: str
    confirmation_basis: ConfirmationBasis | None
    confirmation: CurrentFactConfirmationResponse | None
    evidence_status: EvidenceStatus
    citations: list[ReviewCitationResponse]
    conflict: bool
    usable_as_fact: bool


class CurrentFactsResponse(BaseModel):
    case_id: str
    case_type: str
    items: list[CurrentFactResponse]


@router.get("/cases/{case_id}/current-facts", response_model=CurrentFactsResponse)
def list_current_facts(
    case_id: str, database: DatabaseDependency, response: Response
) -> CurrentFactsResponse:
    """Return every current Claim with a freshly checked, Case-scoped basis."""

    try:
        result = CurrentFactsService(database).list_case_facts(case_id)
    except CurrentFactsError as error:
        _raise_api_error(error)
    response.headers["Cache-Control"] = "no-store"
    return CurrentFactsResponse(
        case_id=result.case_id,
        case_type=result.case_type,
        items=[_item_response(item) for item in result.items],
    )


def _item_response(item: CurrentFact) -> CurrentFactResponse:
    claim = item.claim
    return CurrentFactResponse(
        value_sha256=claim.value.sha256,
        claim=ReviewClaimResponse(
            id=claim.id,
            case_id=claim.case_id,
            field=claim.field,
            value=cast(JsonValue, claim.value.to_python()),
            status=claim.status,
            confidence=claim.confidence,
            created_by=claim.created_by,
            contradiction=claim.contradiction,
            version=claim.version,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
        ),
        confirmation_basis=item.confirmation_basis,
        confirmation=(
            CurrentFactConfirmationResponse(**asdict(item.confirmation))
            if item.confirmation is not None
            else None
        ),
        evidence_status=item.evidence_status,
        citations=[ReviewCitationResponse(**asdict(citation)) for citation in item.citations],
        conflict=item.conflict,
        usable_as_fact=item.usable_as_fact,
    )


def _raise_api_error(error: CurrentFactsError) -> NoReturn:
    if isinstance(error, CurrentFactsValidationError):
        raise ApiError(
            status_code=422,
            code="current_facts_invalid",
            message="Current facts request is invalid",
        ) from error
    if isinstance(error, CurrentFactsCaseNotFoundError):
        raise ApiError(
            status_code=404, code="case_not_found", message="Case was not found"
        ) from error
    raise ApiError(
        status_code=503,
        code="current_facts_unavailable",
        message="Current facts could not be read",
        retryable=True,
    ) from error
