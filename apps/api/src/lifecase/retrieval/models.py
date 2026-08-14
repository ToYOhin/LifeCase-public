"""Stable typed contracts for local lexical retrieval."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """Bounds for untrusted local retrieval requests."""

    min_query_chars: int = 3
    max_query_chars: int = 256
    default_limit: int = 20
    max_limit: int = 100

    def __post_init__(self) -> None:
        if self.min_query_chars <= 0:
            raise ValueError("min_query_chars must be positive")
        if self.max_query_chars < self.min_query_chars:
            raise ValueError("max_query_chars must be at least min_query_chars")
        if self.default_limit <= 0:
            raise ValueError("default_limit must be positive")
        if self.max_limit < self.default_limit:
            raise ValueError("max_limit must be at least default_limit")


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """One validated match backed by an authoritative SourceSegment row."""

    case_id: str
    source_id: str
    segment_id: str
    sequence: int
    page: int | None
    text_sha256: str
    text: str
    score: float


@dataclass(frozen=True, slots=True)
class RetrievalIndexStatus:
    """Deterministic counts produced by a successful index rebuild."""

    authoritative_segments: int
    indexed_segments: int
