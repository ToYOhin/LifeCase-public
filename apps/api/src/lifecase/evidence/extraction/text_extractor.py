"""Strict UTF-8 plain-text extraction."""

from pathlib import Path

from lifecase.evidence.extraction.errors import EmptyContentError, InvalidTextEncodingError
from lifecase.evidence.extraction.models import ParsedDocument, RawTextPart

TEXT_EXTRACTOR_VERSION = "text.v1"


def extract_plain_text(path: Path) -> ParsedDocument:
    """Read strict UTF-8 text, consuming an optional leading UTF-8 BOM."""

    try:
        text = path.read_bytes().decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        raise InvalidTextEncodingError from error
    except OSError as error:
        raise InvalidTextEncodingError from error

    if "\x00" in text:
        raise InvalidTextEncodingError
    if not text.strip():
        raise EmptyContentError

    return ParsedDocument(
        extractor_version=TEXT_EXTRACTOR_VERSION,
        parts=(RawTextPart(text, None, "direct", 1.0),),
    )
