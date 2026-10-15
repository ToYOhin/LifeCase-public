"""Strict, bounded storage contract for one immutable Case question input."""

from __future__ import annotations

import hashlib
import json
import re
from typing import cast

from lifecase.agent.errors import AgentValidationError
from lifecase.claims.models import ClaimValue, validate_field
from lifecase.providers.models import canonical_json_object

QUESTION_RUN_PREFIX = "portfolio-live-q1-"
MAX_QUESTION_BINDING_BYTES = 100_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def validate_question_binding(json_text: str, case_id: str) -> dict[str, object]:
    """Read canonical input metadata without requiring that historical facts remain current."""
    try:
        value = json.loads(json_text)
        item = _object(
            value,
            {
                "schema_version",
                "case_id",
                "question",
                "retrieval_query",
                "basis",
                "basis_sha256",
                "authorization_sha256",
            },
        )
        if (
            canonical_json_object(item, maximum_bytes=MAX_QUESTION_BINDING_BYTES) != json_text
            or item["schema_version"] != "live_agent_question.v1"
            or item["case_id"] != case_id
        ):
            raise _invalid()
        _text(item["case_id"], 100)
        _text(item["question"], 2000, trimmed=True)
        _text(item["retrieval_query"], 256, trimmed=True)
        _hash(item["basis_sha256"])
        _hash(item["authorization_sha256"])
        basis = _object(item["basis"], {"facts", "sources"})
        if (
            _sha(canonical_json_object(basis, maximum_bytes=MAX_QUESTION_BINDING_BYTES))
            != item["basis_sha256"]
        ):
            raise _invalid()
        _facts(basis["facts"], case_id)
        _sources(basis["sources"], case_id)
        return item
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise _invalid() from error


def validate_run_binding(
    run_id: str, case_id: str, json_text: str | None, sha256: str | None
) -> None:
    """Keep legacy inputs absent and question inputs complete and tamper-evident."""
    if not run_id.startswith(QUESTION_RUN_PREFIX):
        if json_text is not None or sha256 is not None:
            raise _invalid()
        return
    if not isinstance(json_text, str) or not isinstance(sha256, str):
        raise _invalid()
    _hash(sha256)
    if _sha(json_text) != sha256:
        raise _invalid()
    validate_question_binding(json_text, case_id)


def _facts(value: object, case_id: str) -> None:
    facts = _object(value, {"case_id", "items"})
    if facts["case_id"] != case_id:
        raise _invalid()
    seen: set[str] = set()
    for raw in _items(facts["items"]):
        item = _object(
            raw,
            {
                "claim_id",
                "field",
                "value",
                "value_sha256",
                "version",
                "status",
                "confirmation_basis",
                "confirmation",
                "evidence_status",
                "conflict",
                "usable_as_fact",
                "citations",
            },
        )
        claim_id = _text(item["claim_id"], 200)
        if claim_id in seen:
            raise _invalid()
        seen.add(claim_id)
        validate_field(item["field"])
        claim_value = ClaimValue.from_input(item["value"])
        if claim_value.to_python() != item["value"] or claim_value.sha256 != item["value_sha256"]:
            raise _invalid()
        _integer(item["version"], minimum=1)
        if item["status"] not in {"candidate", "inferred", "confirmed", "corrected", "rejected"}:
            raise _invalid()
        basis = item["confirmation_basis"]
        if basis not in {None, "evidence", "user", "correction"}:
            raise _invalid()
        if item["confirmation"] is not None:
            confirmation = _object(item["confirmation"], {"actor", "reason", "occurred_at"})
            _text(confirmation["actor"], 120)
            _text(confirmation["reason"], 1000)
            _text(confirmation["occurred_at"], 100)
        if (basis is None) != (item["confirmation"] is None):
            raise _invalid()
        if item["evidence_status"] not in {"valid", "unavailable", "none"}:
            raise _invalid()
        if not isinstance(item["conflict"], bool) or not isinstance(item["usable_as_fact"], bool):
            raise _invalid()
        if item["usable_as_fact"] and (
            basis is None
            or item["conflict"]
            or item["evidence_status"] == "unavailable"
            or item["status"] not in {"confirmed", "corrected"}
        ):
            raise _invalid()
        for citation in _items(item["citations"]):
            _citation(citation, case_id, claim_id)


def _citation(value: object, case_id: str, claim_id: str) -> None:
    item = _object(
        value,
        {
            "claim_id",
            "case_id",
            "source_id",
            "source_name",
            "segment_id",
            "sequence",
            "page",
            "text_sha256",
            "quote_start",
            "quote_end",
            "quote_hash",
            "quote_text",
            "relation",
            "attached_by",
            "attached_at",
        },
    )
    if item["case_id"] != case_id or item["claim_id"] != claim_id:
        raise _invalid()
    for key in ("source_id", "segment_id"):
        _text(item[key], 200)
    _text(item["source_name"], 1024)
    _integer(item["sequence"])
    if item["page"] is not None:
        _integer(item["page"], minimum=1)
    _hash(item["text_sha256"])
    _hash(item["quote_hash"])
    start = _integer(item["quote_start"])
    end = _integer(item["quote_end"], minimum=1)
    quote = _text(item["quote_text"], 32_000)
    if end - start != len(quote) or _sha(quote) != item["quote_hash"]:
        raise _invalid()
    if item["relation"] not in {"supports", "contradicts", "context"}:
        raise _invalid()
    _text(item["attached_by"], 120)
    _text(item["attached_at"], 100)


def _sources(value: object, case_id: str) -> None:
    seen: set[str] = set()
    segment_ids: set[str] = set()
    for raw in _items(value):
        item = _object(
            raw, {"id", "case_id", "original_name", "sha256", "parse_status", "segments"}
        )
        identifier = _text(item["id"], 200)
        if item["case_id"] != case_id or identifier in seen:
            raise _invalid()
        seen.add(identifier)
        _text(item["original_name"], 1024)
        _hash(item["sha256"])
        _text(item["parse_status"], 100)
        sequences: set[int] = set()
        for raw_segment in _items(item["segments"]):
            segment = _object(raw_segment, {"id", "sequence", "page", "text_sha256"})
            segment_id = _text(segment["id"], 200)
            sequence = _integer(segment["sequence"])
            if segment_id in segment_ids or sequence in sequences:
                raise _invalid()
            segment_ids.add(segment_id)
            sequences.add(sequence)
            if segment["page"] is not None:
                _integer(segment["page"], minimum=1)
            _hash(segment["text_sha256"])


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise _invalid()
    return cast(dict[str, object], value)


def _items(value: object) -> list[object]:
    if not isinstance(value, list) or len(value) > 500:
        raise _invalid()
    return cast(list[object], value)


def _text(value: object, maximum: int, *, trimmed: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or "\0" in value
        or (trimmed and value != value.strip())
    ):
        raise _invalid()
    return value


def _integer(value: object, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise _invalid()
    return value


def _hash(value: object) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _invalid()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _invalid() -> AgentValidationError:
    return AgentValidationError("Question input binding is invalid")
