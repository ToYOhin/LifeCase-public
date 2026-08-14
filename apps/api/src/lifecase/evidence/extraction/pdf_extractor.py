"""Thin pypdf adapter for bounded text-only PDF extraction."""

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from lifecase.evidence.extraction.errors import (
    OcrRequiredError,
    PdfEncryptedError,
    PdfLimitExceededError,
    PdfMalformedError,
)
from lifecase.evidence.extraction.models import ParsedDocument, RawTextPart

PDF_EXTRACTOR_VERSION = "pypdf.v1"


def extract_pdf(path: Path, *, max_pages: int, max_content_stream_bytes: int) -> ParsedDocument:
    """Extract page text while rejecting encrypted, malformed, and image-only PDFs."""

    try:
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise PdfMalformedError
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise PdfEncryptedError
        if len(reader.pages) > max_pages:
            raise PdfLimitExceededError

        parts: list[RawTextPart] = []
        for page_number, page in enumerate(reader.pages, start=1):
            contents = page.get_contents()
            if contents is not None and len(contents.get_data()) > max_content_stream_bytes:
                raise PdfLimitExceededError
            text = page.extract_text()
            if text and text.strip():
                parts.append(RawTextPart(text, page_number, "pdf", 0.95))
    except (OcrRequiredError, PdfEncryptedError, PdfLimitExceededError, PdfMalformedError):
        raise
    except (OSError, PdfReadError, KeyError, TypeError, ValueError) as error:
        raise PdfMalformedError from error

    if not parts:
        raise OcrRequiredError
    return ParsedDocument(extractor_version=PDF_EXTRACTOR_VERSION, parts=tuple(parts))
