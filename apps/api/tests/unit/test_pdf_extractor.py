"""Unit tests for the isolated pypdf adapter."""

from pathlib import Path

import pytest
from pypdf import PdfWriter

from lifecase.evidence.extraction.errors import (
    ExtractionTimeoutError,
    OcrRequiredError,
    PdfEncryptedError,
    PdfLimitExceededError,
    PdfMalformedError,
)
from lifecase.evidence.extraction.models import ExtractionConfig
from lifecase.evidence.extraction.pdf_extractor import extract_pdf
from lifecase.evidence.extraction.worker import parse_in_isolated_process


def build_synthetic_pdf(page_texts: list[str | None]) -> bytes:
    """Build a minimal text PDF without introducing a test-only PDF dependency."""

    page_ids = [4 + index * 2 for index in range(len(page_texts))]
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
            f"/Count {len(page_ids)} >>"
        ).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, text in enumerate(page_texts):
        content_id = page_ids[index] + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        escaped = (text or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode() if text else b""
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    document = bytearray(b"%PDF-1.4\n%synthetic\n")
    offsets = [0]
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{object_id} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(document)


def test_pdf_extracts_stable_page_text(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(build_synthetic_pdf(["Synthetic page one", "Synthetic page two"]))

    parsed = extract_pdf(source, max_pages=10, max_content_stream_bytes=1024)

    assert [(part.page, part.text.strip()) for part in parsed.parts] == [
        (1, "Synthetic page one"),
        (2, "Synthetic page two"),
    ]


def test_pdf_reports_ocr_required_for_pages_without_text(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(build_synthetic_pdf([None]))

    with pytest.raises(OcrRequiredError):
        extract_pdf(source, max_pages=10, max_content_stream_bytes=1024)


def test_pdf_rejects_malformed_signature_and_page_limit(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-broken")
    with pytest.raises(PdfMalformedError):
        extract_pdf(source, max_pages=10, max_content_stream_bytes=1024)

    source.write_bytes(build_synthetic_pdf(["one", "two"]))
    with pytest.raises(PdfLimitExceededError):
        extract_pdf(source, max_pages=1, max_content_stream_bytes=1024)


def test_pdf_rejects_encrypted_documents(tmp_path: Path) -> None:
    source = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("test-only")
    with source.open("wb") as output:
        writer.write(output)

    with pytest.raises(PdfEncryptedError):
        extract_pdf(source, max_pages=10, max_content_stream_bytes=1024)


def test_parser_worker_returns_results_and_enforces_hard_timeout(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("synthetic text", encoding="utf-8")

    parsed = parse_in_isolated_process(
        source,
        "text/plain",
        ExtractionConfig(data_root=tmp_path, parser_timeout_seconds=5.0),
    )
    assert parsed.parts[0].text == "synthetic text"

    with pytest.raises(ExtractionTimeoutError):
        parse_in_isolated_process(
            source,
            "text/plain",
            ExtractionConfig(data_root=tmp_path, parser_timeout_seconds=0.000001),
        )
