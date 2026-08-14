"""Read-only deterministic Evidence Review composition."""

import hashlib
import sqlite3
from datetime import datetime

from lifecase.cases.service import CaseNotFoundError, CaseService, CaseTemplateBindingError
from lifecase.claims.models import ACTIVE_CLAIM_STATUSES, ClaimRecord, ClaimSource, ClaimStatus
from lifecase.claims.review_models import (
    EvidenceReviewItem,
    EvidenceReviewResult,
    ReviewCitation,
)
from lifecase.claims.service import (
    ClaimCaseNotFoundError,
    ClaimPersistenceError,
    ClaimService,
    ClaimSourceInvalidError,
    ClaimValidationError,
)
from lifecase.templates.models import CaseTemplate
from lifecase.templates.registry import BUILT_IN_TEMPLATE_REGISTRY, TemplateRegistryError

_SETTLED_STATUSES = frozenset({ClaimStatus.CONFIRMED, ClaimStatus.CORRECTED})
_ACTIVE_STATUS_RANK = {ClaimStatus.INFERRED: 0, ClaimStatus.CANDIDATE: 1}


class EvidenceReviewError(RuntimeError):
    """Base class for safe Evidence Review failures."""


class EvidenceReviewValidationError(EvidenceReviewError):
    """Raised when the Case ID violates the stable Claim boundary."""


class EvidenceReviewCaseNotFoundError(EvidenceReviewError):
    """Raised when the requested Case does not exist."""


class EvidenceReviewIntegrityError(EvidenceReviewError):
    """Raised when persisted ClaimSource authority is stale or inconsistent."""


class EvidenceReviewConfigurationError(EvidenceReviewError):
    """Raised when a Case template binding cannot define review field order."""


class EvidenceReviewPersistenceError(EvidenceReviewError):
    """Raised when review state cannot be read safely."""


class EvidenceReviewService:
    """Build a stable Case-scoped review queue without durable mutation."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_case_review(self, case_id: str) -> EvidenceReviewResult:
        """Return active Claims, current settled values, and authoritative evidence."""

        try:
            if self._connection.in_transaction:
                raise EvidenceReviewPersistenceError("Evidence Review requires an idle connection")
            self._connection.execute("BEGIN")
            case = CaseService(self._connection).get_case(case_id)
            template = BUILT_IN_TEMPLATE_REGISTRY.load_exact(
                case_type=case.type,
                template_id=case.template_id,
                template_version=case.template_version,
            )
            claim_service = ClaimService(self._connection)
            claims = claim_service.list_case_claims(case.id)
            field_rank = _field_rank(template)
            active = sorted(
                (claim for claim in claims if claim.status in ACTIVE_CLAIM_STATUSES),
                key=lambda claim: _active_order(claim, field_rank),
            )
            settled = _settled_by_field(claims)
            items = tuple(
                EvidenceReviewItem(
                    claim=claim,
                    citations=self._citations(claim_service, claim),
                    settled_claims=settled.get(claim.field, ()),
                    is_next=index == 0,
                )
                for index, claim in enumerate(active)
            )
            result = EvidenceReviewResult(case_id=case.id, items=items)
            self._connection.execute("COMMIT")
            return result
        except EvidenceReviewError:
            _rollback(self._connection)
            raise
        except (CaseNotFoundError, ClaimCaseNotFoundError) as error:
            _rollback(self._connection)
            raise EvidenceReviewCaseNotFoundError from error
        except (ClaimValidationError, ValueError) as error:
            _rollback(self._connection)
            raise EvidenceReviewValidationError("Evidence Review request is invalid") from error
        except ClaimSourceInvalidError as error:
            _rollback(self._connection)
            raise EvidenceReviewIntegrityError("Evidence Review authority is invalid") from error
        except (CaseTemplateBindingError, TemplateRegistryError) as error:
            _rollback(self._connection)
            raise EvidenceReviewConfigurationError(
                "Evidence Review template configuration is invalid"
            ) from error
        except (ClaimPersistenceError, sqlite3.Error) as error:
            _rollback(self._connection)
            raise EvidenceReviewPersistenceError("Evidence Review could not be read") from error

    def _citations(
        self,
        claim_service: ClaimService,
        claim: ClaimRecord,
    ) -> tuple[ReviewCitation, ...]:
        sources = claim_service.list_claim_sources(claim.id)
        return tuple(self._citation_from_authority(claim, source) for source in sources)

    def _citation_from_authority(
        self,
        claim: ClaimRecord,
        source: ClaimSource,
    ) -> ReviewCitation:
        row = self._connection.execute(
            """
            SELECT s.original_name, ss.sequence, ss.page, ss.text, ss.text_sha256
            FROM source AS s
            JOIN source_segment AS ss ON ss.source_id = s.id
            WHERE s.case_id = ? AND s.id = ? AND ss.id = ?
            """,
            (claim.case_id, source.source_id, source.segment_id),
        ).fetchone()
        if row is None:
            raise EvidenceReviewIntegrityError("Evidence Review authority is invalid")
        text = str(row["text"])
        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        page = int(row["page"]) if row["page"] is not None else None
        quote_text = text[source.quote_start : source.quote_end]
        quote_hash = hashlib.sha256(quote_text.encode("utf-8")).hexdigest()
        if (
            source.claim_id != claim.id
            or source.case_id != claim.case_id
            or int(row["sequence"]) != source.sequence
            or page != source.page
            or str(row["text_sha256"]) != text_sha256
            or source.segment_text_sha256 != text_sha256
            or source.quote_start < 0
            or source.quote_end <= source.quote_start
            or source.quote_end > len(text)
            or quote_hash != source.quote_hash
        ):
            raise EvidenceReviewIntegrityError("Evidence Review authority is invalid")
        return ReviewCitation(
            claim_id=source.claim_id,
            case_id=source.case_id,
            source_id=source.source_id,
            source_name=str(row["original_name"]),
            segment_id=source.segment_id,
            sequence=source.sequence,
            page=source.page,
            text_sha256=source.segment_text_sha256,
            text=text,
            quote_start=source.quote_start,
            quote_end=source.quote_end,
            quote_hash=source.quote_hash,
            quote_text=quote_text,
            relation=source.relation,
            attached_by=source.attached_by,
            attached_at=source.attached_at,
        )


def _field_rank(template: CaseTemplate) -> dict[str, int]:
    ordered = (
        *template.required_claims,
        *(item.field for item in template.conditional_claims),
        *(item.key for item in template.evidence_requirements),
    )
    return {field: index for index, field in enumerate(ordered)}


def _active_order(claim: ClaimRecord, field_rank: dict[str, int]) -> tuple[object, ...]:
    return (
        field_rank.get(claim.field, len(field_rank)),
        claim.field,
        not claim.contradiction,
        _ACTIVE_STATUS_RANK[claim.status],
        claim.created_at,
        claim.id,
    )


def _settled_by_field(claims: tuple[ClaimRecord, ...]) -> dict[str, tuple[ClaimRecord, ...]]:
    grouped: dict[str, list[ClaimRecord]] = {}
    for claim in claims:
        if claim.status in _SETTLED_STATUSES:
            grouped.setdefault(claim.field, []).append(claim)
    return {field: tuple(sorted(values, key=_settled_order)) for field, values in grouped.items()}


def _settled_order(claim: ClaimRecord) -> tuple[object, ...]:
    timestamp = datetime.fromisoformat(claim.updated_at.replace("Z", "+00:00"))
    return (-timestamp.timestamp(), claim.id)


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass
