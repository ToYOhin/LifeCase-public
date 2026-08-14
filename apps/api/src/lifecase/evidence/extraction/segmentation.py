"""Deterministic bounded segmentation for extracted text."""

import hashlib

from lifecase.evidence.extraction.models import RawTextPart, SegmentDraft


def segment_text_parts(
    parts: tuple[RawTextPart, ...], *, max_segment_chars: int
) -> tuple[SegmentDraft, ...]:
    """Split parser parts into stable ordered segments without interpreting content."""

    if max_segment_chars <= 0:
        raise ValueError("max_segment_chars must be positive")

    segments: list[SegmentDraft] = []
    for part in parts:
        normalized = part.text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        for chunk in _split_bounded(normalized, max_segment_chars):
            segments.append(
                SegmentDraft(
                    sequence=len(segments),
                    page=part.page,
                    bbox_json=None,
                    text=chunk,
                    extraction_method=part.extraction_method,
                    confidence=part.confidence,
                    text_sha256=hashlib.sha256(chunk.encode()).hexdigest(),
                )
            )
    return tuple(segments)


def _split_bounded(text: str, maximum: int) -> tuple[str, ...]:
    if not text.strip():
        return ()

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + maximum, len(text))
        if end < len(text):
            minimum_break = start + maximum // 2
            boundary = text.rfind("\n", minimum_break, end + 1)
            if boundary < 0:
                boundary = text.rfind(" ", minimum_break, end + 1)
            if boundary >= minimum_break:
                end = boundary + 1

        chunk = text[start:end].strip("\n")
        if chunk.strip():
            chunks.append(chunk)
        start = end
    return tuple(chunks)
