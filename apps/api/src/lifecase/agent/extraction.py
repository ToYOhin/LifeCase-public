"""M3-03 evidence-backed warranty extraction contracts and commit handler."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from lifecase.agent.errors import AgentValidationError
from lifecase.agent.models import (
    AgentCheckpoint,
    AgentStageName,
    StageInput,
    checkpoint_payload,
    stage_input_scope_sha256,
)
from lifecase.agent.registry import BUILT_IN_STAGE_REGISTRY, StageRegistry, validate_stage_output
from lifecase.claims import (
    ClaimCreatedBy,
    ClaimSourceInput,
    ClaimSourceRelation,
    ClaimStatus,
    ClaimValue,
    CreateClaimCommand,
)
from lifecase.claims.models import ClaimCommandValidationError
from lifecase.claims.service import ClaimService, ClaimServiceError
from lifecase.providers.models import canonical_json_object

WARRANTY_EXTRACTION_SCHEMA_VERSION = "warranty_extraction.v1"
WARRANTY_EXTRACTION_OUTPUT_SCHEMA_NAME = "lifecase_warranty_extraction_checkpoint_v1"
_EXTRACT_STAGE_VERSION = "extract.warranty.v1"
_EXTRACT_INPUT_SCHEMA_VERSION = "extract.warranty.input.v1"
_EXTRACT_OUTPUT_SCHEMA_VERSION = "extract.warranty.output.v1"
_EXTRACT_CHECKPOINT_SCHEMA_VERSION = "extract.warranty.checkpoint.v1"
_MAX_EVIDENCE_SEGMENTS = 32
_MAX_SOURCE_REFS = 8
_MAX_SEGMENT_TEXT = 16_000
_MAX_UNCERTAINTY_REASON = 900
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class WarrantyExtractionField(StrEnum):
    """The six reviewed M3-03 warranty extraction fields."""

    MERCHANT_OR_MANUFACTURER = "merchant_or_manufacturer"
    PRODUCT_NAME = "product_name"
    PURCHASE_DATE = "purchase_date"
    ORDER_NUMBER = "order_number"
    AMOUNT = "amount"
    SERIAL_NUMBER = "serial_number"


class ExtractionOrigin(StrEnum):
    """Ephemeral extraction semantics, not a durable Claim status."""

    EXTRACTED = "extracted"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class WarrantyExtractionDiagnosticCode(StrEnum):
    """Closed, content-free business validation reasons for evaluation evidence."""

    CHECKPOINT_SHAPE = "checkpoint_shape"
    FACT_SET = "fact_set"
    FACT_SHAPE = "fact_shape"
    ORIGIN_CONTRACT = "origin_contract"
    FIELD_VALUE = "field_value"
    SOURCE_REF_SHAPE = "source_ref_shape"
    SOURCE_REF_DUPLICATE = "source_ref_duplicate"
    SOURCE_REF_AUTHORITY = "source_ref_authority"
    QUOTE_AUTHORITY = "quote_authority"


@dataclass(frozen=True, slots=True)
class WarrantyExtractionDiagnostic:
    """One safe code tied only to a reviewed field name, never a candidate value."""

    code: WarrantyExtractionDiagnosticCode
    field: WarrantyExtractionField | None = None

    @property
    def safe_code(self) -> str:
        scope = "checkpoint" if self.field is None else self.field.value
        return f"extract.{scope}.{self.code.value}"


class _WarrantyExtractionValidationError(AgentValidationError):
    def __init__(self, diagnostic: WarrantyExtractionDiagnostic) -> None:
        super().__init__("Extraction candidate is invalid")
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class ExtractionEvidenceSegment:
    """One explicitly supplied, authoritative Evidence Context segment."""

    case_id: str
    source_id: str
    segment_id: str
    sequence: int
    page: int | None
    text: str
    text_sha256: str

    def __post_init__(self) -> None:
        case_id = _bounded_identifier(self.case_id)
        source_id = _bounded_identifier(self.source_id, maximum=200)
        segment_id = _bounded_identifier(self.segment_id, maximum=200)
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise AgentValidationError("Extraction evidence is invalid")
        if self.page is not None and (
            not isinstance(self.page, int) or isinstance(self.page, bool) or self.page < 1
        ):
            raise AgentValidationError("Extraction evidence is invalid")
        if not isinstance(self.text, str) or not 1 <= len(self.text) <= _MAX_SEGMENT_TEXT:
            raise AgentValidationError("Extraction evidence is invalid")
        if not _valid_sha256(self.text_sha256) or _sha256(self.text) != self.text_sha256:
            raise AgentValidationError("Extraction evidence is invalid")
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "segment_id", segment_id)

    def to_json(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "source_id": self.source_id,
            "segment_id": self.segment_id,
            "sequence": self.sequence,
            "page": self.page,
            "text": self.text,
            "text_sha256": self.text_sha256,
        }


@dataclass(frozen=True, slots=True)
class ExtractionSourceRef:
    """A model-proposed excerpt awaiting authoritative revalidation."""

    source_id: str
    segment_id: str
    segment_text_sha256: str
    quote_start: int
    quote_end: int
    quote_hash: str

    def to_claim_source(self, relation: ClaimSourceRelation) -> ClaimSourceInput:
        return ClaimSourceInput(
            source_id=self.source_id,
            segment_id=self.segment_id,
            segment_text_sha256=self.segment_text_sha256,
            quote_start=self.quote_start,
            quote_end=self.quote_end,
            quote_hash=self.quote_hash,
            relation=relation,
        )


@dataclass(frozen=True, slots=True)
class ExtractionFact:
    """One validated field disposition from the model checkpoint."""

    field: WarrantyExtractionField
    origin: ExtractionOrigin
    value: ClaimValue | None
    confidence: float | None
    uncertainty_reason: str | None
    source_refs: tuple[ExtractionSourceRef, ...]


@dataclass(frozen=True, slots=True)
class PreparedWarrantyExtraction:
    """Validated commands ready for one Agent-owned commit transaction."""

    facts: tuple[ExtractionFact, ...]
    evidence: tuple[ExtractionEvidenceSegment, ...]
    commands: tuple[CreateClaimCommand, ...]


def warranty_extraction_stage_input(
    case_id: str,
    evidence: tuple[ExtractionEvidenceSegment, ...],
    *,
    prior_checkpoint_hashes: tuple[str, ...] = (),
) -> StageInput:
    """Build the bounded, hashed M3-03 extract-stage input."""

    _bounded_identifier(case_id)
    if (
        not isinstance(evidence, tuple)
        or len(evidence) > _MAX_EVIDENCE_SEGMENTS
        or not all(isinstance(item, ExtractionEvidenceSegment) for item in evidence)
        or any(item.case_id != case_id for item in evidence)
        or len({item.segment_id for item in evidence}) != len(evidence)
    ):
        raise AgentValidationError("Extraction evidence is invalid")
    if not isinstance(prior_checkpoint_hashes, tuple) or any(
        not _valid_sha256(value) for value in prior_checkpoint_hashes
    ):
        raise AgentValidationError("Extraction checkpoint chain is invalid")
    payload_json = canonical_json_object(
        {
            "case_id": case_id,
            "prior_checkpoint_hashes": list(prior_checkpoint_hashes),
            "stage_payload": {"evidence": [item.to_json() for item in evidence]},
        },
        maximum_bytes=100_000,
    )
    return StageInput(
        case_id=case_id,
        stage=AgentStageName.EXTRACT,
        stage_version=_EXTRACT_STAGE_VERSION,
        input_schema_version=_EXTRACT_INPUT_SCHEMA_VERSION,
        input_scope_sha256=stage_input_scope_sha256(
            case_id,
            AgentStageName.EXTRACT,
            _EXTRACT_STAGE_VERSION,
            _EXTRACT_INPUT_SCHEMA_VERSION,
            payload_json,
        ),
        payload_json=payload_json,
    )


class WarrantyExtractionCheckpointHandler:
    """Validate M3-03 output and commit Claims with the Agent checkpoint."""

    def normalize(
        self,
        *,
        connection: sqlite3.Connection,
        case_id: str,
        stage_input_json: str,
        checkpoint: AgentCheckpoint,
    ) -> AgentCheckpoint:
        if checkpoint.stage is not AgentStageName.EXTRACT:
            return checkpoint
        evidence = _parse_input(stage_input_json, case_id)
        _validate_authority(connection, case_id, evidence)
        normalized = _normalize_source_refs(checkpoint.payload_json, evidence)
        payload_json, payload_sha256 = checkpoint_payload(normalized)
        return replace(
            checkpoint,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
        )

    def prepare(
        self,
        *,
        connection: sqlite3.Connection,
        run_id: str,
        case_id: str,
        stage_input_json: str,
        checkpoint: AgentCheckpoint,
    ) -> object:
        if checkpoint.stage is not AgentStageName.EXTRACT:
            return None
        evidence = _parse_input(stage_input_json, case_id)
        _validate_authority(connection, case_id, evidence)
        facts = _parse_checkpoint(checkpoint.payload_json, evidence)
        commands = tuple(
            _claim_command(case_id=case_id, run_id=run_id, fact=fact)
            for fact in facts
            if fact.origin is not ExtractionOrigin.UNKNOWN
        )
        return PreparedWarrantyExtraction(facts=facts, evidence=evidence, commands=commands)

    def commit(
        self,
        *,
        connection: sqlite3.Connection,
        run_id: str,
        case_id: str,
        checkpoint: AgentCheckpoint,
        prepared: object,
    ) -> None:
        if checkpoint.stage is not AgentStageName.EXTRACT:
            return
        if not isinstance(prepared, PreparedWarrantyExtraction):
            raise AgentValidationError("Extraction checkpoint is invalid")
        try:
            _validate_authority(connection, case_id, prepared.evidence)
            ClaimService(connection).create_claims_in_transaction(prepared.commands)
        except (ClaimCommandValidationError, ClaimServiceError) as error:
            raise AgentValidationError("Extraction authority is invalid") from error


def _claim_command(
    *,
    case_id: str,
    run_id: str,
    fact: ExtractionFact,
) -> CreateClaimCommand:
    if fact.value is None or fact.confidence is None:
        raise AgentValidationError("Extraction fact is invalid")
    if fact.origin is ExtractionOrigin.EXTRACTED:
        status = ClaimStatus.CANDIDATE
        relation = ClaimSourceRelation.SUPPORTS
        reason = "Agent extraction origin=extracted with authoritative citation"
    elif fact.origin is ExtractionOrigin.INFERRED:
        status = ClaimStatus.INFERRED
        relation = ClaimSourceRelation.CONTEXT
        reason = f"Agent inference basis: {fact.uncertainty_reason}"
    else:
        raise AgentValidationError("Extraction fact is invalid")
    try:
        return CreateClaimCommand(
            case_id=case_id,
            field=fact.field.value,
            value=fact.value,
            status=status,
            confidence=fact.confidence,
            created_by=ClaimCreatedBy.AGENT,
            actor=run_id,
            reason=reason,
            sources=tuple(source.to_claim_source(relation) for source in fact.source_refs),
        )
    except ClaimCommandValidationError as error:
        raise AgentValidationError("Extraction fact is invalid") from error


def _parse_input(
    payload_json: str,
    case_id: str,
) -> tuple[ExtractionEvidenceSegment, ...]:
    invalid = AgentValidationError("Extraction input is invalid")
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
        if set(stage_payload) != {"evidence"} or not isinstance(stage_payload["evidence"], list):
            raise invalid
        raw_evidence = stage_payload["evidence"]
        if len(raw_evidence) > _MAX_EVIDENCE_SEGMENTS:
            raise invalid
        evidence = tuple(_parse_evidence(item) for item in raw_evidence)
        if any(item.case_id != case_id for item in evidence):
            raise invalid
        if len({item.segment_id for item in evidence}) != len(evidence):
            raise invalid
        return evidence
    except (AgentValidationError, KeyError, TypeError, ValueError) as error:
        if error is invalid:
            raise
        raise invalid from error


def _parse_evidence(value: object) -> ExtractionEvidenceSegment:
    if not isinstance(value, dict) or set(value) != {
        "case_id",
        "source_id",
        "segment_id",
        "sequence",
        "page",
        "text",
        "text_sha256",
    }:
        raise AgentValidationError("Extraction evidence is invalid")
    return ExtractionEvidenceSegment(
        case_id=value["case_id"],
        source_id=value["source_id"],
        segment_id=value["segment_id"],
        sequence=value["sequence"],
        page=value["page"],
        text=value["text"],
        text_sha256=value["text_sha256"],
    )


def _parse_checkpoint(
    payload_json: str,
    evidence: tuple[ExtractionEvidenceSegment, ...],
) -> tuple[ExtractionFact, ...]:
    invalid = _validation_failure(WarrantyExtractionDiagnosticCode.CHECKPOINT_SHAPE)
    try:
        value = json.loads(payload_json)
        if not isinstance(value, dict) or set(value) != {"schema_version", "facts"}:
            raise invalid
        if value["schema_version"] != WARRANTY_EXTRACTION_SCHEMA_VERSION or not isinstance(
            value["facts"], list
        ):
            raise invalid
        facts = tuple(_parse_fact(item, evidence) for item in value["facts"])
        if len(facts) != len(WarrantyExtractionField) or {fact.field for fact in facts} != set(
            WarrantyExtractionField
        ):
            raise _validation_failure(WarrantyExtractionDiagnosticCode.FACT_SET)
        field_order = tuple(WarrantyExtractionField)
        return tuple(sorted(facts, key=lambda fact: field_order.index(fact.field)))
    except _WarrantyExtractionValidationError:
        raise
    except (AgentValidationError, KeyError, TypeError, ValueError) as error:
        if error is invalid:
            raise
        raise invalid from error


def _parse_fact(
    value: object,
    evidence: tuple[ExtractionEvidenceSegment, ...],
) -> ExtractionFact:
    field = _diagnostic_field(value)
    invalid = _validation_failure(WarrantyExtractionDiagnosticCode.FACT_SHAPE, field)
    if not isinstance(value, dict) or set(value) != {
        "field",
        "origin",
        "value",
        "confidence",
        "uncertainty_reason",
        "source_refs",
    }:
        raise invalid
    try:
        field = WarrantyExtractionField(value["field"])
        origin = ExtractionOrigin(value["origin"])
        confidence = _confidence(value["confidence"])
        reason = _uncertainty_reason(value["uncertainty_reason"])
    except (AgentValidationError, TypeError, ValueError) as error:
        raise invalid from error
    try:
        raw_refs = value["source_refs"]
        if not isinstance(raw_refs, list) or len(raw_refs) > _MAX_SOURCE_REFS:
            raise _validation_failure(WarrantyExtractionDiagnosticCode.SOURCE_REF_SHAPE, field)
        source_refs = tuple(_parse_source_ref(item, evidence, field=field) for item in raw_refs)
        keys = {
            (item.source_id, item.segment_id, item.quote_start, item.quote_end)
            for item in source_refs
        }
        if len(keys) != len(source_refs):
            raise _validation_failure(WarrantyExtractionDiagnosticCode.SOURCE_REF_DUPLICATE, field)
        if origin is ExtractionOrigin.UNKNOWN:
            if (
                value["value"] is not None
                or confidence is not None
                or reason is None
                or source_refs
            ):
                raise _validation_failure(WarrantyExtractionDiagnosticCode.ORIGIN_CONTRACT, field)
            claim_value = None
        else:
            if value["value"] is None or confidence is None or not source_refs:
                raise _validation_failure(WarrantyExtractionDiagnosticCode.ORIGIN_CONTRACT, field)
            if origin is ExtractionOrigin.INFERRED and reason is None:
                raise _validation_failure(WarrantyExtractionDiagnosticCode.ORIGIN_CONTRACT, field)
            try:
                claim_value = ClaimValue.from_input(value["value"])
                _validate_field_value(field, claim_value.to_python())
            except (
                AgentValidationError,
                ClaimCommandValidationError,
                TypeError,
                ValueError,
            ) as error:
                raise _validation_failure(
                    WarrantyExtractionDiagnosticCode.FIELD_VALUE, field
                ) from error
        return ExtractionFact(
            field=field,
            origin=origin,
            value=claim_value,
            confidence=confidence,
            uncertainty_reason=reason,
            source_refs=source_refs,
        )
    except _WarrantyExtractionValidationError:
        raise
    except (ClaimCommandValidationError, KeyError, TypeError, ValueError) as error:
        raise invalid from error


def _parse_source_ref(
    value: object,
    evidence: tuple[ExtractionEvidenceSegment, ...],
    *,
    field: WarrantyExtractionField,
) -> ExtractionSourceRef:
    invalid = _validation_failure(WarrantyExtractionDiagnosticCode.SOURCE_REF_SHAPE, field)
    if not isinstance(value, dict) or set(value) != {
        "source_id",
        "segment_id",
        "segment_text_sha256",
        "quote_start",
        "quote_end",
        "quote_hash",
    }:
        raise invalid
    try:
        source_id = _bounded_identifier(value["source_id"], maximum=200)
        segment_id = _bounded_identifier(value["segment_id"], maximum=200)
        segment_hash = value["segment_text_sha256"]
        quote_start = value["quote_start"]
        quote_end = value["quote_end"]
        quote_hash = value["quote_hash"]
        if (
            not _valid_sha256(segment_hash)
            or not _valid_sha256(quote_hash)
            or not isinstance(quote_start, int)
            or isinstance(quote_start, bool)
            or not isinstance(quote_end, int)
            or isinstance(quote_end, bool)
            or quote_start < 0
            or quote_end <= quote_start
        ):
            raise invalid
        segment = next(
            (
                item
                for item in evidence
                if item.source_id == source_id
                and item.segment_id == segment_id
                and item.text_sha256 == segment_hash
            ),
            None,
        )
        if segment is None:
            raise _validation_failure(WarrantyExtractionDiagnosticCode.SOURCE_REF_AUTHORITY, field)
        if (
            quote_end > len(segment.text)
            or _sha256(segment.text[quote_start:quote_end]) != quote_hash
        ):
            raise _validation_failure(WarrantyExtractionDiagnosticCode.QUOTE_AUTHORITY, field)
        return ExtractionSourceRef(
            source_id=source_id,
            segment_id=segment_id,
            segment_text_sha256=segment_hash,
            quote_start=quote_start,
            quote_end=quote_end,
            quote_hash=quote_hash,
        )
    except _WarrantyExtractionValidationError:
        raise
    except (StopIteration, TypeError, ValueError) as error:
        raise invalid from error


def _normalize_source_refs(
    payload_json: str,
    evidence: tuple[ExtractionEvidenceSegment, ...],
) -> dict[str, object]:
    invalid = _validation_failure(WarrantyExtractionDiagnosticCode.CHECKPOINT_SHAPE)
    try:
        value = json.loads(payload_json)
        if not isinstance(value, dict) or set(value) != {"schema_version", "facts"}:
            raise invalid
        facts = value.get("facts")
        if not isinstance(facts, list):
            raise invalid
        for raw_fact in facts:
            field = _diagnostic_field(raw_fact)
            if not isinstance(raw_fact, dict):
                raise _validation_failure(WarrantyExtractionDiagnosticCode.FACT_SHAPE, field)
            raw_refs = raw_fact.get("source_refs")
            if not isinstance(raw_refs, list):
                raise _validation_failure(WarrantyExtractionDiagnosticCode.SOURCE_REF_SHAPE, field)
            raw_fact["source_refs"] = [
                _normalized_source_ref(raw_ref, evidence, field=field) for raw_ref in raw_refs
            ]
        return value
    except _WarrantyExtractionValidationError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise invalid from error


def _normalized_source_ref(
    value: object,
    evidence: tuple[ExtractionEvidenceSegment, ...],
    *,
    field: WarrantyExtractionField | None,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "source_id",
        "segment_id",
        "segment_text_sha256",
        "quote_start",
        "quote_end",
        "quote_hash",
    }:
        raise _validation_failure(WarrantyExtractionDiagnosticCode.SOURCE_REF_SHAPE, field)
    try:
        source_id = _bounded_identifier(value["source_id"], maximum=200)
        segment_id = _bounded_identifier(value["segment_id"], maximum=200)
    except (AgentValidationError, KeyError, TypeError, ValueError) as error:
        raise _validation_failure(
            WarrantyExtractionDiagnosticCode.SOURCE_REF_SHAPE, field
        ) from error
    segment_hash = value.get("segment_text_sha256")
    segment = next(
        (
            item
            for item in evidence
            if item.source_id == source_id
            and item.segment_id == segment_id
            and item.text_sha256 == segment_hash
        ),
        None,
    )
    if segment is None:
        raise _validation_failure(WarrantyExtractionDiagnosticCode.SOURCE_REF_AUTHORITY, field)
    quote_start = value.get("quote_start")
    quote_end = value.get("quote_end")
    quote_hash = value.get("quote_hash")
    if (
        isinstance(quote_start, int)
        and not isinstance(quote_start, bool)
        and isinstance(quote_end, int)
        and not isinstance(quote_end, bool)
        and isinstance(quote_hash, str)
        and 0 <= quote_start < quote_end <= len(segment.text)
        and _sha256(segment.text[quote_start:quote_end]) == quote_hash
    ):
        return {
            "source_id": segment.source_id,
            "segment_id": segment.segment_id,
            "segment_text_sha256": segment.text_sha256,
            "quote_start": quote_start,
            "quote_end": quote_end,
            "quote_hash": quote_hash,
        }
    return {
        "source_id": segment.source_id,
        "segment_id": segment.segment_id,
        "segment_text_sha256": segment.text_sha256,
        "quote_start": 0,
        "quote_end": len(segment.text),
        "quote_hash": segment.text_sha256,
    }


def diagnose_warranty_extraction_candidate(
    stage_input_json: str,
    output_json: str,
) -> tuple[str, ...]:
    """Run canonical extraction validation and reveal only one closed safe failure code."""

    try:
        raw_input = json.loads(stage_input_json)
        if not isinstance(raw_input, dict) or not isinstance(raw_input.get("case_id"), str):
            return ()
        case_id = raw_input["case_id"]
        evidence = _parse_input(stage_input_json, case_id)
        definition = EVIDENCE_EXTRACTION_STAGE_REGISTRY.get(AgentStageName.EXTRACT)
        checkpoint = validate_stage_output(
            definition,
            output_json,
            input_scope_sha256="0" * 64,
        )
        normalized = _normalize_source_refs(checkpoint.payload_json, evidence)
        normalized_payload_json, _ = checkpoint_payload(normalized)
        _parse_checkpoint(normalized_payload_json, evidence)
    except _WarrantyExtractionValidationError as error:
        return (error.diagnostic.safe_code,)
    except (AgentValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return ()
    return ()


def _diagnostic_field(value: object) -> WarrantyExtractionField | None:
    if not isinstance(value, dict):
        return None
    raw_field = value.get("field")
    if not isinstance(raw_field, str):
        return None
    try:
        return WarrantyExtractionField(raw_field)
    except ValueError:
        return None


def _validation_failure(
    code: WarrantyExtractionDiagnosticCode,
    field: WarrantyExtractionField | None = None,
) -> _WarrantyExtractionValidationError:
    return _WarrantyExtractionValidationError(WarrantyExtractionDiagnostic(code=code, field=field))


def _validate_authority(
    connection: sqlite3.Connection,
    case_id: str,
    evidence: tuple[ExtractionEvidenceSegment, ...],
) -> None:
    try:
        for item in evidence:
            row = connection.execute(
                """
                SELECT s.case_id, ss.sequence, ss.page, ss.text, ss.text_sha256
                FROM source AS s
                JOIN source_segment AS ss ON ss.source_id = s.id
                WHERE s.id = ? AND ss.id = ?
                """,
                (item.source_id, item.segment_id),
            ).fetchone()
            if row is None:
                raise AgentValidationError("Extraction authority is invalid")
            text = str(row["text"])
            page = int(row["page"]) if row["page"] is not None else None
            if (
                str(row["case_id"]) != case_id
                or int(row["sequence"]) != item.sequence
                or page != item.page
                or text != item.text
                or str(row["text_sha256"]) != item.text_sha256
                or _sha256(text) != item.text_sha256
            ):
                raise AgentValidationError("Extraction authority is invalid")
    except sqlite3.Error as error:
        raise AgentValidationError("Extraction authority could not be validated") from error


def _validate_field_value(field: WarrantyExtractionField, value: object) -> None:
    if field is WarrantyExtractionField.AMOUNT:
        if (
            not isinstance(value, dict)
            or set(value) != {"amount_minor", "currency"}
            or not isinstance(value["amount_minor"], int)
            or isinstance(value["amount_minor"], bool)
            or value["amount_minor"] < 0
            or not isinstance(value["currency"], str)
            or _CURRENCY_PATTERN.fullmatch(value["currency"]) is None
        ):
            raise AgentValidationError("Extraction amount is invalid")
        return
    if not isinstance(value, str) or len(value) > 500:
        raise AgentValidationError("Extraction field value is invalid")
    if field is WarrantyExtractionField.PURCHASE_DATE:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise AgentValidationError("Extraction date is invalid")
        except ValueError as error:
            raise AgentValidationError("Extraction date is invalid") from error


def _confidence(value: object) -> float | None:
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0.0 <= float(value) <= 1.0
    ):
        raise AgentValidationError("Extraction confidence is invalid")
    return float(value)


def _uncertainty_reason(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AgentValidationError("Extraction uncertainty is invalid")
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= _MAX_UNCERTAINTY_REASON:
        raise AgentValidationError("Extraction uncertainty is invalid")
    return cleaned


def _bounded_identifier(value: object, *, maximum: int = 100) -> str:
    if not isinstance(value, str):
        raise AgentValidationError("Extraction identifier is invalid")
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= maximum:
        raise AgentValidationError("Extraction identifier is invalid")
    return cleaned


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_ref_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "source_id",
            "segment_id",
            "segment_text_sha256",
            "quote_start",
            "quote_end",
            "quote_hash",
        ],
        "properties": {
            "source_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "segment_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "segment_text_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "quote_start": {"type": "integer", "minimum": 0},
            "quote_end": {"type": "integer", "minimum": 1},
            "quote_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
    }


def _extraction_registry() -> StageRegistry:
    definitions = list(BUILT_IN_STAGE_REGISTRY.definitions)
    extract = definitions[0]
    input_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["case_id", "prior_checkpoint_hashes", "stage_payload"],
        "properties": {
            "case_id": {"type": "string", "minLength": 1, "maxLength": 100},
            "prior_checkpoint_hashes": {
                "type": "array",
                "maxItems": 7,
                "items": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
            "stage_payload": {
                "type": "object",
                "additionalProperties": False,
                "required": ["evidence"],
                "properties": {
                    "evidence": {
                        "type": "array",
                        "maxItems": _MAX_EVIDENCE_SEGMENTS,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "case_id",
                                "source_id",
                                "segment_id",
                                "sequence",
                                "page",
                                "text",
                                "text_sha256",
                            ],
                            "properties": {
                                "case_id": {"type": "string", "minLength": 1},
                                "source_id": {"type": "string", "minLength": 1},
                                "segment_id": {"type": "string", "minLength": 1},
                                "sequence": {"type": "integer", "minimum": 0},
                                "page": {"type": ["integer", "null"], "minimum": 1},
                                "text": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": _MAX_SEGMENT_TEXT,
                                },
                                "text_sha256": {
                                    "type": "string",
                                    "pattern": "^[0-9a-f]{64}$",
                                },
                            },
                        },
                    }
                },
            },
        },
    }
    fact_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "field",
            "origin",
            "value",
            "confidence",
            "uncertainty_reason",
            "source_refs",
        ],
        "properties": {
            "field": {"enum": [field.value for field in WarrantyExtractionField]},
            "origin": {"enum": [origin.value for origin in ExtractionOrigin]},
            "value": {},
            "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "uncertainty_reason": {
                "type": ["string", "null"],
                "maxLength": _MAX_UNCERTAINTY_REASON,
            },
            "source_refs": {
                "type": "array",
                "maxItems": _MAX_SOURCE_REFS,
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
            "stage": {"const": AgentStageName.EXTRACT.value},
            "stage_version": {"const": _EXTRACT_STAGE_VERSION},
            "checkpoint_schema_version": {"const": _EXTRACT_CHECKPOINT_SCHEMA_VERSION},
            "checkpoint": {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "facts"],
                "properties": {
                    "schema_version": {"const": WARRANTY_EXTRACTION_SCHEMA_VERSION},
                    "facts": {
                        "type": "array",
                        "minItems": len(WarrantyExtractionField),
                        "maxItems": len(WarrantyExtractionField),
                        "items": fact_schema,
                    },
                },
            },
        },
    }
    definitions[0] = replace(
        extract,
        stage_version=_EXTRACT_STAGE_VERSION,
        input_schema_version=_EXTRACT_INPUT_SCHEMA_VERSION,
        output_schema_version=_EXTRACT_OUTPUT_SCHEMA_VERSION,
        checkpoint_schema_version=_EXTRACT_CHECKPOINT_SCHEMA_VERSION,
        input_schema_name="lifecase_warranty_extraction_input_v1",
        input_schema_json=canonical_json_object(input_schema, maximum_bytes=20_000),
        output_schema_name=WARRANTY_EXTRACTION_OUTPUT_SCHEMA_NAME,
        output_schema_json=canonical_json_object(output_schema, maximum_bytes=20_000),
        stage_instruction=(
            "# Task\n"
            "Return exactly one fact for each of these six fields: "
            "merchant_or_manufacturer, product_name, purchase_date, order_number, amount, "
            "and serial_number.\n"
            "# Field disposition contract\n"
            "Use origin=extracted only when the evidence directly states the value. Use "
            "origin=inferred only when the evidence supports an inference; include a non-empty "
            "uncertainty_reason. A serial prefix is inferred rather than a complete serial "
            "number. Use origin=unknown when the evidence does not support a value; set "
            "value=null, confidence=null, provide a non-empty uncertainty_reason, and use an "
            "empty source_refs array. Amount values use integer amount_minor plus a three-letter "
            "currency. Purchase dates use YYYY-MM-DD.\n"
            "# Citation contract\n"
            "Every extracted or inferred fact needs at least one source_ref copied from one "
            "supplied evidence object. Cite that entire evidence Segment: copy source_id and "
            "segment_id, copy evidence.text_sha256 into segment_text_sha256, set quote_start=0, "
            "set quote_end to the number of Unicode characters in evidence.text, and set "
            "quote_hash to that evidence.text_sha256. Do not generate or calculate a SHA-256 "
            "value and do not cite a partial excerpt."
        ),
    )
    return StageRegistry(tuple(definitions))


EVIDENCE_EXTRACTION_STAGE_REGISTRY = _extraction_registry()
