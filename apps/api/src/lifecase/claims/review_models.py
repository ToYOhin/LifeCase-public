"""Immutable Case-scoped Evidence Review projections."""

from dataclasses import dataclass

from lifecase.claims.models import ClaimRecord, ClaimSourceRelation


@dataclass(frozen=True, slots=True)
class ReviewCitation:
    """Authoritative Segment text joined to validated ClaimSource metadata."""

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


@dataclass(frozen=True, slots=True)
class EvidenceReviewItem:
    """One active Claim together with evidence and settled same-field values."""

    claim: ClaimRecord
    citations: tuple[ReviewCitation, ...]
    settled_claims: tuple[ClaimRecord, ...]
    is_next: bool


@dataclass(frozen=True, slots=True)
class EvidenceReviewResult:
    """Deterministic review queue for exactly one Case."""

    case_id: str
    items: tuple[EvidenceReviewItem, ...]

    @property
    def next_claim_id(self) -> str | None:
        return self.items[0].claim.id if self.items else None
