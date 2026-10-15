"""Current authority fingerprints for one explicitly scoped question run."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Literal

from lifecase.agent.analysis_facts import analysis_facts_context
from lifecase.agent.errors import AgentErrorCode, AgentIntegrityError
from lifecase.agent.question_binding import validate_question_binding
from lifecase.claims.current_facts import CurrentFactsError
from lifecase.providers import canonical_json_object


def question_basis(connection: sqlite3.Connection, case_id: str) -> dict[str, object]:
    """Read facts and material identities in one snapshot, also inside runner writes."""
    owned = not connection.in_transaction
    if owned:
        connection.execute("BEGIN")
    try:
        facts = analysis_facts_context(connection, case_id)
        sources: list[dict[str, object]] = []
        for source in connection.execute(
            """SELECT s.id, s.case_id, s.original_name, s.sha256, s.trust_label,
                      COALESCE(e.status, 'not_extracted') AS parse_status
               FROM source s LEFT JOIN source_extraction e ON e.source_id = s.id
               WHERE s.case_id = ? ORDER BY s.id""",
            (case_id,),
        ).fetchall():
            if source["trust_label"] != "untrusted":
                raise _changed()
            segments: list[dict[str, object]] = []
            for segment in connection.execute(
                """SELECT id, sequence, page, text, text_sha256 FROM source_segment
                   WHERE source_id = ? ORDER BY sequence, id""",
                (source["id"],),
            ).fetchall():
                if (
                    hashlib.sha256(str(segment["text"]).encode("utf-8")).hexdigest()
                    != segment["text_sha256"]
                ):
                    raise _changed()
                segments.append(
                    {
                        "id": str(segment["id"]),
                        "sequence": int(segment["sequence"]),
                        "page": segment["page"],
                        "text_sha256": str(segment["text_sha256"]),
                    }
                )
            sources.append(
                {
                    "id": str(source["id"]),
                    "case_id": str(source["case_id"]),
                    "original_name": str(source["original_name"]),
                    "sha256": str(source["sha256"]),
                    "parse_status": str(source["parse_status"]),
                    "segments": segments,
                }
            )
        result: dict[str, object] = {"facts": facts, "sources": sources}
        canonical_json_object(result, maximum_bytes=100_000)
        if owned:
            connection.execute("COMMIT")
        return result
    finally:
        if owned and connection.in_transaction:
            connection.execute("ROLLBACK")


def basis_sha256(basis: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_object(basis, maximum_bytes=100_000).encode()).hexdigest()


def require_current_binding(
    connection: sqlite3.Connection, binding_json: str, case_id: str
) -> None:
    """Never silently replace the question or its authorized basis during a run."""
    binding = validate_question_binding(binding_json, case_id)
    try:
        current = question_basis(connection, case_id)
        if basis_sha256(current) != binding["basis_sha256"]:
            raise _changed()
    except (CurrentFactsError, sqlite3.Error, ValueError) as error:
        raise _changed() from error


def binding_status(
    connection: sqlite3.Connection,
    binding_json: str,
    case_id: str,
) -> Literal["current", "changed", "unavailable"]:
    binding = validate_question_binding(binding_json, case_id)
    try:
        current = question_basis(connection, case_id)
    except (AgentIntegrityError, CurrentFactsError, sqlite3.Error, ValueError):
        return "unavailable"
    return "current" if basis_sha256(current) == binding["basis_sha256"] else "changed"


def _changed() -> AgentIntegrityError:
    return AgentIntegrityError(
        "Question analysis basis changed or is unavailable", code=AgentErrorCode.INTEGRITY
    )
