"""Thin HTTP contract for Case-scoped Evidence Review."""

from typing import NoReturn, cast

from fastapi import APIRouter
from pydantic import BaseModel, JsonValue

from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.claims import (
    ClaimCreatedBy,
    ClaimSourceRelation,
    ClaimStatus,
    EvidenceReviewCaseNotFoundError,
    EvidenceReviewConfigurationError,
    EvidenceReviewError,
    EvidenceReviewIntegrityError,
    EvidenceReviewItem,
    EvidenceReviewPersistenceError,
    EvidenceReviewResult,
    EvidenceReviewService,
    EvidenceReviewValidationError,
    ReviewCitation,
)
from lifecase.claims.models import ClaimRecord

router = APIRouter()


class ReviewClaimResponse(BaseModel):
    """Authoritative Claim value shown in the review surface."""

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


class ReviewCitationResponse(BaseModel):
    """Case-scoped current Segment and quote authority."""

    claim_id: str
    case_id: str
    source_id: str
    source_name: str
    segment_id: str
    sequence: int
    page: int | None
    text_sha256: str
    text: str
    quote_start: int
    quote_end: int
    quote_hash: str
    quote_text: str
    relation: ClaimSourceRelation
    attached_by: str
    attached_at: str


class EvidenceReviewItemResponse(BaseModel):
    """One active Claim and the authority needed to decide it."""

    claim: ReviewClaimResponse
    citations: list[ReviewCitationResponse]
    settled_claims: list[ReviewClaimResponse]
    is_next: bool


class EvidenceReviewResponse(BaseModel):
    """Deterministic M4-02 queue for one Case."""

    case_id: str
    next_claim_id: str | None
    items: list[EvidenceReviewItemResponse]


@router.get("/cases/{case_id}/evidence-review", response_model=EvidenceReviewResponse)
def list_evidence_review(
    case_id: str,
    database: DatabaseDependency,
) -> EvidenceReviewResponse:
    """Return current active Claims with freshly validated evidence."""

    try:
        result = EvidenceReviewService(database).list_case_review(case_id)
    except EvidenceReviewError as error:
        _raise_review_api_error(error)
    return _review_response(result)


def _review_response(result: EvidenceReviewResult) -> EvidenceReviewResponse:
    return EvidenceReviewResponse(
        case_id=result.case_id,
        next_claim_id=result.next_claim_id,
        items=[_review_item_response(item) for item in result.items],
    )


def _review_item_response(item: EvidenceReviewItem) -> EvidenceReviewItemResponse:
    return EvidenceReviewItemResponse(
        claim=_claim_response(item.claim),
        citations=[_citation_response(citation) for citation in item.citations],
        settled_claims=[_claim_response(claim) for claim in item.settled_claims],
        is_next=item.is_next,
    )


def _claim_response(claim: ClaimRecord) -> ReviewClaimResponse:
    return ReviewClaimResponse(
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
    )


def _citation_response(citation: ReviewCitation) -> ReviewCitationResponse:
    return ReviewCitationResponse(
        claim_id=citation.claim_id,
        case_id=citation.case_id,
        source_id=citation.source_id,
        source_name=citation.source_name,
        segment_id=citation.segment_id,
        sequence=citation.sequence,
        page=citation.page,
        text_sha256=citation.text_sha256,
        text=citation.text,
        quote_start=citation.quote_start,
        quote_end=citation.quote_end,
        quote_hash=citation.quote_hash,
        quote_text=citation.quote_text,
        relation=citation.relation,
        attached_by=citation.attached_by,
        attached_at=citation.attached_at,
    )


def _raise_review_api_error(error: EvidenceReviewError) -> NoReturn:
    if isinstance(error, EvidenceReviewValidationError):
        raise ApiError(
            status_code=422,
            code="evidence_review_invalid",
            message="Evidence Review request is invalid",
        ) from error
    if isinstance(error, EvidenceReviewCaseNotFoundError):
        raise ApiError(
            status_code=404,
            code="case_not_found",
            message="Case was not found",
        ) from error
    if isinstance(error, EvidenceReviewIntegrityError):
        raise ApiError(
            status_code=409,
            code="evidence_review_integrity_failed",
            message="Evidence Review authority is no longer current",
        ) from error
    if isinstance(error, EvidenceReviewConfigurationError):
        raise ApiError(
            status_code=503,
            code="evidence_review_unavailable",
            message="Evidence Review is unavailable",
        ) from error
    if isinstance(error, EvidenceReviewPersistenceError):
        raise ApiError(
            status_code=503,
            code="evidence_review_unavailable",
            message="Evidence Review could not be read",
            retryable=True,
        ) from error
    raise ApiError(
        status_code=503,
        code="evidence_review_unavailable",
        message="Evidence Review is unavailable",
    ) from error
