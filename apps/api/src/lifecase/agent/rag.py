"""M3-04 controlled retrieval and evidence-backed answer checkpoint contracts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, replace
from enum import StrEnum

from lifecase.agent.errors import AgentValidationError
from lifecase.agent.extraction import (
    EVIDENCE_EXTRACTION_STAGE_REGISTRY,
    WarrantyExtractionCheckpointHandler,
)
from lifecase.agent.models import (
    STAGE_ORDER,
    AgentCheckpoint,
    AgentStageName,
    StageInput,
    checkpoint_payload,
    stage_input_scope_sha256,
)
from lifecase.agent.registry import StageRegistry
from lifecase.evidence.context import (
    EvidenceContextBudget,
    EvidenceContextError,
    EvidenceContextRequest,
    EvidenceContextService,
    EvidencePolicy,
    EvidenceTrustLabel,
)
from lifecase.providers.models import canonical_json_object
from lifecase.tools import (
    EVIDENCE_CONTEXT_TOOL_NAME,
    EvidenceContextReadTool,
    EvidenceContextToolRequest,
    EvidenceContextToolResult,
    evidence_context_payload,
    evidence_context_sha256,
)

EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION = "evidence_backed_answer.v1"
_RAG_STAGE_VERSION = "validate.rag.v1"
_RAG_INPUT_SCHEMA_VERSION = "validate.rag.input.v1"
_RAG_OUTPUT_SCHEMA_VERSION = "validate.rag.output.v1"
_RAG_CHECKPOINT_SCHEMA_VERSION = "validate.rag.checkpoint.v1"
_MAX_RAG_RETRIEVAL_LIMIT = 10
_MAX_RAG_ADJACENT_SEGMENTS = 2
_MAX_RAG_CONTEXT_TEXT_BYTES = 32_000
_MAX_STATEMENTS = 16
_MAX_STATEMENT_CHARS = 2_000
_MAX_REFS_PER_STATEMENT = 8
_MAX_UNCERTAINTY_REASON = 1_000
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class EvidenceAnswerStatus(StrEnum):
    """The only two valid evidence-backed answer outcomes."""

    ANSWERED = "answered"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RagEvidenceSegment:
    """One ephemeral tool segment revalidated against authoritative storage."""

    case_id: str
    source_id: str
    segment_id: str
    sequence: int
    page: int | None
    text: str
    text_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceAnswerSourceRef:
    """A bounded quote from one authoritative Context segment."""

    case_id: str
    source_id: str
    segment_id: str
    sequence: int
    page: int | None
    text_sha256: str
    quote_start: int
    quote_end: int
    quote_hash: str

    def to_json(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "source_id": self.source_id,
            "segment_id": self.segment_id,
            "sequence": self.sequence,
            "page": self.page,
            "text_sha256": self.text_sha256,
            "quote_start": self.quote_start,
            "quote_end": self.quote_end,
            "quote_hash": self.quote_hash,
        }


@dataclass(frozen=True, slots=True)
class EvidenceAnswerStatement:
    """One answer statement supported by one or more exact quote references."""

    text: str
    source_refs: tuple[EvidenceAnswerSourceRef, ...]


@dataclass(frozen=True, slots=True)
class PreparedEvidenceAnswer:
    """Parsed answer and its ephemeral authority set for final revalidation."""

    status: EvidenceAnswerStatus
    statements: tuple[EvidenceAnswerStatement, ...]
    uncertainty_reason: str | None
    context_sha256: str
    evidence: tuple[RagEvidenceSegment, ...]


def evidence_backed_rag_stage_input(
    connection: sqlite3.Connection,
    request: EvidenceContextToolRequest,
    *,
    prior_checkpoint_hashes: tuple[str, ...],
) -> StageInput:
    """Execute the allowed local tool and build the ephemeral validate-stage input."""

    if not isinstance(request, EvidenceContextToolRequest):
        raise AgentValidationError("Evidence-backed RAG request is invalid")
    if (
        request.context_request.retrieval_limit > _MAX_RAG_RETRIEVAL_LIMIT
        or request.context_request.adjacent_before > _MAX_RAG_ADJACENT_SEGMENTS
        or request.context_request.adjacent_after > _MAX_RAG_ADJACENT_SEGMENTS
        or request.context_request.budget.max_text_bytes > _MAX_RAG_CONTEXT_TEXT_BYTES
    ):
        raise AgentValidationError("Evidence-backed RAG Context budget is invalid")
    expected_prior = STAGE_ORDER.index(AgentStageName.VALIDATE)
    if (
        not isinstance(prior_checkpoint_hashes, tuple)
        or len(prior_checkpoint_hashes) != expected_prior
        or any(not _valid_sha256(value) for value in prior_checkpoint_hashes)
    ):
        raise AgentValidationError("Evidence-backed RAG checkpoint chain is invalid")
    definition = EVIDENCE_BACKED_RAG_STAGE_REGISTRY.get(AgentStageName.VALIDATE)
    result = EvidenceContextReadTool(
        connection,
        allowed_tools=definition.allowed_tools,
    ).execute(request)
    try:
        payload_json = canonical_json_object(
            {
                "case_id": request.context_request.case_id,
                "prior_checkpoint_hashes": list(prior_checkpoint_hashes),
                "stage_payload": {
                    "question": request.context_request.query,
                    "tool_result": _tool_result_payload(result, request.context_request),
                },
            },
            maximum_bytes=100_000,
        )
    except ValueError as error:
        raise AgentValidationError("Evidence-backed RAG input is invalid") from error
    return StageInput(
        case_id=request.context_request.case_id,
        stage=AgentStageName.VALIDATE,
        stage_version=definition.stage_version,
        input_schema_version=definition.input_schema_version,
        input_scope_sha256=stage_input_scope_sha256(
            request.context_request.case_id,
            AgentStageName.VALIDATE,
            definition.stage_version,
            definition.input_schema_version,
            payload_json,
        ),
        payload_json=payload_json,
    )


class EvidenceBackedRagCheckpointHandler:
    """Combine M3-03 extraction commits with M3-04 read-only answer validation."""

    def __init__(self) -> None:
        self._extraction = WarrantyExtractionCheckpointHandler()

    def normalize(
        self,
        *,
        connection: sqlite3.Connection,
        case_id: str,
        stage_input_json: str,
        checkpoint: AgentCheckpoint,
    ) -> AgentCheckpoint:
        if checkpoint.stage is AgentStageName.EXTRACT:
            return self._extraction.normalize(
                connection=connection,
                case_id=case_id,
                stage_input_json=stage_input_json,
                checkpoint=checkpoint,
            )
        if checkpoint.stage is AgentStageName.VALIDATE:
            _context_sha256, evidence, request, supplied_context = _parse_rag_input(
                stage_input_json, case_id
            )
            _validate_tool_result(connection, request, _context_sha256, supplied_context)
            _validate_authority(connection, case_id, evidence)
            normalized = _normalize_answer_source_refs(
                checkpoint.payload_json,
                case_id=case_id,
                evidence=evidence,
            )
            payload_json, payload_sha256 = checkpoint_payload(normalized)
            return replace(
                checkpoint,
                payload_json=payload_json,
                payload_sha256=payload_sha256,
            )
        return checkpoint

    def prepare(
        self,
        *,
        connection: sqlite3.Connection,
        run_id: str,
        case_id: str,
        stage_input_json: str,
        checkpoint: AgentCheckpoint,
    ) -> object:
        if checkpoint.stage is AgentStageName.EXTRACT:
            return self._extraction.prepare(
                connection=connection,
                run_id=run_id,
                case_id=case_id,
                stage_input_json=stage_input_json,
                checkpoint=checkpoint,
            )
        if checkpoint.stage is not AgentStageName.VALIDATE:
            return None
        context_sha256, evidence, request, supplied_context = _parse_rag_input(
            stage_input_json, case_id
        )
        _validate_tool_result(connection, request, context_sha256, supplied_context)
        _validate_authority(connection, case_id, evidence)
        return _parse_answer(checkpoint.payload_json, case_id, context_sha256, evidence)

    def commit(
        self,
        *,
        connection: sqlite3.Connection,
        run_id: str,
        case_id: str,
        checkpoint: AgentCheckpoint,
        prepared: object,
    ) -> None:
        if checkpoint.stage is AgentStageName.EXTRACT:
            self._extraction.commit(
                connection=connection,
                run_id=run_id,
                case_id=case_id,
                checkpoint=checkpoint,
                prepared=prepared,
            )
            return
        if checkpoint.stage is not AgentStageName.VALIDATE:
            return
        if not isinstance(prepared, PreparedEvidenceAnswer):
            raise AgentValidationError("Evidence-backed answer is invalid")
        _validate_authority(connection, case_id, prepared.evidence)


def _tool_result_payload(
    result: EvidenceContextToolResult,
    request: EvidenceContextRequest,
) -> dict[str, object]:
    return {
        "tool_name": result.tool_name,
        "risk": result.risk.value,
        "call_count": result.call_count,
        "context_sha256": result.context_sha256,
        "request": {
            "retrieval_limit": request.retrieval_limit,
            "adjacent_before": request.adjacent_before,
            "adjacent_after": request.adjacent_after,
            "max_text_bytes": request.budget.max_text_bytes,
            "max_item_text_bytes": request.budget.max_item_text_bytes,
            "allowed_trust_labels": sorted(
                label.value for label in request.policy.allowed_trust_labels
            ),
        },
        "context": result.to_stage_payload(),
    }


def _parse_rag_input(
    payload_json: str,
    case_id: str,
) -> tuple[
    str,
    tuple[RagEvidenceSegment, ...],
    EvidenceContextRequest,
    dict[str, object],
]:
    invalid = AgentValidationError("Evidence-backed RAG input is invalid")
    try:
        value = json.loads(payload_json)
        if not isinstance(value, dict) or set(value) != {
            "case_id",
            "prior_checkpoint_hashes",
            "stage_payload",
        }:
            raise invalid
        if value["case_id"] != case_id or not isinstance(value["stage_payload"], dict):
            raise invalid
        stage_payload = value["stage_payload"]
        if set(stage_payload) != {"question", "tool_result"}:
            raise invalid
        question = stage_payload["question"]
        tool_result = stage_payload["tool_result"]
        if not isinstance(question, str) or not isinstance(tool_result, dict):
            raise invalid
        if set(tool_result) != {
            "tool_name",
            "risk",
            "call_count",
            "context_sha256",
            "request",
            "context",
        }:
            raise invalid
        if (
            tool_result["tool_name"] != EVIDENCE_CONTEXT_TOOL_NAME
            or tool_result["risk"] != "A"
            or tool_result["call_count"] != 1
            or not _valid_sha256(tool_result["context_sha256"])
            or not isinstance(tool_result["request"], dict)
            or not isinstance(tool_result["context"], dict)
        ):
            raise invalid
        context = tool_result["context"]
        if _sha256_json(context) != tool_result["context_sha256"]:
            raise invalid
        evidence = _parse_context(context, case_id, question)
        context_request = _parse_tool_request(
            tool_result["request"], case_id=case_id, query=question
        )
        return str(tool_result["context_sha256"]), evidence, context_request, context
    except (AgentValidationError, KeyError, TypeError, ValueError) as error:
        if error is invalid:
            raise
        raise invalid from error


def _parse_tool_request(
    value: dict[str, object],
    *,
    case_id: str,
    query: str,
) -> EvidenceContextRequest:
    invalid = AgentValidationError("Evidence-backed RAG tool request is invalid")
    if set(value) != {
        "retrieval_limit",
        "adjacent_before",
        "adjacent_after",
        "max_text_bytes",
        "max_item_text_bytes",
        "allowed_trust_labels",
    }:
        raise invalid
    labels = value["allowed_trust_labels"]
    if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
        raise invalid
    try:
        if (
            not isinstance(value["retrieval_limit"], int)
            or isinstance(value["retrieval_limit"], bool)
            or not 1 <= value["retrieval_limit"] <= _MAX_RAG_RETRIEVAL_LIMIT
            or not isinstance(value["adjacent_before"], int)
            or isinstance(value["adjacent_before"], bool)
            or not 0 <= value["adjacent_before"] <= _MAX_RAG_ADJACENT_SEGMENTS
            or not isinstance(value["adjacent_after"], int)
            or isinstance(value["adjacent_after"], bool)
            or not 0 <= value["adjacent_after"] <= _MAX_RAG_ADJACENT_SEGMENTS
            or not isinstance(value["max_text_bytes"], int)
            or isinstance(value["max_text_bytes"], bool)
            or not 1 <= value["max_text_bytes"] <= _MAX_RAG_CONTEXT_TEXT_BYTES
        ):
            raise invalid
        parsed_labels = tuple(EvidenceTrustLabel(label) for label in labels)
        if tuple(sorted(label.value for label in parsed_labels)) != tuple(labels) or len(
            parsed_labels
        ) != len(set(parsed_labels)):
            raise invalid
        return EvidenceContextRequest(
            case_id=case_id,
            query=query,
            retrieval_limit=value["retrieval_limit"],
            adjacent_before=value["adjacent_before"],
            adjacent_after=value["adjacent_after"],
            budget=EvidenceContextBudget(
                max_text_bytes=value["max_text_bytes"],
                max_item_text_bytes=value["max_item_text_bytes"],  # type: ignore[arg-type]
            ),
            policy=EvidencePolicy(frozenset(parsed_labels)),
        )
    except (AgentValidationError, TypeError, ValueError) as error:
        if error is invalid:
            raise
        raise invalid from error


def _validate_tool_result(
    connection: sqlite3.Connection,
    request: EvidenceContextRequest,
    supplied_sha256: str,
    supplied_context: dict[str, object],
) -> None:
    """Replay the bounded formal service to reject self-hashed forged tool results."""

    try:
        authoritative = EvidenceContextService(connection).build(request)
        if (
            evidence_context_sha256(authoritative) != supplied_sha256
            or evidence_context_payload(authoritative) != supplied_context
        ):
            raise AgentValidationError("Evidence-backed RAG tool result is invalid")
    except EvidenceContextError as error:
        raise AgentValidationError("Evidence-backed RAG tool result is invalid") from error


def _parse_context(
    value: dict[str, object],
    case_id: str,
    question: str,
) -> tuple[RagEvidenceSegment, ...]:
    invalid = AgentValidationError("Evidence-backed RAG Context is invalid")
    if set(value) != {
        "case_id",
        "query",
        "used_text_bytes",
        "truncated",
        "matches",
        "items",
    }:
        raise invalid
    if (
        value["case_id"] != case_id
        or value["query"] != question
        or not isinstance(value["used_text_bytes"], int)
        or isinstance(value["used_text_bytes"], bool)
        or not 0 <= value["used_text_bytes"] <= _MAX_RAG_CONTEXT_TEXT_BYTES
        or not isinstance(value["truncated"], bool)
        or not isinstance(value["matches"], list)
        or not isinstance(value["items"], list)
    ):
        raise invalid
    evidence: list[RagEvidenceSegment] = []
    for raw_item in value["items"]:
        if not isinstance(raw_item, dict) or set(raw_item) != {
            "citation",
            "text",
            "origin",
            "match_ranks",
            "text_bytes",
        }:
            raise invalid
        citation = raw_item["citation"]
        text = raw_item["text"]
        if (
            not isinstance(citation, dict)
            or not isinstance(text, str)
            or raw_item["origin"] not in {"match", "adjacent"}
            or not isinstance(raw_item["match_ranks"], list)
            or raw_item["text_bytes"] != len(text.encode("utf-8"))
        ):
            raise invalid
        evidence.append(_parse_context_segment(citation, text, case_id))
    if sum(len(item.text.encode("utf-8")) for item in evidence) != value["used_text_bytes"] or len(
        {item.segment_id for item in evidence}
    ) != len(evidence):
        raise invalid
    return tuple(evidence)


def _parse_context_segment(
    citation: dict[str, object],
    text: str,
    case_id: str,
) -> RagEvidenceSegment:
    invalid = AgentValidationError("Evidence-backed RAG citation is invalid")
    if set(citation) != {
        "case_id",
        "source_id",
        "segment_id",
        "sequence",
        "page",
        "text_sha256",
    }:
        raise invalid
    source_id = _identifier(citation["source_id"])
    segment_id = _identifier(citation["segment_id"])
    sequence = citation["sequence"]
    page = citation["page"]
    text_sha256 = citation["text_sha256"]
    if (
        citation["case_id"] != case_id
        or not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 0
        or (page is not None and (not isinstance(page, int) or isinstance(page, bool) or page < 1))
        or not _valid_sha256(text_sha256)
        or _sha256(text) != text_sha256
    ):
        raise invalid
    return RagEvidenceSegment(
        case_id=case_id,
        source_id=source_id,
        segment_id=segment_id,
        sequence=sequence,
        page=page,
        text=text,
        text_sha256=str(text_sha256),
    )


def _parse_answer(
    payload_json: str,
    case_id: str,
    context_sha256: str,
    evidence: tuple[RagEvidenceSegment, ...],
) -> PreparedEvidenceAnswer:
    invalid = AgentValidationError("Evidence-backed answer is invalid")
    try:
        value = json.loads(payload_json)
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "status",
            "statements",
            "uncertainty_reason",
            "tool",
        }:
            raise invalid
        if value["schema_version"] != EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION:
            raise invalid
        status = EvidenceAnswerStatus(value["status"])
        tool = value["tool"]
        if not isinstance(tool, dict) or set(tool) != {
            "name",
            "call_count",
            "context_sha256",
        }:
            raise invalid
        if (
            tool["name"] != EVIDENCE_CONTEXT_TOOL_NAME
            or tool["call_count"] != 1
            or tool["context_sha256"] != context_sha256
        ):
            raise invalid
        raw_statements = value["statements"]
        if not isinstance(raw_statements, list) or len(raw_statements) > _MAX_STATEMENTS:
            raise invalid
        statements = tuple(_parse_statement(item, case_id, evidence) for item in raw_statements)
        reason = _optional_reason(value["uncertainty_reason"])
        if status is EvidenceAnswerStatus.ANSWERED:
            if not evidence or not statements or reason is not None:
                raise invalid
        elif statements or reason is None:
            raise invalid
        return PreparedEvidenceAnswer(
            status=status,
            statements=statements,
            uncertainty_reason=reason,
            context_sha256=context_sha256,
            evidence=evidence,
        )
    except (AgentValidationError, KeyError, TypeError, ValueError) as error:
        if error is invalid:
            raise
        raise invalid from error


def _normalize_answer_source_refs(
    payload_json: str,
    *,
    case_id: str,
    evidence: tuple[RagEvidenceSegment, ...],
) -> dict[str, object]:
    """Recompute derived quote metadata only after exact Context authority matches."""

    value = json.loads(payload_json)
    if not isinstance(value, dict):
        raise AgentValidationError("Evidence-backed answer is invalid")
    if value.get("status") != EvidenceAnswerStatus.ANSWERED:
        return value
    statements = value.get("statements")
    if not isinstance(statements, list):
        return value
    for statement in statements:
        if not isinstance(statement, dict):
            continue
        source_refs = statement.get("source_refs")
        if not isinstance(source_refs, list):
            continue
        for source_ref in source_refs:
            if not isinstance(source_ref, dict) or source_ref.get("case_id") != case_id:
                continue
            segment = next(
                (
                    item
                    for item in evidence
                    if source_ref.get("source_id") == item.source_id
                    and source_ref.get("segment_id") == item.segment_id
                    and source_ref.get("sequence") == item.sequence
                    and source_ref.get("page") == item.page
                    and source_ref.get("text_sha256") == item.text_sha256
                ),
                None,
            )
            if segment is None:
                continue
            quote_start = source_ref.get("quote_start")
            quote_end = source_ref.get("quote_end")
            quote_hash = source_ref.get("quote_hash")
            quote_is_valid = (
                isinstance(quote_start, int)
                and not isinstance(quote_start, bool)
                and isinstance(quote_end, int)
                and not isinstance(quote_end, bool)
                and 0 <= quote_start < quote_end <= len(segment.text)
                and _valid_sha256(quote_hash)
                and _sha256(segment.text[quote_start:quote_end]) == quote_hash
            )
            if not quote_is_valid:
                source_ref["quote_start"] = 0
                source_ref["quote_end"] = len(segment.text)
                source_ref["quote_hash"] = segment.text_sha256
    return value


def _parse_statement(
    value: object,
    case_id: str,
    evidence: tuple[RagEvidenceSegment, ...],
) -> EvidenceAnswerStatement:
    invalid = AgentValidationError("Evidence-backed answer statement is invalid")
    if not isinstance(value, dict) or set(value) != {"text", "source_refs"}:
        raise invalid
    text = value["text"]
    raw_refs = value["source_refs"]
    if (
        not isinstance(text, str)
        or not 1 <= len(text.strip()) <= _MAX_STATEMENT_CHARS
        or not isinstance(raw_refs, list)
        or not 1 <= len(raw_refs) <= _MAX_REFS_PER_STATEMENT
    ):
        raise invalid
    refs = tuple(_parse_source_ref(item, case_id, evidence) for item in raw_refs)
    keys = tuple((ref.segment_id, ref.quote_start, ref.quote_end, ref.quote_hash) for ref in refs)
    if len(keys) != len(set(keys)):
        raise invalid
    return EvidenceAnswerStatement(text=text.strip(), source_refs=refs)


def _parse_source_ref(
    value: object,
    case_id: str,
    evidence: tuple[RagEvidenceSegment, ...],
) -> EvidenceAnswerSourceRef:
    invalid = AgentValidationError("Evidence-backed answer source reference is invalid")
    if not isinstance(value, dict) or set(value) != {
        "case_id",
        "source_id",
        "segment_id",
        "sequence",
        "page",
        "text_sha256",
        "quote_start",
        "quote_end",
        "quote_hash",
    }:
        raise invalid
    try:
        source_id = _identifier(value["source_id"])
        segment_id = _identifier(value["segment_id"])
        sequence = value["sequence"]
        page = value["page"]
        text_sha256 = value["text_sha256"]
        quote_start = value["quote_start"]
        quote_end = value["quote_end"]
        quote_hash = value["quote_hash"]
        if (
            value["case_id"] != case_id
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 0
            or (
                page is not None
                and (not isinstance(page, int) or isinstance(page, bool) or page < 1)
            )
            or not _valid_sha256(text_sha256)
            or not isinstance(quote_start, int)
            or isinstance(quote_start, bool)
            or not isinstance(quote_end, int)
            or isinstance(quote_end, bool)
            or quote_start < 0
            or quote_end <= quote_start
            or not _valid_sha256(quote_hash)
        ):
            raise invalid
        segment = next(
            (
                item
                for item in evidence
                if item.source_id == source_id
                and item.segment_id == segment_id
                and item.sequence == sequence
                and item.page == page
                and item.text_sha256 == text_sha256
            ),
            None,
        )
        if (
            segment is None
            or quote_end > len(segment.text)
            or _sha256(segment.text[quote_start:quote_end]) != quote_hash
        ):
            raise invalid
        return EvidenceAnswerSourceRef(
            case_id=case_id,
            source_id=source_id,
            segment_id=segment_id,
            sequence=sequence,
            page=page,
            text_sha256=str(text_sha256),
            quote_start=quote_start,
            quote_end=quote_end,
            quote_hash=str(quote_hash),
        )
    except (StopIteration, TypeError, ValueError) as error:
        raise invalid from error


def _validate_authority(
    connection: sqlite3.Connection,
    case_id: str,
    evidence: tuple[RagEvidenceSegment, ...],
) -> None:
    try:
        for item in evidence:
            row = connection.execute(
                """
                SELECT s.case_id, ss.sequence, ss.page, ss.text, ss.text_sha256
                FROM source AS s
                JOIN source_segment AS ss ON ss.source_id = s.id
                WHERE s.id = ? AND ss.id = ? AND s.case_id = ?
                """,
                (item.source_id, item.segment_id, case_id),
            ).fetchone()
            if row is None:
                raise AgentValidationError("Evidence-backed answer authority is invalid")
            page = int(row["page"]) if row["page"] is not None else None
            text = str(row["text"])
            if (
                int(row["sequence"]) != item.sequence
                or page != item.page
                or text != item.text
                or str(row["text_sha256"]) != item.text_sha256
                or _sha256(text) != item.text_sha256
            ):
                raise AgentValidationError("Evidence-backed answer authority is invalid")
    except sqlite3.Error as error:
        raise AgentValidationError(
            "Evidence-backed answer authority could not be validated"
        ) from error


def _optional_reason(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AgentValidationError("Evidence-backed answer uncertainty is invalid")
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= _MAX_UNCERTAINTY_REASON:
        raise AgentValidationError("Evidence-backed answer uncertainty is invalid")
    return cleaned


def _identifier(value: object) -> str:
    if not isinstance(value, str):
        raise AgentValidationError("Evidence-backed answer identifier is invalid")
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= 200:
        raise AgentValidationError("Evidence-backed answer identifier is invalid")
    return cleaned


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: dict[str, object]) -> str:
    canonical = canonical_json_object(value, maximum_bytes=100_000)
    return _sha256(canonical)


def _source_ref_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "case_id",
            "source_id",
            "segment_id",
            "sequence",
            "page",
            "text_sha256",
            "quote_start",
            "quote_end",
            "quote_hash",
        ],
        "properties": {
            "case_id": {"type": "string", "minLength": 1, "maxLength": 100},
            "source_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "segment_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "sequence": {"type": "integer", "minimum": 0},
            "page": {"type": ["integer", "null"], "minimum": 1},
            "text_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "quote_start": {"type": "integer", "minimum": 0},
            "quote_end": {"type": "integer", "minimum": 1},
            "quote_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
    }


def _rag_registry() -> StageRegistry:
    definitions = list(EVIDENCE_EXTRACTION_STAGE_REGISTRY.definitions)
    validate = definitions[-1]
    input_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["case_id", "prior_checkpoint_hashes", "stage_payload"],
        "properties": {
            "case_id": {"type": "string", "minLength": 1, "maxLength": 100},
            "prior_checkpoint_hashes": {
                "type": "array",
                "minItems": 6,
                "maxItems": 6,
                "items": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
            "stage_payload": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question", "tool_result"],
                "properties": {
                    "question": {"type": "string", "minLength": 3, "maxLength": 256},
                    "tool_result": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "tool_name",
                            "risk",
                            "call_count",
                            "context_sha256",
                            "request",
                            "context",
                        ],
                        "properties": {
                            "tool_name": {"const": EVIDENCE_CONTEXT_TOOL_NAME},
                            "risk": {"const": "A"},
                            "call_count": {"const": 1},
                            "context_sha256": {
                                "type": "string",
                                "pattern": "^[0-9a-f]{64}$",
                            },
                            "request": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "retrieval_limit",
                                    "adjacent_before",
                                    "adjacent_after",
                                    "max_text_bytes",
                                    "max_item_text_bytes",
                                    "allowed_trust_labels",
                                ],
                                "properties": {
                                    "retrieval_limit": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": _MAX_RAG_RETRIEVAL_LIMIT,
                                    },
                                    "adjacent_before": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "maximum": _MAX_RAG_ADJACENT_SEGMENTS,
                                    },
                                    "adjacent_after": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "maximum": _MAX_RAG_ADJACENT_SEGMENTS,
                                    },
                                    "max_text_bytes": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": _MAX_RAG_CONTEXT_TEXT_BYTES,
                                    },
                                    "max_item_text_bytes": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": 32_000,
                                    },
                                    "allowed_trust_labels": {
                                        "type": "array",
                                        "maxItems": 1,
                                        "uniqueItems": True,
                                        "items": {
                                            "enum": [label.value for label in EvidenceTrustLabel]
                                        },
                                    },
                                },
                            },
                            "context": {"type": "object"},
                        },
                    },
                },
            },
        },
    }
    statement_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "source_refs"],
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": _MAX_STATEMENT_CHARS},
            "source_refs": {
                "type": "array",
                "minItems": 1,
                "maxItems": _MAX_REFS_PER_STATEMENT,
                "items": _source_ref_schema(),
            },
        },
    }
    output_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "stage",
            "stage_version",
            "checkpoint_schema_version",
            "checkpoint",
        ],
        "properties": {
            "stage": {"const": AgentStageName.VALIDATE.value},
            "stage_version": {"const": _RAG_STAGE_VERSION},
            "checkpoint_schema_version": {"const": _RAG_CHECKPOINT_SCHEMA_VERSION},
            "checkpoint": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "schema_version",
                    "status",
                    "statements",
                    "uncertainty_reason",
                    "tool",
                ],
                "properties": {
                    "schema_version": {"const": EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION},
                    "status": {"enum": [status.value for status in EvidenceAnswerStatus]},
                    "statements": {
                        "type": "array",
                        "maxItems": _MAX_STATEMENTS,
                        "items": statement_schema,
                    },
                    "uncertainty_reason": {
                        "type": ["string", "null"],
                        "maxLength": _MAX_UNCERTAINTY_REASON,
                    },
                    "tool": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "call_count", "context_sha256"],
                        "properties": {
                            "name": {"const": EVIDENCE_CONTEXT_TOOL_NAME},
                            "call_count": {"const": 1},
                            "context_sha256": {
                                "type": "string",
                                "pattern": "^[0-9a-f]{64}$",
                            },
                        },
                    },
                },
            },
        },
    }
    definitions[-1] = replace(
        validate,
        stage_version=_RAG_STAGE_VERSION,
        input_schema_version=_RAG_INPUT_SCHEMA_VERSION,
        output_schema_version=_RAG_OUTPUT_SCHEMA_VERSION,
        checkpoint_schema_version=_RAG_CHECKPOINT_SCHEMA_VERSION,
        input_schema_name="lifecase_evidence_backed_rag_input_v1",
        input_schema_json=canonical_json_object(input_schema, maximum_bytes=20_000),
        output_schema_name="lifecase_evidence_backed_answer_checkpoint_v1",
        output_schema_json=canonical_json_object(output_schema, maximum_bytes=20_000),
        system_instruction=(
            "Answer only from the supplied Case-scoped Evidence Context. Every answer "
            "statement requires exact quote references. Return unknown when evidence is "
            "absent or insufficient. Never invent or reuse a citation from another Case."
        ),
        stage_instruction=(
            "Return status=answered with cited statements, or status=unknown with no "
            "statements and an explicit uncertainty reason. Preserve tool metadata exactly. "
            "For every source reference, copy case/source/segment/sequence/page/text_sha256 "
            "from one supplied Context item. Cite the complete Segment with quote_start=0, "
            "quote_end equal to the Segment text character count, and quote_hash copied from "
            "that item's text_sha256; do not calculate a new hash."
        ),
        allowed_tools=(EVIDENCE_CONTEXT_TOOL_NAME,),
    )
    return StageRegistry(tuple(definitions))


EVIDENCE_BACKED_RAG_STAGE_REGISTRY = _rag_registry()
