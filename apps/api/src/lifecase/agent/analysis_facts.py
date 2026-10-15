"""A bounded analysis projection of existing current-fact authority."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict

from lifecase.agent.errors import AgentValidationError
from lifecase.claims.current_facts import CurrentFactsError, CurrentFactsService
from lifecase.providers.models import canonical_json_object


def analysis_facts_context(connection: sqlite3.Connection, case_id: str) -> dict[str, object]:
    """Read current authority, joining a caller's transaction without owning it."""

    try:
        facts = CurrentFactsService(connection).list_case_facts(
            case_id, within_transaction=connection.in_transaction
        )
    except CurrentFactsError as error:
        raise AgentValidationError("Analysis current facts could not be read") from error
    items: list[dict[str, object]] = []
    for item in facts.items:
        citations = []
        for citation in item.citations:
            reference = asdict(citation)
            # Store the original quote and all locations, not another complete
            # material copy. CurrentFactsService already revalidates its text hash.
            del reference["text"]
            reference["relation"] = citation.relation.value
            citations.append(reference)
        items.append(
            {
                "claim_id": item.claim.id,
                "field": item.claim.field,
                "value": item.claim.value.to_python(),
                "value_sha256": item.claim.value.sha256,
                "version": item.claim.version,
                "status": item.claim.status.value,
                "confirmation_basis": item.confirmation_basis,
                "confirmation": asdict(item.confirmation) if item.confirmation else None,
                "evidence_status": item.evidence_status,
                "conflict": item.conflict,
                "usable_as_fact": item.usable_as_fact,
                "citations": citations,
            }
        )
    return {"case_id": facts.case_id, "items": items}


def validate_analysis_facts(
    connection: sqlite3.Connection, case_id: str, supplied: dict[str, object]
) -> None:
    """Reject stale or self-hashed forged authority at every commit boundary."""

    try:
        actual = analysis_facts_context(connection, case_id)
        if canonical_json_object(actual, maximum_bytes=100_000) != canonical_json_object(
            supplied, maximum_bytes=100_000
        ):
            raise AgentValidationError("Analysis current facts have changed")
    except ValueError as error:
        raise AgentValidationError("Analysis current facts are invalid") from error
