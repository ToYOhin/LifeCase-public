"""Integration tests for the M1 Source upload and detail API."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifecase.api.sources as sources_api
from lifecase.config import ApplicationConfig
from lifecase.evidence import SourceMetadataError
from lifecase.evidence.extraction import ObjectIntegrityError
from lifecase.main import create_app


def _config(tmp_path: Path, *, max_upload_bytes: int = 1024 * 1024) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
        max_upload_bytes=max_upload_bytes,
    )


def _create_case(client: TestClient) -> str:
    response = client.post(
        "/api/v1/cases",
        json={
            "title": "Synthetic warranty",
            "goal": "Test evidence intake",
            "template_id": "warranty",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _upload(client: TestClient, case_id: str, name: str, content: bytes):
    return client.post(
        f"/api/v1/cases/{case_id}/sources",
        files={"file": (name, content, "application/octet-stream")},
    )


def test_txt_eml_and_pdf_uploads_persist_and_restore_from_api(tmp_path: Path) -> None:
    config = _config(tmp_path)
    text_bytes = "合成中文证据，不含个人资料。".encode()
    eml_bytes = (
        b"Subject: Synthetic request\r\n"
        b"From: sender@example.invalid\r\n"
        b"To: receiver@example.invalid\r\n"
        b"Date: Fri, 01 Aug 2026 08:00:00 +0000\r\n\r\n"
        b"Synthetic plain body.\r\n"
    )
    pdf_bytes = _build_synthetic_pdf("Synthetic PDF evidence")

    with TestClient(create_app(config)) as client:
        case_id = _create_case(client)
        responses = [
            _upload(client, case_id, "evidence.txt", text_bytes),
            _upload(client, case_id, "message.eml", eml_bytes),
            _upload(client, case_id, "document.pdf", pdf_bytes),
        ]

    assert [response.status_code for response in responses] == [201, 201, 201]
    for response in responses:
        body = response.json()
        assert body["case_id"] == case_id
        assert body["trust_label"] == "untrusted"
        assert body["extraction"]["status"] == "completed"
        assert len(body["sha256"]) == 64
        assert "object_path" not in body
        assert str(config.data_root) not in response.text
        assert "合成中文证据" not in response.text

    source_id = responses[0].json()["id"]
    with TestClient(create_app(config)) as restarted_client:
        source = restarted_client.get(f"/api/v1/sources/{source_id}")
        case = restarted_client.get(f"/api/v1/cases/{case_id}")

    assert source.status_code == 200
    assert source.json() == responses[0].json()
    assert [item["id"] for item in case.json()["sources"]] == [
        response.json()["id"] for response in responses
    ]
    assert list(config.upload_root.iterdir()) == []


def test_duplicate_uploads_create_two_sources_and_one_object(tmp_path: Path) -> None:
    config = _config(tmp_path)
    content = b"same synthetic upload"
    with TestClient(create_app(config)) as client:
        case_id = _create_case(client)
        first = _upload(client, case_id, "first.txt", content)
        second = _upload(client, case_id, "second.txt", content)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["sha256"] == second.json()["sha256"]
    assert [path for path in config.data_root.rglob("*") if path.is_file()] == [
        config.data_root
        / "objects"
        / first.json()["sha256"][:2]
        / first.json()["sha256"][2:4]
        / first.json()["sha256"]
    ]


def test_upload_errors_are_actionable_and_paths_cannot_select_server_files(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, max_upload_bytes=32)
    with TestClient(create_app(config)) as client:
        case_id = _create_case(client)
        unsupported = _upload(client, case_id, "program.exe", b"MZ synthetic")
        oversized = _upload(client, case_id, "large.txt", b"x" * 33)
        missing_file = client.post(f"/api/v1/cases/{case_id}/sources")
        missing_case = _upload(client, "case_missing", "evidence.txt", b"synthetic")
        traversal_name = _upload(client, case_id, "../../safe.txt", b"safe synthetic")
        invalid_text = _upload(client, case_id, "invalid.txt", b"private-marker\xff")
        missing_source = client.get("/api/v1/sources/src_missing")

    assert (unsupported.status_code, unsupported.json()["error"]["code"]) == (
        415,
        "media_type_unsupported",
    )
    assert (oversized.status_code, oversized.json()["error"]["code"]) == (
        413,
        "source_too_large",
    )
    assert missing_file.status_code == 422
    assert missing_file.json()["error"]["code"] == "validation_error"
    assert missing_case.status_code == 404
    assert missing_case.json()["error"]["code"] == "case_not_found"
    assert traversal_name.status_code == 201
    assert traversal_name.json()["original_name"] == "safe.txt"
    assert invalid_text.status_code == 201
    assert invalid_text.json()["extraction"] == {
        "status": "failed",
        "extractor_version": "text.v1",
        "segment_count": 0,
        "notices": [],
        "error_code": "TEXT_ENCODING_INVALID",
    }
    assert "private-marker" not in invalid_text.text
    assert missing_source.status_code == 404
    assert list(config.upload_root.iterdir()) == []
    assert not (tmp_path / "safe.txt").exists()


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (ObjectIntegrityError(), 409, "source_integrity_failed"),
        (SourceMetadataError("synthetic database failure"), 503, "source_storage_failed"),
    ],
)
def test_internal_source_failures_map_without_leaking_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    config = _config(tmp_path)

    def fail_import(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(sources_api.SourceService, "import_source", fail_import)
    with TestClient(create_app(config)) as client:
        case_id = _create_case(client)
        response = _upload(client, case_id, "evidence.txt", b"synthetic evidence")

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert "synthetic database failure" not in response.text
    assert str(config.data_root) not in response.text
    assert list(config.upload_root.iterdir()) == []


def _build_synthetic_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
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
