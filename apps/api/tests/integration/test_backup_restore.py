"""M6-02 consistent backup, strict validation, and clean restore coverage."""

import hashlib
import json
import sqlite3
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import lifecase.storage.backup.service as backup_module
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
from lifecase.evidence.pack import EvidencePackService
from lifecase.main import create_app
from lifecase.storage import (
    MIGRATIONS,
    ContentAddressedObjectStore,
    Migration,
    ObjectStoreConfig,
    apply_migrations,
    connect_database,
)
from lifecase.storage.backup import (
    BackupArtifact,
    BackupIntegrityError,
    BackupLimitError,
    BackupPersistenceError,
    BackupService,
    BackupValidationError,
    RestoreApplyError,
    RestoreTargetNotCleanError,
    apply_migrations_with_backup,
    verify_backup,
)
from lifecase.timeline import TimelineService

_CONFIRMATION = "RESTORE INTO NEW DIRECTORY"


def test_full_backup_validates_without_target_and_restores_all_authority(tmp_path: Path) -> None:
    config = _config(tmp_path)
    connection, case_id, pack_id = _full_warranty_case(config)
    service = BackupService(connection, config.database_path, config.data_root)

    artifact = service.create()
    repeated = service.create()
    download = service.get_download(artifact.id)
    independent = verify_backup(download.path)
    validation = service.validate(download.path)
    target = config.database_path.parent / "recovery" / "restored" / "warranty-copy"

    assert artifact.format_version == independent.format_version == 1
    assert repeated.id == artifact.id
    assert len(list((config.database_path.parent / "backups").rglob("*.zip"))) == 1
    assert artifact.case_count == validation.case_count == 1
    assert validation.validation_id == artifact.sha256
    assert not target.exists()
    with zipfile.ZipFile(download.path) as archive:
        assert "database/lifecase.sqlite3" in archive.namelist()
        assert not any(name.endswith(("-wal", "-shm")) for name in archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["scope"] == {"case_ids": [case_id], "kind": "all_cases"}
        assert all("H:\\" not in json.dumps(item) for item in manifest["members"])

    connection.close()
    restarted_source = connect_database(config.database_path)
    restored = BackupService(restarted_source, config.database_path, config.data_root).apply(
        validation.validation_id, "warranty-copy", _CONFIRMATION
    )
    assert restored.runtime_id == "warranty-copy"
    assert target.is_dir()
    restarted_source.close()

    restarted = connect_database(target / "lifecase.sqlite3")
    try:
        assert CaseService(restarted).get_case(case_id).id == case_id
        assert (
            restarted.execute(
                "SELECT COUNT(*) FROM source WHERE case_id = ?", (case_id,)
            ).fetchone()[0]
            == 1
        )
        assert len(ClaimService(restarted).list_case_claims(case_id)) == 5
        assert len(DeadlineService(restarted).list_case_deadlines(case_id)) == 1
        assert len(ActionService(restarted).list_case_actions(case_id)) == 1
        assert len(ApprovalService(restarted).list_case_approvals(case_id)) == 1
        assert TimelineService(restarted).list_case_timeline(case_id).events
        assert EvidencePackService(restarted, target / "data").get_download(pack_id).path.is_file()
    finally:
        restarted.close()


def test_online_backup_includes_committed_state_and_excludes_open_transaction(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    connection, first_case, _ = _full_warranty_case(config)
    writer = connect_database(config.database_path)
    committed = CaseService(writer).create_case(
        title="Committed concurrent case",
        goal="Must be visible in the snapshot",
        template_id="warranty",
    )
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        """
        INSERT INTO case_record (
            id, type, template_id, template_version, title, goal, status,
            priority, version, created_at, updated_at
        ) VALUES ('case_half_transaction', 'warranty', 'warranty_claim', 1,
                  'Half transaction', 'Must not be visible', 'intake', 0, 1, ?, ?)
        """,
        ("2026-08-03T00:00:00.000000Z", "2026-08-03T00:00:00.000000Z"),
    )

    artifact = BackupService(connection, config.database_path, config.data_root).create()
    writer.execute("ROLLBACK")
    writer.close()
    with zipfile.ZipFile(
        BackupService(connection, config.database_path, config.data_root)
        .get_download(artifact.id)
        .path
    ) as archive:
        database = tmp_path / "snapshot.sqlite3"
        database.write_bytes(archive.read("database/lifecase.sqlite3"))
    snapshot = sqlite3.connect(database)
    try:
        ids = {str(row[0]) for row in snapshot.execute("SELECT id FROM case_record")}
        assert {first_case, committed.id} <= ids
        assert "case_half_transaction" not in ids
    finally:
        snapshot.close()
        connection.close()


@pytest.mark.parametrize("mode", ["missing", "extra", "changed"])
def test_backup_creation_reconciles_the_complete_data_root(tmp_path: Path, mode: str) -> None:
    config = _config(tmp_path)
    connection, _, _ = _full_warranty_case(config)
    source_path = next((config.data_root / "objects").rglob("*"))
    while source_path.is_dir():
        source_path = next(source_path.iterdir())
    if mode == "missing":
        source_path.unlink()
    elif mode == "extra":
        extra = config.data_root / "objects" / "stale.bin"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_bytes(b"stale")
    else:
        source_path.write_bytes(b"changed")

    with pytest.raises(BackupIntegrityError):
        BackupService(connection, config.database_path, config.data_root).create()
    assert not list((config.database_path.parent / "backups").rglob("*.zip"))
    connection.close()


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../escape",
        "/absolute",
        "C:/drive",
        "//server/share",
        "folder\\member",
        "control\x01member",
    ],
)
def test_restore_rejects_unsafe_archive_paths(tmp_path: Path, unsafe_name: str) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
        output.writestr(unsafe_name, b"unsafe")
    with pytest.raises(BackupIntegrityError):
        verify_backup(archive)


def test_restore_rejects_duplicate_case_collision_symlink_and_single_byte_tamper(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    connection, _, _ = _full_warranty_case(config)
    original = BackupService(connection, config.database_path, config.data_root).create()
    original_path = (
        BackupService(connection, config.database_path, config.data_root)
        .get_download(original.id)
        .path
    )

    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate, "w") as output:
        output.writestr("manifest.json", b"{}")
        output.writestr("Manifest.json", b"{}")
        output.writestr("checksums.sha256", b"")
    with pytest.raises(BackupIntegrityError):
        verify_backup(duplicate)

    symbolic = tmp_path / "symbolic.zip"
    info = zipfile.ZipInfo("database/lifecase.sqlite3")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(symbolic, "w") as output:
        output.writestr(info, b"target")
        output.writestr("manifest.json", b"{}")
        output.writestr("checksums.sha256", b"")
    with pytest.raises(BackupIntegrityError):
        verify_backup(symbolic)

    tampered = tmp_path / "tampered.zip"
    content = bytearray(original_path.read_bytes())
    content[len(content) // 2] ^= 1
    tampered.write_bytes(content)
    with pytest.raises(BackupIntegrityError):
        verify_backup(tampered)
    connection.close()


def test_restore_rejects_unknown_format_migration_drift_and_nonclean_target(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    connection, _, _ = _full_warranty_case(config)
    service = BackupService(connection, config.database_path, config.data_root)
    artifact = service.create()
    source = service.get_download(artifact.id).path

    unknown_format = _rewrite_archive(
        source,
        tmp_path / "unknown-format.zip",
        lambda manifest, _database: manifest.update(format_version=999),
    )
    with pytest.raises(BackupIntegrityError):
        verify_backup(unknown_format)

    drifted = _rewrite_archive(
        source,
        tmp_path / "drifted.zip",
        _drift_migration_checksum,
    )
    with pytest.raises(BackupIntegrityError):
        verify_backup(drifted)

    validation = service.validate(source)
    target = config.database_path.parent / "recovery" / "restored" / "occupied"
    target.mkdir(parents=True)
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(RestoreTargetNotCleanError):
        service.apply(validation.validation_id, "occupied", _CONFIRMATION)
    assert marker.read_text(encoding="utf-8") == "keep"
    with pytest.raises(BackupValidationError):
        service.apply(validation.validation_id, "another", "yes")
    connection.close()


@pytest.mark.parametrize(
    "mutation",
    ["future-migration", "foreign-key", "sqlite-integrity"],
)
def test_restore_rejects_future_or_invalid_sqlite_authority(
    tmp_path: Path,
    mutation: str,
) -> None:
    config = _config(tmp_path)
    connection, _, _ = _full_warranty_case(config)
    service = BackupService(connection, config.database_path, config.data_root)
    source = service.get_download(service.create().id).path
    mutations = {
        "future-migration": _add_future_migration,
        "foreign-key": _add_foreign_key_violation,
        "sqlite-integrity": _corrupt_sqlite_header,
    }
    invalid = _rewrite_archive(source, tmp_path / "invalid-authority.zip", mutations[mutation])
    with pytest.raises(BackupIntegrityError):
        verify_backup(invalid)
    connection.close()


def test_restore_enforces_member_and_total_size_policy(tmp_path: Path) -> None:
    from lifecase.storage.backup import BackupPolicy

    config = _config(tmp_path)
    connection, _, _ = _full_warranty_case(config)
    service = BackupService(connection, config.database_path, config.data_root)
    source = service.get_download(service.create().id).path
    restrictive = BackupPolicy(
        max_members=4096,
        max_member_bytes=1024,
        max_total_bytes=2048,
        chunk_size_bytes=128,
    )
    with pytest.raises(BackupLimitError):
        verify_backup(source, restrictive)
    connection.close()


def test_backup_and_restore_injected_failures_leave_no_partial_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    connection, _, _ = _full_warranty_case(config)
    service = BackupService(connection, config.database_path, config.data_root)

    def fail_link(_source: Path, _destination: Path) -> None:
        raise OSError("synthetic private publication path")

    monkeypatch.setattr("lifecase.storage.backup.service.os.link", fail_link)
    with pytest.raises(BackupPersistenceError) as captured:
        service.create()
    assert "private" not in str(captured.value)
    assert not list((config.database_path.parent / "backups").rglob("*.zip"))
    monkeypatch.undo()

    artifact = service.create()
    validation = service.validate(service.get_download(artifact.id).path)

    def fail_services(_database: Path, _data: Path) -> bool:
        raise OSError("synthetic private restore path")

    monkeypatch.setattr(backup_module, "_verify_restored_services", fail_services)
    with pytest.raises(RestoreApplyError) as restore_error:
        service.apply(validation.validation_id, "failed-restore", _CONFIRMATION)
    assert "private" not in str(restore_error.value)
    restored_root = config.database_path.parent / "recovery" / "restored"
    assert not (restored_root / "failed-restore").exists()
    assert not list(restored_root.glob(".lifecase-restore-*"))
    connection.close()


def test_pre_migration_backup_order_and_failure_gate(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.data_root.mkdir(parents=True)
    connection = connect_database(config.database_path)
    apply_migrations(connection)
    calls: list[str] = []
    migration = Migration(
        version=MIGRATIONS[-1].version + 1,
        name="synthetic_pre_migration_order",
        statements=("CREATE TABLE synthetic_upgrade (id INTEGER PRIMARY KEY)",),
    )

    def unexpected_backup() -> BackupArtifact:
        calls.append("unexpected")
        return _fake_artifact()

    assert (
        apply_migrations_with_backup(
            connection,
            config.database_path,
            config.data_root,
            backup_factory=unexpected_backup,
        )
        is None
    )
    assert calls == []

    def fail_backup() -> object:
        calls.append("backup_failed")
        raise BackupPersistenceError("safe failure")

    with pytest.raises(BackupPersistenceError):
        apply_migrations_with_backup(
            connection,
            config.database_path,
            config.data_root,
            (*MIGRATIONS, migration),
            backup_factory=fail_backup,  # type: ignore[arg-type]
        )
    assert "synthetic_upgrade" not in _tables(connection)
    assert calls == ["backup_failed"]

    def successful_backup() -> BackupArtifact:
        calls.append("backup_ok")
        return BackupService(connection, config.database_path, config.data_root).create()

    apply_migrations_with_backup(
        connection,
        config.database_path,
        config.data_root,
        (*MIGRATIONS, migration),
        backup_factory=successful_backup,
    )
    assert calls == ["backup_failed", "backup_ok"]
    assert "synthetic_upgrade" in _tables(connection)
    assert list((config.database_path.parent / "backups").rglob("*.zip"))
    connection.close()


def test_backup_restore_api_downloads_validates_applies_and_redacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    connection, _, _ = _full_warranty_case(config)
    connection.close()
    with TestClient(create_app(config)) as client:
        missing = client.get("/api/v1/backups")
        created = client.post("/api/v1/backups")
        latest = client.get("/api/v1/backups")
        downloaded = client.get(created.json()["download_url"])
        validated = client.post(
            "/api/v1/restores/validate",
            files={"file": ("synthetic-backup.zip", downloaded.content, "application/zip")},
        )
        applied = client.post(
            "/api/v1/restores/apply",
            json={
                "validation_id": validated.json()["validation_id"],
                "target_name": "api-restore",
                "confirmation": _CONFIRMATION,
            },
        )
        occupied = client.post(
            "/api/v1/restores/apply",
            json={
                "validation_id": validated.json()["validation_id"],
                "target_name": "api-restore",
                "confirmation": _CONFIRMATION,
            },
        )
    assert created.status_code == 201
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "backup_not_found"
    assert latest.status_code == 200 and latest.json() == created.json()
    assert downloaded.status_code == 200
    assert validated.status_code == applied.status_code == 200
    assert occupied.status_code == 409
    assert occupied.json()["error"]["code"] == "restore_target_not_clean"

    def fail_create(*_args: object, **_kwargs: object) -> None:
        raise BackupPersistenceError("SELECT secret FROM H:\\private\\claim.txt")

    monkeypatch.setattr("lifecase.api.backups.BackupService.create", fail_create)
    with TestClient(create_app(config)) as client:
        failed = client.post("/api/v1/backups")
    assert failed.status_code == 503
    assert all(marker not in failed.text for marker in ("secret", "private", "claim.txt"))


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _full_warranty_case(config: ApplicationConfig) -> tuple[sqlite3.Connection, str, str]:
    connection = connect_database(config.database_path)
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic headphones warranty",
        goal="Prepare a verified replacement request",
        template_id="warranty",
    )
    config.upload_root.mkdir(parents=True, exist_ok=True)
    receipt = config.upload_root / "synthetic-receipt.txt"
    receipt.write_bytes(b"Synthetic headphones receipt 2026-06-18 order SYN-2048\n")
    store = ContentAddressedObjectStore(
        ObjectStoreConfig(
            data_root=config.data_root,
            allowed_source_roots=(config.upload_root,),
            max_file_size_bytes=4096,
            allowed_media_types=frozenset({"text/plain"}),
        )
    )
    SourceImporter(connection, store).import_selected(case_id=case.id, selected_path=receipt)

    values = {
        "merchant_or_manufacturer": "Synthetic Audio Shop",
        "product_name": "Synthetic Headphones",
        "purchase_date": "2026-06-18",
        "order_number": "SYN-2048",
        "desired_outcome": "Replacement",
    }
    claims = []
    claim_service = ClaimService(connection)
    for field, value in values.items():
        claim = claim_service.create_claim(
            CreateClaimCommand(
                case_id=case.id,
                field=field,
                value=ClaimValue.from_input(value),
                status=ClaimStatus.CANDIDATE,
                confidence=1.0,
                created_by=ClaimCreatedBy.USER,
                actor="local_user",
                reason="Create synthetic warranty authority",
            )
        )
        claims.append(
            claim_service.confirm_claim(
                ConfirmClaimCommand(
                    claim.id,
                    claim.version,
                    ClaimConfirmationBasis.USER,
                    "local_user",
                    "Confirm synthetic warranty authority",
                )
            )
        )
    purchase = next(item for item in claims if item.field == "purchase_date")
    deadline_service = DeadlineService(connection)
    deadline = deadline_service.create_explicit(
        CreateExplicitDeadlineCommand(
            case_id=case.id,
            basis=DeadlineBasisInput(
                purchase.id,
                purchase.version,
                purchase.value.sha256,
                DeadlineDateKind.LOCAL_DATE,
            ),
            timezone="Asia/Shanghai",
            actor="local_user",
            reason="Create synthetic follow-up date",
        )
    )
    deadline_service.confirm(
        DeadlineStatusCommand(
            deadline.id,
            deadline.version,
            "local_user",
            "Confirm synthetic follow-up date",
        )
    )
    action = ActionService(connection).create_action(
        CreateActionCommand(
            case_id=case.id,
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
            reason="Prepare synthetic warranty communication",
        )
    )
    approval_service = ApprovalService(connection)
    approval = approval_service.request(
        RequestApprovalCommand(
            action.id,
            action.version,
            action.payload.sha256,
            ApprovalSummary.from_input("Approve the exact synthetic request"),
            "local_user",
            "Request synthetic approval",
        )
    )
    approval_service.approve(
        ApprovalDecisionCommand(
            approval.id,
            action.id,
            approval.version,
            action.version,
            "local_user",
            "Approve synthetic communication",
        )
    )
    pack = EvidencePackService(connection, config.data_root).create(case.id)
    return connection, case.id, pack.id


def _rewrite_archive(
    source: Path,
    destination: Path,
    mutate: Callable[[dict[str, Any], Path], None],
) -> Path:
    with zipfile.ZipFile(source) as original:
        contents = {name: original.read(name) for name in original.namelist()}
    manifest = json.loads(contents["manifest.json"])
    database_path = destination.with_suffix(".sqlite3")
    database_path.write_bytes(contents["database/lifecase.sqlite3"])
    mutate(manifest, database_path)
    contents["database/lifecase.sqlite3"] = database_path.read_bytes()
    database_path.unlink()
    manifest["database"]["sha256"] = hashlib.sha256(
        contents["database/lifecase.sqlite3"]
    ).hexdigest()
    manifest["database"]["size_bytes"] = len(contents["database/lifecase.sqlite3"])
    contents["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, separators=(",", ": "))
        + "\n"
    ).encode()
    checksum_names = ["manifest.json", "database/lifecase.sqlite3"] + [
        item["archive_path"] for item in manifest["members"]
    ]
    contents["checksums.sha256"] = "".join(
        f"{hashlib.sha256(contents[name]).hexdigest()}  {name}\n" for name in checksum_names
    ).encode()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as output:
        for name in source_names(contents):
            output.writestr(name, contents[name])
    return destination


def source_names(contents: dict[str, bytes]) -> list[str]:
    return (
        ["manifest.json", "database/lifecase.sqlite3"]
        + sorted(name for name in contents if name.startswith("data/"))
        + ["checksums.sha256"]
    )


def _drift_migration_checksum(manifest: dict[str, Any], database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE schema_migration SET checksum = ? WHERE version = 1", ("f" * 64,)
        )
        connection.commit()
    finally:
        connection.close()
    catalog = manifest["migration_catalog"]
    assert isinstance(catalog, list)
    catalog[0]["checksum"] = "f" * 64


def _add_future_migration(_manifest: dict[str, Any], database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "INSERT INTO schema_migration VALUES (999, 'future', ?, ?)",
            ("f" * 64, "2099-01-01T00:00:00.000000Z"),
        )
        connection.commit()
    finally:
        connection.close()


def _add_foreign_key_violation(_manifest: dict[str, Any], database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO artifact (
                id, case_id, source_id, type, relative_path,
                sha256, size_bytes, created_by_run, created_at
            ) VALUES ('art_invalid_fk', 'case_missing', NULL, 'manifest',
                      'invalid/missing.bin', ?, 0, NULL, ?)
            """,
            ("0" * 64, "2026-08-03T00:00:00.000000Z"),
        )
        connection.commit()
    finally:
        connection.close()


def _corrupt_sqlite_header(_manifest: dict[str, Any], database_path: Path) -> None:
    content = bytearray(database_path.read_bytes())
    content[:16] = b"Not a SQLite db!"
    database_path.write_bytes(content)


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _fake_artifact() -> BackupArtifact:
    return BackupArtifact("0" * 64, "synthetic.zip", "0" * 64, 1, 1, "0.1.0", 0, 3, "now")
