"""Typed contracts for extraction and stable source segments."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExtractionConfig:
    """Local bounds applied before and during document parsing."""

    data_root: Path
    max_segment_chars: int = 2000
    parser_timeout_seconds: float = 10.0
    max_pdf_pages: int = 100
    max_pdf_content_stream_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.max_segment_chars <= 0:
            raise ValueError("max_segment_chars must be positive")
        if self.parser_timeout_seconds <= 0:
            raise ValueError("parser_timeout_seconds must be positive")
        if self.max_pdf_pages <= 0:
            raise ValueError("max_pdf_pages must be positive")
        if self.max_pdf_content_stream_bytes <= 0:
            raise ValueError("max_pdf_content_stream_bytes must be positive")


@dataclass(frozen=True, slots=True)
class ExtractionNotice:
    """Non-fatal extraction condition without sensitive document content."""

    code: str
    count: int = 1


@dataclass(frozen=True, slots=True)
class RawTextPart:
    """One parser-produced text unit before deterministic segmentation."""

    text: str
    page: int | None
    extraction_method: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Serializable result produced by an isolated parser."""

    extractor_version: str
    parts: tuple[RawTextPart, ...]
    notices: tuple[ExtractionNotice, ...] = ()


@dataclass(frozen=True, slots=True)
class SegmentDraft:
    """Stable segment content before a source-specific ID is assigned."""

    sequence: int
    page: int | None
    bbox_json: str | None
    text: str
    extraction_method: str
    confidence: float
    text_sha256: str


@dataclass(frozen=True, slots=True)
class ExtractedSegment:
    """Persisted source segment returned to the caller."""

    id: str
    source_id: str
    sequence: int
    page: int | None
    bbox_json: str | None
    text: str
    extraction_method: str
    extractor_version: str
    confidence: float
    text_sha256: str


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Durable extraction result for one Source."""

    source_id: str
    status: str
    extractor_version: str
    segments: tuple[ExtractedSegment, ...]
    notices: tuple[ExtractionNotice, ...]
