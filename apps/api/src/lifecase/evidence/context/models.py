"""Immutable contracts for deterministic, budgeted Evidence Context."""

import hashlib
import math
import re
from dataclasses import dataclass
from enum import StrEnum

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_MAX_SCOPE_CHARS = 200
_MIN_QUERY_CHARS = 3
_MAX_QUERY_CHARS = 256
_MAX_RETRIEVAL_LIMIT = 100
_MAX_ADJACENT_SEGMENTS = 5
_MAX_CONTEXT_TEXT_BYTES = 100_000
_MAX_ITEM_TEXT_BYTES = 32_000


class EvidenceContextModelError(ValueError):
    """Raised when an Evidence Context value violates a stable boundary."""


class EvidenceTrustLabel(StrEnum):
    """Source trust labels currently supported by authoritative storage."""

    UNTRUSTED = "untrusted"


class EvidenceContextOrigin(StrEnum):
    """Why one segment is present in the resulting Context."""

    MATCH = "match"
    ADJACENT = "adjacent"


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    """Explicit allowlist applied to authoritative Source trust labels."""

    allowed_trust_labels: frozenset[EvidenceTrustLabel]

    def __post_init__(self) -> None:
        if not isinstance(self.allowed_trust_labels, frozenset) or not all(
            isinstance(label, EvidenceTrustLabel) for label in self.allowed_trust_labels
        ):
            raise EvidenceContextModelError("Evidence policy is invalid")


@dataclass(frozen=True, slots=True)
class EvidenceContextBudget:
    """UTF-8 text-byte ceilings; segment text is never partially included."""

    max_text_bytes: int
    max_item_text_bytes: int

    def __post_init__(self) -> None:
        if (
            not _is_int(self.max_text_bytes)
            or not 1 <= self.max_text_bytes <= _MAX_CONTEXT_TEXT_BYTES
            or not _is_int(self.max_item_text_bytes)
            or not 1 <= self.max_item_text_bytes <= _MAX_ITEM_TEXT_BYTES
            or self.max_item_text_bytes > self.max_text_bytes
        ):
            raise EvidenceContextModelError("Evidence Context budget is invalid")


@dataclass(frozen=True, slots=True)
class EvidenceContextRequest:
    """Bounded intent to build one local, Case-scoped Evidence Context."""

    case_id: str
    query: str
    retrieval_limit: int
    adjacent_before: int
    adjacent_after: int
    budget: EvidenceContextBudget
    policy: EvidencePolicy

    def __post_init__(self) -> None:
        case_id = _bounded_text(self.case_id, maximum=_MAX_SCOPE_CHARS)
        query = _bounded_text(self.query, maximum=_MAX_QUERY_CHARS)
        if len(query) < _MIN_QUERY_CHARS:
            raise EvidenceContextModelError("Evidence Context request is invalid")
        if not _is_int(self.retrieval_limit) or not 1 <= self.retrieval_limit <= (
            _MAX_RETRIEVAL_LIMIT
        ):
            raise EvidenceContextModelError("Evidence Context request is invalid")
        for value in (self.adjacent_before, self.adjacent_after):
            if not _is_int(value) or not 0 <= value <= _MAX_ADJACENT_SEGMENTS:
                raise EvidenceContextModelError("Evidence Context request is invalid")
        if not isinstance(self.budget, EvidenceContextBudget) or not isinstance(
            self.policy, EvidencePolicy
        ):
            raise EvidenceContextModelError("Evidence Context request is invalid")
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "query", query)


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    """Portable identity of one authoritative SourceSegment."""

    case_id: str
    source_id: str
    segment_id: str
    sequence: int
    page: int | None
    text_sha256: str

    def __post_init__(self) -> None:
        case_id = _bounded_text(self.case_id, maximum=_MAX_SCOPE_CHARS)
        source_id = _bounded_text(self.source_id, maximum=_MAX_SCOPE_CHARS)
        segment_id = _bounded_text(self.segment_id, maximum=_MAX_SCOPE_CHARS)
        if not _is_int(self.sequence) or self.sequence < 0:
            raise EvidenceContextModelError("Evidence citation is invalid")
        if self.page is not None and (not _is_int(self.page) or self.page <= 0):
            raise EvidenceContextModelError("Evidence citation is invalid")
        if not isinstance(self.text_sha256, str) or (
            _SHA256_PATTERN.fullmatch(self.text_sha256) is None
        ):
            raise EvidenceContextModelError("Evidence citation is invalid")
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "segment_id", segment_id)


@dataclass(frozen=True, slots=True)
class EvidenceContextMatch:
    """One policy-allowed initial retrieval hit in original ranked order."""

    rank: int
    score: float
    citation: EvidenceCitation

    def __post_init__(self) -> None:
        if not _is_int(self.rank) or self.rank <= 0:
            raise EvidenceContextModelError("Evidence match is invalid")
        if (
            not isinstance(self.score, (int, float))
            or isinstance(self.score, bool)
            or not math.isfinite(self.score)
            or not isinstance(self.citation, EvidenceCitation)
        ):
            raise EvidenceContextModelError("Evidence match is invalid")
        object.__setattr__(self, "score", float(self.score))


@dataclass(frozen=True, slots=True)
class EvidenceContextItem:
    """One complete segment admitted to the Evidence Context budget."""

    citation: EvidenceCitation
    text: str
    origin: EvidenceContextOrigin
    match_ranks: tuple[int, ...]
    text_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.citation, EvidenceCitation) or not isinstance(self.text, str):
            raise EvidenceContextModelError("Evidence Context item is invalid")
        if not self.text or not isinstance(self.origin, EvidenceContextOrigin):
            raise EvidenceContextModelError("Evidence Context item is invalid")
        if (
            not isinstance(self.match_ranks, tuple)
            or not self.match_ranks
            or tuple(sorted(set(self.match_ranks))) != self.match_ranks
            or any(not _is_int(rank) or rank <= 0 for rank in self.match_ranks)
        ):
            raise EvidenceContextModelError("Evidence Context item is invalid")
        actual_bytes = len(self.text.encode("utf-8"))
        if (
            not _is_int(self.text_bytes)
            or self.text_bytes != actual_bytes
            or hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.citation.text_sha256
        ):
            raise EvidenceContextModelError("Evidence Context item is invalid")


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """Deterministic read-only Context; it contains evidence, never an answer."""

    case_id: str
    query: str
    matches: tuple[EvidenceContextMatch, ...]
    items: tuple[EvidenceContextItem, ...]
    budget: EvidenceContextBudget
    used_text_bytes: int
    truncated: bool

    def __post_init__(self) -> None:
        case_id = _bounded_text(self.case_id, maximum=_MAX_SCOPE_CHARS)
        query = _bounded_text(self.query, maximum=_MAX_QUERY_CHARS)
        if not isinstance(self.matches, tuple) or not all(
            isinstance(match, EvidenceContextMatch) for match in self.matches
        ):
            raise EvidenceContextModelError("Evidence Context result is invalid")
        if not isinstance(self.items, tuple) or not all(
            isinstance(item, EvidenceContextItem) for item in self.items
        ):
            raise EvidenceContextModelError("Evidence Context result is invalid")
        if not isinstance(self.budget, EvidenceContextBudget):
            raise EvidenceContextModelError("Evidence Context result is invalid")
        match_ranks = tuple(match.rank for match in self.matches)
        if (
            tuple(sorted(set(match_ranks))) != match_ranks
            or any(match.citation.case_id != case_id for match in self.matches)
            or any(item.citation.case_id != case_id for item in self.items)
            or any(not set(item.match_ranks) <= set(match_ranks) for item in self.items)
        ):
            raise EvidenceContextModelError("Evidence Context result is invalid")
        actual_bytes = sum(item.text_bytes for item in self.items)
        if (
            not _is_int(self.used_text_bytes)
            or self.used_text_bytes != actual_bytes
            or actual_bytes > self.budget.max_text_bytes
            or not isinstance(self.truncated, bool)
        ):
            raise EvidenceContextModelError("Evidence Context result is invalid")
        segment_ids = tuple(item.citation.segment_id for item in self.items)
        if len(segment_ids) != len(set(segment_ids)):
            raise EvidenceContextModelError("Evidence Context result is invalid")
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "query", query)


def _bounded_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise EvidenceContextModelError("Evidence Context value is invalid")
    clean = value.strip()
    if not 1 <= len(clean) <= maximum:
        raise EvidenceContextModelError("Evidence Context value is invalid")
    return clean


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
