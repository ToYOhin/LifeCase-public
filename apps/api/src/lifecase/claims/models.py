"""Typed Claim provenance values and command contracts."""

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

type ClaimJson = str | int | bool | list[ClaimJson] | dict[str, ClaimJson]

_FIELD_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_OBJECT_KEY_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_MAX_VALUE_BYTES = 4096
_MAX_VALUE_DEPTH = 8
_MAX_COLLECTION_ITEMS = 50
_MAX_SAFE_INTEGER = 9_007_199_254_740_991


class ClaimCommandValidationError(ValueError):
    """Raised when a Claim command violates its public boundary."""


class ClaimStatus(StrEnum):
    """Durable lifecycle states for one Claim."""

    CANDIDATE = "candidate"
    INFERRED = "inferred"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    REJECTED = "rejected"


ACTIVE_CLAIM_STATUSES = frozenset({ClaimStatus.CANDIDATE, ClaimStatus.INFERRED})
CONFIRMABLE_CLAIM_STATUSES = ACTIVE_CLAIM_STATUSES
CORRECTABLE_CLAIM_STATUSES = frozenset(
    {
        ClaimStatus.CANDIDATE,
        ClaimStatus.INFERRED,
        ClaimStatus.CONFIRMED,
        ClaimStatus.CORRECTED,
    }
)
REJECTABLE_CLAIM_STATUSES = CORRECTABLE_CLAIM_STATUSES
SOURCE_ATTACHABLE_CLAIM_STATUSES = CORRECTABLE_CLAIM_STATUSES


class ClaimCreatedBy(StrEnum):
    """Typed origin of a Claim proposal."""

    USER = "user"
    RULE = "rule"
    AGENT = "agent"


class ClaimSourceRelation(StrEnum):
    """Meaning of a source excerpt relative to a Claim."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class ClaimConfirmationBasis(StrEnum):
    """Authority used to confirm a Claim."""

    EVIDENCE = "evidence"
    USER = "user"


class ClaimEventType(StrEnum):
    """Append-only Claim history event types."""

    CREATED = "created"
    SOURCE_ATTACHED = "source_attached"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ClaimValue:
    """Canonical bounded JSON plus exact and comparison hashes."""

    json_text: str
    sha256: str
    comparison_sha256: str

    def __post_init__(self) -> None:
        try:
            cleaned = _clean_json_value(json.loads(self.json_text), depth=0)
        except (json.JSONDecodeError, TypeError) as error:
            raise ClaimCommandValidationError("Claim value is invalid") from error
        canonical = _canonical_json(cleaned)
        if (
            canonical != self.json_text
            or len(canonical.encode("utf-8")) > _MAX_VALUE_BYTES
            or self.sha256 != _sha256_text(canonical)
            or self.comparison_sha256 != _sha256_text(_canonical_json(_comparison_value(cleaned)))
        ):
            raise ClaimCommandValidationError("Claim value is invalid")

    @classmethod
    def from_input(cls, value: object) -> "ClaimValue":
        cleaned = _clean_json_value(value, depth=0)
        json_text = _canonical_json(cleaned)
        if len(json_text.encode("utf-8")) > _MAX_VALUE_BYTES:
            raise ClaimCommandValidationError("Claim value is invalid")
        comparison = _comparison_value(cleaned)
        return cls(
            json_text=json_text,
            sha256=_sha256_text(json_text),
            comparison_sha256=_sha256_text(_canonical_json(comparison)),
        )

    @classmethod
    def from_storage(
        cls,
        json_text: str,
        sha256: str,
        comparison_sha256: str,
    ) -> "ClaimValue":
        return cls(
            json_text=json_text,
            sha256=sha256,
            comparison_sha256=comparison_sha256,
        )

    def to_python(self) -> ClaimJson:
        """Return a detached JSON-compatible value."""

        return cast(ClaimJson, json.loads(self.json_text))


def validate_case_id(value: object) -> str:
    return _bounded_text(value, maximum=200, message="Case ID is invalid")


def validate_claim_id(value: object) -> str:
    return _bounded_text(value, maximum=200, message="Claim ID is invalid")


def validate_field(value: object) -> str:
    field = _bounded_text(value, maximum=64, message="Claim field is invalid")
    if _FIELD_PATTERN.fullmatch(field) is None:
        raise ClaimCommandValidationError("Claim field is invalid")
    return field


def validate_actor(value: object) -> str:
    return _bounded_text(value, maximum=120, message="Claim metadata is invalid")


def validate_reason(value: object) -> str:
    return _bounded_text(value, maximum=1000, message="Claim metadata is invalid")


def validate_version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ClaimCommandValidationError("Expected version is invalid")
    return value


def _clean_json_value(value: object, *, depth: int) -> ClaimJson:
    if depth > _MAX_VALUE_DEPTH:
        raise ClaimCommandValidationError("Claim value is invalid")
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ClaimCommandValidationError("Claim value is invalid")
        return value
    if isinstance(value, str):
        cleaned = unicodedata.normalize("NFKC", value).strip()
        if not cleaned:
            raise ClaimCommandValidationError("Claim value is invalid")
        return cleaned
    if isinstance(value, list):
        if not 1 <= len(value) <= _MAX_COLLECTION_ITEMS:
            raise ClaimCommandValidationError("Claim value is invalid")
        return [_clean_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if not 1 <= len(value) <= _MAX_COLLECTION_ITEMS:
            raise ClaimCommandValidationError("Claim value is invalid")
        cleaned_object: dict[str, ClaimJson] = {}
        for key, item in value.items():
            if not isinstance(key, str) or _OBJECT_KEY_PATTERN.fullmatch(key) is None:
                raise ClaimCommandValidationError("Claim value is invalid")
            cleaned_object[key] = _clean_json_value(item, depth=depth + 1)
        return cleaned_object
    raise ClaimCommandValidationError("Claim value is invalid")


def _comparison_value(value: ClaimJson) -> ClaimJson:
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, list):
        return [_comparison_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _comparison_value(item) for key, item in value.items()}
    return value


def _canonical_json(value: ClaimJson) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_text(value: object, *, maximum: int, message: str) -> str:
    if not isinstance(value, str):
        raise ClaimCommandValidationError(message)
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= maximum:
        raise ClaimCommandValidationError(message)
    return cleaned


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _valid_confidence(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


@dataclass(frozen=True, slots=True)
class ClaimSourceInput:
    """A bounded source excerpt reference awaiting authoritative validation."""

    source_id: str
    segment_id: str
    segment_text_sha256: str
    quote_start: int
    quote_end: int
    quote_hash: str
    relation: ClaimSourceRelation

    def __post_init__(self) -> None:
        source_id = _bounded_text(self.source_id, maximum=200, message="Claim source is invalid")
        segment_id = _bounded_text(self.segment_id, maximum=200, message="Claim source is invalid")
        if not _valid_sha256(self.segment_text_sha256) or not _valid_sha256(self.quote_hash):
            raise ClaimCommandValidationError("Claim source is invalid")
        if (
            not isinstance(self.quote_start, int)
            or isinstance(self.quote_start, bool)
            or not isinstance(self.quote_end, int)
            or isinstance(self.quote_end, bool)
            or self.quote_start < 0
            or self.quote_end <= self.quote_start
        ):
            raise ClaimCommandValidationError("Claim source is invalid")
        if not isinstance(self.relation, ClaimSourceRelation):
            raise ClaimCommandValidationError("Claim source is invalid")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "segment_id", segment_id)


@dataclass(frozen=True, slots=True)
class CreateClaimCommand:
    """Validated intent to create one candidate or inferred Claim."""

    case_id: str
    field: str
    value: ClaimValue
    status: ClaimStatus
    confidence: float | None
    created_by: ClaimCreatedBy
    actor: str
    reason: str
    sources: tuple[ClaimSourceInput, ...] = ()

    def __post_init__(self) -> None:
        case_id = validate_case_id(self.case_id)
        field = validate_field(self.field)
        if not isinstance(self.value, ClaimValue):
            raise ClaimCommandValidationError("Claim value is invalid")
        if not isinstance(self.status, ClaimStatus) or self.status not in ACTIVE_CLAIM_STATUSES:
            raise ClaimCommandValidationError("Initial Claim status is invalid")
        if self.confidence is not None and not _valid_confidence(self.confidence):
            raise ClaimCommandValidationError("Claim confidence is invalid")
        if not isinstance(self.created_by, ClaimCreatedBy):
            raise ClaimCommandValidationError("Claim creator is invalid")
        actor = validate_actor(self.actor)
        reason = validate_reason(self.reason)
        if not isinstance(self.sources, tuple) or not all(
            isinstance(source, ClaimSourceInput) for source in self.sources
        ):
            raise ClaimCommandValidationError("Claim sources are invalid")
        source_keys = {
            (
                source.source_id,
                source.segment_id,
                source.quote_start,
                source.quote_end,
                source.relation,
            )
            for source in self.sources
        }
        if len(source_keys) != len(self.sources):
            raise ClaimCommandValidationError("Claim sources are invalid")
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "field", field)
        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "reason", reason)
        if self.confidence is not None:
            object.__setattr__(self, "confidence", float(self.confidence))


@dataclass(frozen=True, slots=True)
class AttachClaimSourceCommand:
    """Validated intent to append one source reference to a Claim."""

    claim_id: str
    expected_version: int
    source: ClaimSourceInput
    actor: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", validate_claim_id(self.claim_id))
        validate_version(self.expected_version)
        if not isinstance(self.source, ClaimSourceInput):
            raise ClaimCommandValidationError("Claim source is invalid")
        object.__setattr__(self, "actor", validate_actor(self.actor))
        object.__setattr__(self, "reason", validate_reason(self.reason))


@dataclass(frozen=True, slots=True)
class ConfirmClaimCommand:
    """Validated intent to confirm one current Claim version."""

    claim_id: str
    expected_version: int
    basis: ClaimConfirmationBasis
    actor: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", validate_claim_id(self.claim_id))
        validate_version(self.expected_version)
        if not isinstance(self.basis, ClaimConfirmationBasis):
            raise ClaimCommandValidationError("Confirmation basis is invalid")
        object.__setattr__(self, "actor", validate_actor(self.actor))
        object.__setattr__(self, "reason", validate_reason(self.reason))


@dataclass(frozen=True, slots=True)
class CorrectClaimCommand:
    """Validated intent to replace a Claim value without losing history."""

    claim_id: str
    expected_version: int
    value: ClaimValue
    actor: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", validate_claim_id(self.claim_id))
        validate_version(self.expected_version)
        if not isinstance(self.value, ClaimValue):
            raise ClaimCommandValidationError("Claim value is invalid")
        object.__setattr__(self, "actor", validate_actor(self.actor))
        object.__setattr__(self, "reason", validate_reason(self.reason))


@dataclass(frozen=True, slots=True)
class RejectClaimCommand:
    """Validated intent to reject one current Claim version."""

    claim_id: str
    expected_version: int
    actor: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", validate_claim_id(self.claim_id))
        validate_version(self.expected_version)
        object.__setattr__(self, "actor", validate_actor(self.actor))
        object.__setattr__(self, "reason", validate_reason(self.reason))


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    """Authoritative portable projection of one Claim."""

    id: str
    case_id: str
    field: str
    value: ClaimValue
    status: ClaimStatus
    confidence: float | None
    created_by: ClaimCreatedBy
    contradiction: bool
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ClaimSource:
    """Validated Claim citation metadata backed by an authoritative Segment."""

    claim_id: str
    case_id: str
    source_id: str
    segment_id: str
    sequence: int
    page: int | None
    segment_text_sha256: str
    quote_start: int
    quote_end: int
    quote_hash: str
    relation: ClaimSourceRelation
    attached_by: str
    attached_at: str


@dataclass(frozen=True, slots=True)
class ClaimEvent:
    """One immutable Claim provenance event."""

    id: str
    claim_id: str
    event_type: ClaimEventType
    prior_status: ClaimStatus | None
    new_status: ClaimStatus
    prior_value: ClaimValue | None
    new_value: ClaimValue
    prior_version: int
    new_version: int
    actor: str
    reason: str
    occurred_at: str


@dataclass(frozen=True, slots=True)
class ClaimConfirmation:
    """Immutable evidence or explicit-user confirmation record."""

    id: str
    claim_id: str
    claim_version: int
    basis: ClaimConfirmationBasis
    value_sha256: str
    actor: str
    reason: str
    confirmed_at: str
