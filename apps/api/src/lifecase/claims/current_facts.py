"""Read-only current fact authority for a user-prepared communication preview."""

import sqlite3
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Literal

from lifecase.cases.service import CaseNotFoundError, CaseService, CaseServiceError
from lifecase.claims.models import (
    ClaimEventType,
    ClaimRecord,
    ClaimSource,
    ClaimSourceRelation,
    ClaimStatus,
    validate_case_id,
)
from lifecase.claims.review_models import ReviewCitation
from lifecase.claims.service import ClaimService, ClaimServiceError, ClaimSourceInvalidError

type ConfirmationBasis = Literal["evidence", "user", "correction"]
type EvidenceStatus = Literal["valid", "unavailable", "none"]


class CurrentFactsError(RuntimeError):
    """Base error without private storage details."""


class CurrentFactsValidationError(CurrentFactsError):
    """The requested Case scope is invalid."""


class CurrentFactsCaseNotFoundError(CurrentFactsError):
    """The requested Case does not exist."""


class CurrentFactsPersistenceError(CurrentFactsError):
    """A consistent current projection could not be read."""


@dataclass(frozen=True, slots=True)
class CurrentFactConfirmation:
    """Existing confirmation or correction metadata for the current value."""

    actor: str
    reason: str
    occurred_at: str


@dataclass(frozen=True, slots=True)
class CurrentFact:
    """One unchanged Claim plus fresh presentation-only authority flags."""

    claim: ClaimRecord
    confirmation_basis: ConfirmationBasis | None
    confirmation: CurrentFactConfirmation | None
    evidence_status: EvidenceStatus
    citations: tuple[ReviewCitation, ...]
    conflict: bool
    usable_as_fact: bool


@dataclass(frozen=True, slots=True)
class CurrentFacts:
    case_id: str
    case_type: str
    items: tuple[CurrentFact, ...]


class CurrentFactsService:
    """Compose Claim reads in one snapshot without changing durable authority."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_case_facts(self, case_id: str, *, within_transaction: bool = False) -> CurrentFacts:
        try:
            clean_case_id = validate_case_id(case_id)
        except ValueError as error:
            raise CurrentFactsValidationError("Case scope is invalid") from error

        started = False
        try:
            if within_transaction:
                if not self._connection.in_transaction:
                    raise CurrentFactsPersistenceError(
                        "Current facts require an active transaction"
                    )
            elif self._connection.in_transaction:
                raise CurrentFactsPersistenceError("Current facts require an idle connection")
            else:
                self._connection.execute("BEGIN")
                started = True
            case = CaseService(self._connection).get_case(clean_case_id)
            claims = ClaimService(self._connection)
            records = claims.list_case_claims(case.id)
            items = tuple(self._item(claims, record) for record in records)
            conflicts = _conflicting_fields(records)
            conflicts.update(
                item.claim.field
                for item in items
                if item.claim.status is not ClaimStatus.REJECTED
                and any(c.relation is ClaimSourceRelation.CONTRADICTS for c in item.citations)
            )
            result = CurrentFacts(
                case_id=case.id,
                case_type=case.type,
                items=tuple(
                    replace(
                        item,
                        conflict=item.claim.field in conflicts,
                        usable_as_fact=item.usable_as_fact and item.claim.field not in conflicts,
                    )
                    for item in items
                ),
            )
            if started:
                self._connection.execute("COMMIT")
            return result
        except CaseNotFoundError as error:
            raise CurrentFactsCaseNotFoundError from error
        except (CaseServiceError, ClaimServiceError, sqlite3.Error, ValueError) as error:
            raise CurrentFactsPersistenceError("Current facts could not be read") from error
        finally:
            if started:
                with suppress(sqlite3.Error):
                    if self._connection.in_transaction:
                        self._connection.execute("ROLLBACK")

    def _item(self, service: ClaimService, claim: ClaimRecord) -> CurrentFact:
        evidence_status: EvidenceStatus
        try:
            sources = service.list_claim_sources(claim.id)
            citations = tuple(self._citation(claim, source) for source in sources)
            evidence_status = "valid" if citations else "none"
        except ClaimSourceInvalidError:
            # One damaged reference makes this fact unusable, not unrelated facts.
            citations = ()
            evidence_status = "unavailable"
        basis, confirmation = _confirmation(service, claim)
        usable = (
            basis is not None
            and evidence_status != "unavailable"
            and (
                basis != "evidence"
                or any(c.relation is ClaimSourceRelation.SUPPORTS for c in citations)
            )
        )
        return CurrentFact(
            claim=claim,
            confirmation_basis=basis,
            confirmation=confirmation,
            evidence_status=evidence_status,
            citations=citations,
            conflict=False,
            usable_as_fact=usable,
        )

    def _citation(self, claim: ClaimRecord, source: ClaimSource) -> ReviewCitation:
        # ClaimService has already revalidated the quote and Segment hashes in
        # this same read transaction. This join only adds display name and text.
        row = self._connection.execute(
            """
            SELECT s.original_name, ss.text
            FROM source AS s
            JOIN source_segment AS ss ON ss.source_id = s.id
            WHERE s.case_id = ? AND s.id = ? AND ss.id = ?
            """,
            (claim.case_id, source.source_id, source.segment_id),
        ).fetchone()
        if row is None:
            raise ClaimSourceInvalidError
        text = str(row["text"])
        return ReviewCitation(
            claim_id=claim.id,
            case_id=claim.case_id,
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
            quote_text=text[source.quote_start : source.quote_end],
            relation=source.relation,
            attached_by=source.attached_by,
            attached_at=source.attached_at,
        )


def _confirmation(
    service: ClaimService, claim: ClaimRecord
) -> tuple[ConfirmationBasis | None, CurrentFactConfirmation | None]:
    if claim.status is ClaimStatus.CONFIRMED:
        for record in reversed(service.list_claim_confirmations(claim.id)):
            if record.claim_version <= claim.version and record.value_sha256 == claim.value.sha256:
                basis: ConfirmationBasis = "user" if record.basis.value == "user" else "evidence"
                return basis, CurrentFactConfirmation(
                    actor=record.actor,
                    reason=record.reason,
                    occurred_at=record.confirmed_at,
                )
    elif claim.status is ClaimStatus.CORRECTED:
        for event in reversed(service.list_claim_events(claim.id)):
            if (
                event.event_type is ClaimEventType.CORRECTED
                and event.new_version <= claim.version
                and event.new_value.sha256 == claim.value.sha256
            ):
                return "correction", CurrentFactConfirmation(
                    actor=event.actor,
                    reason=event.reason,
                    occurred_at=event.occurred_at,
                )
    return None, None


def _conflicting_fields(claims: tuple[ClaimRecord, ...]) -> set[str]:
    values: dict[str, set[str]] = {}
    for claim in claims:
        if claim.status is not ClaimStatus.REJECTED:
            values.setdefault(claim.field, set()).add(claim.value.comparison_sha256)
    return {field for field, hashes in values.items() if len(hashes) > 1}
