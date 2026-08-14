"""M6-01 deterministic Evidence Pack authority, API, and integrity coverage."""

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifecase.api.evidence_packs as evidence_pack_api
from lifecase.actions import (
    ActionKind,
    ActionPayload,
    ActionRiskLevel,
    ActionService,
    ClaimRequirementInput,
    CreateActionCommand,
)
from lifecase.approvals import (
    ApprovalDecisionCommand,
    ApprovalService,
    ApprovalSummary,
    RequestApprovalCommand,
)
from lifecase.cases import CaseService
from lifecase.claims import (
    ClaimConfirmationBasis,
    ClaimCreatedBy,
    ClaimRecord,
    ClaimService,
    ClaimStatus,
    ClaimValue,
    ConfirmClaimCommand,
    CreateClaimCommand,
)
from lifecase.config import ApplicationConfig
from lifecase.deadlines import (
    CreateExplicitDeadlineCommand,
    DeadlineBasisInput,
    DeadlineDateKind,
    DeadlineService,
    DeadlineStatusCommand,
)
from lifecase.evidence import SourceImporter
from lifecase.evidence.pack import (
    EvidencePackIntegrityError,
    EvidencePackPersistenceError,
    EvidencePackService,
    verify_evidence_pack,
)
from lifecase.main import create_app
from lifecase.storage import (
    ContentAddressedObjectStore,
    ObjectStoreConfig,
    apply_migrations,
    connect_database,
)

_FIELDS = (
    "merchant_or_manufacturer",
    "product_name",
    "purchase_date",
    "order_number",
    "desired_outcome",
)


def test_pack_is_layered_deterministic_idempotent_and_restart_verifiable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config)
    imported = _import_source(connection, config, case_id)
    claims = tuple(
        _confirmed_claim(
            connection,
            case_id,
            field,
            "2026-06-18" if field == "purchase_date" else f"synthetic {field}",
        )
        for field in _FIELDS
    )
    purchase_date = next(item for item in claims if item.field == "purchase_date")
    deadline = DeadlineService(connection).create_explicit(
        CreateExplicitDeadlineCommand(
            case_id=case_id,
            basis=DeadlineBasisInput(
                purchase_date.id,
                purchase_date.version,
                purchase_date.value.sha256,
                DeadlineDateKind.LOCAL_DATE,
            ),
            timezone="Asia/Shanghai",
            actor="local_user",
            reason="Create synthetic evidence-backed deadline",
        )
    )
    DeadlineService(connection).confirm(
        DeadlineStatusCommand(
            deadline.id,
            deadline.version,
            "local_user",
            "Confirm synthetic evidence-backed deadline",
        )
    )
    action = ActionService(connection).create_action(
        CreateActionCommand(
            case_id=case_id,
            kind=ActionKind.PREPARE_COMMUNICATION,
            risk_level=ActionRiskLevel.B,
            payload=ActionPayload.from_input(
                ActionKind.PREPARE_COMMUNICATION,
                {"intent": "request_remedy", "recipient_role": "merchant"},
            ),
            requirements=tuple(
                ClaimRequirementInput(item.id, item.version, item.value.sha256, item.field)
                for item in claims
            ),
            dependencies=(),
            deadline=None,
            actor="local_user",
            reason="Create synthetic communication Action",
        )
    )
    approval = ApprovalService(connection).request(
        RequestApprovalCommand(
            action.id,
            action.version,
            action.payload.sha256,
            ApprovalSummary.from_input("Approve exact synthetic communication"),
            "local_user",
            "Request synthetic Approval",
        )
    )
    ApprovalService(connection).approve(
        ApprovalDecisionCommand(
            approval.id,
            action.id,
            approval.version,
            action.version,
            "local_user",
            "Approve synthetic communication",
        )
    )

    service = EvidencePackService(connection, config.data_root)
    first = service.create(case_id)
    second = service.create(case_id)
    assert first == second
    assert service.get_latest(case_id) == first
    assert (
        connection.execute("SELECT COUNT(*) FROM artifact WHERE type = 'pack'").fetchone()[0] == 1
    )
    download = service.get_download(first.id)
    verify_evidence_pack(download.path)

    with zipfile.ZipFile(download.path) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        case_value = json.loads(archive.read("confirmed/case.json"))
        assert names[0] == "manifest.json" and names[-1] == "checksums.sha256"
        assert {
            "manifest.json",
            "checksums.sha256",
            f"original/{imported.source_id}/content.bin",
            "derived/source_segments.json",
            "derived/claims.json",
            "generated/actions.json",
            "generated/deadlines.json",
            f"generated/communications/{action.id}.md",
            "confirmed/case.json",
            "confirmed/claims.json",
            "confirmed/deadlines.json",
            "confirmed/approvals.json",
            "timeline.md",
        }.issubset(names)
        assert manifest["format_version"] == 1
        assert manifest["layers"] == ["original", "derived", "generated", "confirmed"]
        assert manifest["excluded_recursive_artifact_types"] == ["backup", "manifest", "pack"]
        assert case_value["id"] == case_id
        assert archive.read(f"original/{imported.source_id}/content.bin") == b"synthetic receipt\n"
        serialized = b"".join(archive.read(name) for name in names)
        assert all(marker not in serialized for marker in (b"H:\\", b"C:\\Users", b"PRIVATE"))
    connection.close()

    restarted = connect_database(config.database_path)
    try:
        recovered = EvidencePackService(restarted, config.data_root).get_download(first.id)
        assert recovered.artifact == first
        assert recovered.path.read_bytes() == download.path.read_bytes()
    finally:
        restarted.close()


def test_pack_tampering_and_changed_source_fail_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config)
    imported = _import_source(connection, config, case_id)
    service = EvidencePackService(connection, config.data_root)
    artifact = service.create(case_id)
    original = service.get_download(artifact.id).path

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as output:
        for name in source.namelist():
            content = source.read(name)
            if name == "confirmed/case.json":
                content += b" "
            output.writestr(name, content)
    with pytest.raises(EvidencePackIntegrityError):
        verify_evidence_pack(tampered)

    malicious = tmp_path / "traversal.zip"
    with zipfile.ZipFile(malicious, "w") as output:
        output.writestr("../outside.txt", b"unsafe")
    with pytest.raises(EvidencePackIntegrityError):
        verify_evidence_pack(malicious)

    source_path = config.data_root.joinpath(*Path(imported.object_path).parts)
    source_path.write_bytes(b"changed receipt\n")
    with pytest.raises(EvidencePackIntegrityError):
        service.create(case_id)
    connection.close()


def test_pack_artifact_write_failure_rolls_back_file_and_metadata(tmp_path: Path) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config)
    connection.execute(
        """
        CREATE TRIGGER synthetic_pack_failure
        BEFORE INSERT ON artifact WHEN NEW.type = 'pack'
        BEGIN
            SELECT RAISE(ABORT, 'synthetic private SQL body');
        END
        """
    )
    with pytest.raises(EvidencePackPersistenceError, match="could not be created") as captured:
        EvidencePackService(connection, config.data_root).create(case_id)
    assert "private SQL" not in str(captured.value)
    assert (
        connection.execute("SELECT COUNT(*) FROM artifact WHERE type = 'pack'").fetchone()[0] == 0
    )
    assert list((config.data_root / "exports").rglob("*.zip")) == []
    connection.close()


def test_pack_api_creates_downloads_recovers_and_redacts_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config)
    connection.close()

    with TestClient(create_app(config)) as client:
        absent = client.get(f"/api/v1/cases/{case_id}/evidence-pack")
        created = client.post(f"/api/v1/cases/{case_id}/evidence-pack")
        missing_case = client.post("/api/v1/cases/case_missing/evidence-pack")
        missing_artifact = client.get("/api/v1/artifacts/art_missing")
        downloaded = client.get(created.json()["download_url"])
    with TestClient(create_app(config)) as restarted_client:
        recovered = restarted_client.get(f"/api/v1/cases/{case_id}/evidence-pack")
    assert (absent.status_code, absent.json()["error"]["code"]) == (
        404,
        "artifact_not_found",
    )
    assert created.status_code == 201
    assert recovered.status_code == 200
    assert recovered.json() == created.json()
    assert created.json()["format_version"] == 1
    assert created.json()["filename"].endswith(".zip")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/zip"
    assert downloaded.headers["x-lifecase-content-sha256"] == created.json()["sha256"]
    downloaded_path = tmp_path / "api-download.zip"
    downloaded_path.write_bytes(downloaded.content)
    verify_evidence_pack(downloaded_path)
    assert (missing_case.status_code, missing_case.json()["error"]["code"]) == (
        404,
        "case_not_found",
    )
    assert (missing_artifact.status_code, missing_artifact.json()["error"]["code"]) == (
        404,
        "artifact_not_found",
    )

    def fail_create(*_args: object, **_kwargs: object) -> None:
        raise EvidencePackPersistenceError(
            "SELECT secret FROM private_table H:\\private\\material.txt"
        )

    monkeypatch.setattr(evidence_pack_api.EvidencePackService, "create", fail_create)
    with TestClient(create_app(config)) as client:
        failed = client.post(f"/api/v1/cases/{case_id}/evidence-pack")
    assert (failed.status_code, failed.json()["error"]["code"]) == (
        503,
        "evidence_pack_unavailable",
    )
    assert failed.json()["error"]["retryable"] is True
    assert all(
        marker not in failed.text
        for marker in ("secret", "private_table", "private", "material.txt")
    )


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _database(config: ApplicationConfig) -> tuple[sqlite3.Connection, str]:
    connection = connect_database(config.database_path)
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic Evidence Pack Case",
        goal="Export a verifiable synthetic package",
        template_id="warranty",
    )
    return connection, case.id


def _import_source(
    connection: sqlite3.Connection,
    config: ApplicationConfig,
    case_id: str,
):
    input_root = config.upload_root
    input_root.mkdir(parents=True, exist_ok=True)
    selected = input_root / "synthetic-receipt.txt"
    selected.write_bytes(b"synthetic receipt\n")
    store = ContentAddressedObjectStore(
        ObjectStoreConfig(
            data_root=config.data_root,
            allowed_source_roots=(input_root,),
            max_file_size_bytes=1024,
            allowed_media_types=frozenset({"text/plain"}),
        )
    )
    return SourceImporter(connection, store).import_selected(
        case_id=case_id,
        selected_path=selected,
    )


def _confirmed_claim(
    connection: sqlite3.Connection,
    case_id: str,
    field: str,
    value: object,
) -> ClaimRecord:
    service = ClaimService(connection)
    claim = service.create_claim(
        CreateClaimCommand(
            case_id=case_id,
            field=field,
            value=ClaimValue.from_input(value),
            status=ClaimStatus.CANDIDATE,
            confidence=0.9,
            created_by=ClaimCreatedBy.USER,
            actor="local_user",
            reason="Create synthetic Evidence Pack Claim",
        )
    )
    return service.confirm_claim(
        ConfirmClaimCommand(
            claim.id,
            claim.version,
            ClaimConfirmationBasis.USER,
            "local_user",
            "Confirm synthetic Evidence Pack Claim",
        )
    )
