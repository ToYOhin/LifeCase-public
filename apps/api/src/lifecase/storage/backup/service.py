"""Consistent SQLite backup, portable verification, and clean-target restore."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from lifecase import __version__
from lifecase.actions import ActionService
from lifecase.approvals import ApprovalService
from lifecase.cases import CaseService
from lifecase.claims import ClaimService
from lifecase.deadlines import DeadlineService
from lifecase.evidence.pack import EvidencePackService
from lifecase.storage.database import connect_database
from lifecase.storage.migrations import MIGRATIONS, apply_migrations
from lifecase.timeline import TimelineService

from .errors import (
    BackupIntegrityError,
    BackupLimitError,
    BackupNotFoundError,
    BackupPersistenceError,
    BackupRestoreError,
    BackupValidationError,
    RestoreApplyError,
    RestoreTargetNotCleanError,
)
from .models import (
    BACKUP_FORMAT_VERSION,
    BackupArtifact,
    BackupDownload,
    BackupPolicy,
    RestoreResult,
    RestoreValidation,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TARGET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_DATABASE_MEMBER = "database/lifecase.sqlite3"
_MANIFEST_MEMBER = "manifest.json"
_CHECKSUM_MEMBER = "checksums.sha256"
_DEFAULT_POLICY = BackupPolicy()


@dataclass(frozen=True, slots=True)
class _Authority:
    authority_type: str
    authority_id: str
    case_id: str


@dataclass(frozen=True, slots=True)
class _DataMember:
    storage_path: str
    archive_path: str
    sha256: str
    size_bytes: int
    authorities: tuple[_Authority, ...]
    source_path: Path | None = None

    def manifest_value(self) -> dict[str, object]:
        return {
            "archive_path": self.archive_path,
            "authorities": [
                {
                    "case_id": item.case_id,
                    "id": item.authority_id,
                    "type": item.authority_type,
                }
                for item in self.authorities
            ],
            "case_ids": sorted({item.case_id for item in self.authorities}),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "storage_path": self.storage_path,
            "type": "retained_authority_file",
        }


@dataclass(frozen=True, slots=True)
class _ValidatedArchive:
    validation: RestoreValidation
    manifest: dict[str, Any]
    extracted_root: Path


class BackupService:
    """Own whole-authority backup retention and isolated restore publication."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        database_path: Path,
        data_root: Path,
        *,
        backup_root: Path | None = None,
        recovery_root: Path | None = None,
        policy: BackupPolicy = _DEFAULT_POLICY,
    ) -> None:
        self._connection = connection
        self._database_path = database_path.expanduser().resolve()
        self._data_root = data_root.expanduser().resolve()
        runtime_parent = self._database_path.parent
        self._backup_root = (backup_root or runtime_parent / "backups").expanduser().resolve()
        self._recovery_root = (recovery_root or runtime_parent / "recovery").expanduser().resolve()
        self._policy = policy

    def create(self) -> BackupArtifact:
        """Create and no-overwrite publish one verified online SQLite backup."""

        if self._connection.in_transaction:
            raise BackupValidationError("Backup requires an idle database connection")
        try:
            root = _safe_root(self._backup_root)
            with tempfile.TemporaryDirectory(prefix=".lifecase-backup-", dir=root) as name:
                temporary_root = Path(name)
                snapshot = temporary_root / "lifecase.sqlite3"
                destination_connection = sqlite3.connect(snapshot, isolation_level=None)
                try:
                    self._connection.backup(destination_connection)
                finally:
                    destination_connection.close()

                snapshot_connection = _open_snapshot(snapshot)
                try:
                    _verify_database(snapshot_connection)
                    members, case_ids = _collect_data_members(
                        snapshot_connection, self._data_root, self._policy
                    )
                    migration_catalog = _migration_catalog(snapshot_connection)
                finally:
                    snapshot_connection.close()

                archive_path = temporary_root / "backup.zip"
                manifest = _manifest(snapshot, members, case_ids, migration_catalog, self._policy)
                _write_archive(archive_path, snapshot, members, manifest, self._policy)
                with tempfile.TemporaryDirectory(
                    prefix=".lifecase-verify-", dir=root
                ) as verify_name:
                    validated = _validate_archive(archive_path, Path(verify_name), self._policy)
                backup_sha256, backup_size = _hash_file(
                    archive_path, self._policy.chunk_size_bytes, self._policy.max_total_bytes
                )
                if validated.validation.backup_sha256 != backup_sha256:
                    raise BackupIntegrityError("Backup publication checksum is invalid")
                destination = root / backup_sha256[:2] / f"{backup_sha256}.zip"
                _publish_file(archive_path, destination, backup_sha256, backup_size, self._policy)
                return _artifact(destination, validated.manifest, backup_sha256, backup_size)
        except BackupRestoreError:
            raise
        except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
            raise BackupPersistenceError("Backup could not be created") from error

    def get_download(self, backup_id: str) -> BackupDownload:
        clean_id = _command_sha256(backup_id, "Backup ID is invalid")
        path = self._backup_root / clean_id[:2] / f"{clean_id}.zip"
        if not path.is_file() or path.is_symlink():
            raise BackupNotFoundError
        try:
            with tempfile.TemporaryDirectory(
                prefix=".lifecase-download-verify-", dir=_safe_root(self._backup_root)
            ) as name:
                validated = _validate_archive(path, Path(name), self._policy)
            sha256, size_bytes = _hash_file(
                path, self._policy.chunk_size_bytes, self._policy.max_total_bytes
            )
            if sha256 != clean_id:
                raise BackupIntegrityError("Retained backup checksum is invalid")
            return BackupDownload(
                _artifact(path, validated.manifest, sha256, size_bytes), path.resolve(strict=True)
            )
        except BackupRestoreError:
            raise
        except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
            raise BackupPersistenceError("Backup could not be read") from error

    def get_latest(self) -> BackupArtifact:
        """Return the newest verified retained backup without trusting a sidecar index."""

        try:
            if not self._backup_root.exists():
                raise BackupNotFoundError
            candidates = [
                path
                for path in self._backup_root.glob("*/*.zip")
                if path.is_file() and not path.is_symlink()
            ]
            if not candidates:
                raise BackupNotFoundError
            newest = max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))
            return self.get_download(newest.stem).artifact
        except BackupRestoreError:
            raise
        except OSError as error:
            raise BackupPersistenceError("Backup could not be read") from error

    def validate(self, archive_path: Path, *, retain: bool = True) -> RestoreValidation:
        """Validate an archive without writing any restore target."""

        try:
            recovery_root = _safe_root(self._recovery_root)
            with tempfile.TemporaryDirectory(
                prefix=".lifecase-restore-validation-", dir=recovery_root
            ) as name:
                validated = _validate_archive(archive_path, Path(name), self._policy)
                if retain:
                    validations_root = _contained_directory(
                        recovery_root, PurePosixPath("validations")
                    )
                    validation_root = _contained_directory(
                        validations_root,
                        PurePosixPath(validated.validation.validation_id),
                    )
                    retained = validation_root / "backup.zip"
                    sha256, size_bytes = _hash_file(
                        archive_path,
                        self._policy.chunk_size_bytes,
                        self._policy.max_total_bytes,
                    )
                    _publish_file(archive_path, retained, sha256, size_bytes, self._policy)
                return validated.validation
        except BackupRestoreError:
            raise
        except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
            raise BackupPersistenceError("Backup validation is unavailable") from error

    def apply(self, validation_id: str, target_name: str, confirmation: str) -> RestoreResult:
        """Revalidate a retained backup and atomically publish a new runtime directory."""

        clean_id = _command_sha256(validation_id, "Restore validation ID is invalid")
        clean_target = _target_name(target_name)
        if confirmation != "RESTORE INTO NEW DIRECTORY":
            raise BackupValidationError("Restore confirmation is invalid")
        recovery_root = _safe_root(self._recovery_root)
        validations_root = _contained_directory(recovery_root, PurePosixPath("validations"))
        if not os.path.lexists(validations_root / clean_id):
            raise BackupNotFoundError
        validation_root = _contained_directory(
            validations_root, PurePosixPath(clean_id), create=False
        )
        retained = validation_root / "backup.zip"
        if not os.path.lexists(retained):
            raise BackupNotFoundError
        if retained.is_symlink() or not retained.is_file():
            raise BackupIntegrityError("Restore validation storage is invalid")
        restored_root = _contained_directory(recovery_root, PurePosixPath("restored"))
        target = restored_root / clean_target
        if os.path.lexists(target):
            raise RestoreTargetNotCleanError("Restore target already exists")

        staging: Path | None = None
        try:
            with tempfile.TemporaryDirectory(
                prefix=".lifecase-apply-validation-", dir=recovery_root
            ) as validation_name:
                validated = _validate_archive(retained, Path(validation_name), self._policy)
                if validated.validation.validation_id != clean_id:
                    raise BackupIntegrityError("Restore validation ticket is stale")
                staging = Path(tempfile.mkdtemp(prefix=".lifecase-restore-", dir=restored_root))
                database_destination = staging / "lifecase.sqlite3"
                data_destination = staging / "data"
                data_destination.mkdir()
                shutil.copyfile(
                    validated.extracted_root / _DATABASE_MEMBER,
                    database_destination,
                )
                for member in validated.manifest["members"]:
                    relative = _portable_path(member["storage_path"])
                    destination = data_destination.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(validated.extracted_root / member["archive_path"], destination)

                migrated = _verify_restored_services(database_destination, data_destination)
                os.rename(staging, target)
                staging = None
                return RestoreResult(
                    validation_id=clean_id,
                    runtime_id=clean_target,
                    case_count=validated.validation.case_count,
                    migrated=migrated,
                )
        except RestoreTargetNotCleanError:
            raise
        except BackupRestoreError:
            raise
        except (OSError, sqlite3.Error, ValueError) as error:
            raise RestoreApplyError("Verified backup could not be restored") from error
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)


def verify_backup(path: Path, policy: BackupPolicy = _DEFAULT_POLICY) -> RestoreValidation:
    """Independently verify a portable backup without retaining it."""

    try:
        parent = path.expanduser().resolve(strict=True).parent
        with tempfile.TemporaryDirectory(prefix=".lifecase-backup-verify-", dir=parent) as name:
            return _validate_archive(path, Path(name), policy).validation
    except BackupRestoreError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        raise BackupIntegrityError("Backup archive is invalid") from error


def _manifest(
    snapshot: Path,
    members: tuple[_DataMember, ...],
    case_ids: tuple[str, ...],
    migration_catalog: tuple[dict[str, object], ...],
    policy: BackupPolicy,
) -> dict[str, object]:
    database_sha256, database_size = _hash_file(
        snapshot, policy.chunk_size_bytes, policy.max_member_bytes
    )
    return {
        "archive_format": "zip-stored",
        "checksum_algorithm": "sha256",
        "database": {
            "path": _DATABASE_MEMBER,
            "sha256": database_sha256,
            "size_bytes": database_size,
            "type": "sqlite_online_backup",
        },
        "format_version": BACKUP_FORMAT_VERSION,
        "members": [item.manifest_value() for item in members],
        "migration_catalog": list(migration_catalog),
        "minimum_application_version": __version__,
        "scope": {"case_ids": list(case_ids), "kind": "all_cases"},
    }


def _collect_data_members(
    connection: sqlite3.Connection, data_root: Path, policy: BackupPolicy
) -> tuple[tuple[_DataMember, ...], tuple[str, ...]]:
    rows = connection.execute(
        """
        SELECT 'source' AS authority_type, id AS authority_id, case_id,
               object_path AS storage_path, sha256, size_bytes
        FROM source
        UNION ALL
        SELECT 'artifact', id, case_id, relative_path, sha256, size_bytes
        FROM artifact
        ORDER BY storage_path, authority_type, authority_id
        """
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        storage_path = _portable_path(str(row["storage_path"])).as_posix()
        grouped.setdefault(storage_path, []).append(row)

    members: list[_DataMember] = []
    for storage_path, references in grouped.items():
        first = references[0]
        sha256 = _sha256(str(first["sha256"]), "Retained authority checksum is invalid")
        size_bytes = _bounded_size(first["size_bytes"], policy)
        for row in references[1:]:
            if str(row["sha256"]) != sha256 or row["size_bytes"] != size_bytes:
                raise BackupIntegrityError("Retained authority references disagree")
        source_path = _verified_data_file(data_root, storage_path, sha256, size_bytes, policy)
        authorities = tuple(
            _Authority(str(row["authority_type"]), str(row["authority_id"]), str(row["case_id"]))
            for row in references
        )
        members.append(
            _DataMember(
                storage_path=storage_path,
                archive_path=f"data/{storage_path}",
                sha256=sha256,
                size_bytes=size_bytes,
                authorities=authorities,
                source_path=source_path,
            )
        )
    _reconcile_data_root(data_root, frozenset(grouped))
    case_ids = tuple(
        str(row[0]) for row in connection.execute("SELECT id FROM case_record ORDER BY id")
    )
    return tuple(members), case_ids


def _write_archive(
    path: Path,
    snapshot: Path,
    members: tuple[_DataMember, ...],
    manifest: dict[str, object],
    policy: BackupPolicy,
) -> None:
    if len(members) + 3 > policy.max_members:
        raise BackupLimitError("Backup member limit was exceeded")
    manifest_bytes = _json_bytes(manifest)
    database_sha256 = str(manifest["database"]["sha256"])  # type: ignore[index]
    checksums = [
        (hashlib.sha256(manifest_bytes).hexdigest(), _MANIFEST_MEMBER),
        (database_sha256, _DATABASE_MEMBER),
        *((item.sha256, item.archive_path) for item in members),
    ]
    checksum_bytes = "".join(f"{digest}  {name}\n" for digest, name in checksums).encode()
    total = (
        len(manifest_bytes)
        + len(checksum_bytes)
        + int(
            manifest["database"]["size_bytes"]  # type: ignore[index]
        )
        + sum(item.size_bytes for item in members)
    )
    if total > policy.max_total_bytes:
        raise BackupLimitError("Backup total limit was exceeded")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        _write_bytes(archive, _MANIFEST_MEMBER, manifest_bytes)
        _write_file(archive, _DATABASE_MEMBER, snapshot, database_sha256, policy)
        for member in members:
            if member.source_path is None:
                raise BackupIntegrityError("Backup member source is missing")
            _write_file(archive, member.archive_path, member.source_path, member.sha256, policy)
        _write_bytes(archive, _CHECKSUM_MEMBER, checksum_bytes)


def _validate_archive(path: Path, extraction_root: Path, policy: BackupPolicy) -> _ValidatedArchive:
    try:
        return _validate_archive_content(path, extraction_root, policy)
    except BackupRestoreError:
        raise
    except (
        OSError,
        sqlite3.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as error:
        raise BackupIntegrityError("Backup archive is invalid") from error


def _validate_archive_content(
    path: Path, extraction_root: Path, policy: BackupPolicy
) -> _ValidatedArchive:
    archive_sha256, _ = _hash_file(path, policy.chunk_size_bytes, policy.max_total_bytes)
    extraction_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "r", allowZip64=False) as archive:
        infos = archive.infolist()
        if len(infos) > policy.max_members:
            raise BackupLimitError("Backup member limit was exceeded")
        if len(infos) < 3:
            raise BackupIntegrityError("Backup control members are missing")
        names: set[str] = set()
        folded: set[str] = set()
        total = 0
        for info in infos:
            name = _portable_path(info.filename).as_posix()
            if name in names or name.casefold() in folded:
                raise BackupIntegrityError("Backup member path is duplicated")
            names.add(name)
            folded.add(name.casefold())
            mode = info.external_attr >> 16
            if (
                info.is_dir()
                or stat.S_ISLNK(mode)
                or info.flag_bits & 0x1
                or info.compress_type != zipfile.ZIP_STORED
                or info.file_size != info.compress_size
            ):
                raise BackupIntegrityError("Backup member type is invalid")
            if info.file_size > policy.max_member_bytes:
                raise BackupLimitError("Backup member limit was exceeded")
            total += info.file_size
            if total > policy.max_total_bytes:
                raise BackupLimitError("Backup total limit was exceeded")
        if {_MANIFEST_MEMBER, _CHECKSUM_MEMBER, _DATABASE_MEMBER} - names:
            raise BackupIntegrityError("Backup control members are missing")

        checksums = _parse_checksums(archive.read(_CHECKSUM_MEMBER).decode("utf-8"))
        if set(checksums) != names - {_CHECKSUM_MEMBER}:
            raise BackupIntegrityError("Backup checksum membership is invalid")
        for info in infos:
            if info.filename == _CHECKSUM_MEMBER:
                continue
            destination = extraction_root.joinpath(*_portable_path(info.filename).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size_bytes = 0
            with archive.open(info) as source, destination.open("xb") as output:
                while chunk := source.read(policy.chunk_size_bytes):
                    size_bytes += len(chunk)
                    if size_bytes > policy.max_member_bytes:
                        raise BackupLimitError("Backup member limit was exceeded")
                    digest.update(chunk)
                    output.write(chunk)
            if size_bytes != info.file_size or digest.hexdigest() != checksums[info.filename]:
                raise BackupIntegrityError("Backup member checksum is invalid")

    manifest = _read_manifest(extraction_root / _MANIFEST_MEMBER)
    _validate_manifest(manifest, checksums, policy)
    database = extraction_root / _DATABASE_MEMBER
    if database.stat().st_size != manifest["database"]["size_bytes"]:
        raise BackupIntegrityError("Backup database size is invalid")
    connection = _open_snapshot(database)
    try:
        _verify_database(connection)
        members, case_ids = _collect_data_members(connection, extraction_root / "data", policy)
        if tuple(manifest["scope"]["case_ids"]) != case_ids:
            raise BackupIntegrityError("Backup Case scope is invalid")
        if [item.manifest_value() for item in members] != manifest["members"]:
            raise BackupIntegrityError("Backup object manifest is inconsistent")
        if list(_migration_catalog(connection)) != manifest["migration_catalog"]:
            raise BackupIntegrityError("Backup migration manifest is inconsistent")
    finally:
        connection.close()

    database_value = manifest["database"]
    return _ValidatedArchive(
        validation=RestoreValidation(
            validation_id=archive_sha256,
            backup_sha256=archive_sha256,
            format_version=int(manifest["format_version"]),
            minimum_application_version=str(manifest["minimum_application_version"]),
            case_count=len(manifest["scope"]["case_ids"]),
            member_count=len(manifest["members"]) + 3,
            database_sha256=str(database_value["sha256"]),
        ),
        manifest=manifest,
        extracted_root=extraction_root,
    )


def _validate_manifest(
    manifest: dict[str, Any], checksums: dict[str, str], policy: BackupPolicy
) -> None:
    if set(manifest) != {
        "archive_format",
        "checksum_algorithm",
        "database",
        "format_version",
        "members",
        "migration_catalog",
        "minimum_application_version",
        "scope",
    }:
        raise BackupIntegrityError("Backup manifest is invalid")
    if (
        manifest["archive_format"] != "zip-stored"
        or manifest["checksum_algorithm"] != "sha256"
        or manifest["format_version"] != BACKUP_FORMAT_VERSION
        or not _compatible_version(manifest["minimum_application_version"])
        or manifest["scope"].get("kind") != "all_cases"
        or not isinstance(manifest["scope"].get("case_ids"), list)
        or not isinstance(manifest["members"], list)
        or not isinstance(manifest["migration_catalog"], list)
    ):
        raise BackupIntegrityError("Backup manifest is invalid")
    database = manifest["database"]
    if set(database) != {"path", "sha256", "size_bytes", "type"} or (
        database["path"] != _DATABASE_MEMBER
        or database["type"] != "sqlite_online_backup"
        or _sha256(database["sha256"], "Backup database checksum is invalid")
        != checksums[_DATABASE_MEMBER]
        or _bounded_size(database["size_bytes"], policy) < 1
    ):
        raise BackupIntegrityError("Backup database manifest is invalid")
    expected_paths = {_MANIFEST_MEMBER, _DATABASE_MEMBER}
    for member in manifest["members"]:
        if not isinstance(member, dict) or set(member) != {
            "archive_path",
            "authorities",
            "case_ids",
            "sha256",
            "size_bytes",
            "storage_path",
            "type",
        }:
            raise BackupIntegrityError("Backup object manifest is invalid")
        storage_path = _portable_path(member["storage_path"]).as_posix()
        archive_path = _portable_path(member["archive_path"]).as_posix()
        if archive_path != f"data/{storage_path}" or member["type"] != "retained_authority_file":
            raise BackupIntegrityError("Backup object manifest is invalid")
        if archive_path in expected_paths or checksums.get(archive_path) != member["sha256"]:
            raise BackupIntegrityError("Backup object manifest is invalid")
        _sha256(member["sha256"], "Backup object checksum is invalid")
        _bounded_size(member["size_bytes"], policy)
        if not isinstance(member["case_ids"], list) or not isinstance(member["authorities"], list):
            raise BackupIntegrityError("Backup object manifest is invalid")
        expected_paths.add(archive_path)
    if expected_paths != set(checksums):
        raise BackupIntegrityError("Backup manifest membership is invalid")


def _verify_database(connection: sqlite3.Connection) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchall()
    if [str(row[0]) for row in integrity] != ["ok"]:
        raise BackupIntegrityError("Backup database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise BackupIntegrityError("Backup database foreign keys are invalid")
    _migration_catalog(connection)


def _migration_catalog(connection: sqlite3.Connection) -> tuple[dict[str, object], ...]:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migration'"
    ).fetchone()
    if table is None:
        raise BackupIntegrityError("Backup migration catalog is missing")
    rows = connection.execute(
        "SELECT version, name, checksum FROM schema_migration ORDER BY version"
    ).fetchall()
    catalog = {item.version: item for item in MIGRATIONS}
    versions = [int(row[0]) for row in rows]
    if versions != [item.version for item in MIGRATIONS[: len(rows)]]:
        raise BackupIntegrityError("Backup migration catalog is not a supported prefix")
    result: list[dict[str, object]] = []
    for row in rows:
        version = int(row[0])
        migration = catalog.get(version)
        if migration is None or str(row[1]) != migration.name or str(row[2]) != migration.checksum:
            raise BackupIntegrityError("Backup migration catalog differs from this build")
        result.append({"checksum": migration.checksum, "name": migration.name, "version": version})
    return tuple(result)


def _verify_restored_services(database_path: Path, data_root: Path) -> bool:
    connection = connect_database(database_path)
    try:
        before = len(_migration_catalog(connection))
        apply_migrations(connection)
        after = len(_migration_catalog(connection))
        cases = CaseService(connection).list_workbench_cases()
        for item in cases:
            case_id = item.case.id
            CaseService(connection).get_case(case_id)
            ClaimService(connection).list_case_claims(case_id)
            DeadlineService(connection).list_case_deadlines(case_id)
            ActionService(connection).list_case_actions(case_id)
            ApprovalService(connection).list_case_approvals(case_id)
            TimelineService(connection).list_case_timeline(case_id)
        pack_rows = connection.execute(
            "SELECT id FROM artifact WHERE type = 'pack' ORDER BY id"
        ).fetchall()
        pack_service = EvidencePackService(connection, data_root)
        for row in pack_rows:
            pack_service.get_download(str(row[0]))
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupIntegrityError("Restored database foreign keys are invalid")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return after != before
    finally:
        connection.close()


def _reconcile_data_root(data_root: Path, expected: frozenset[str]) -> None:
    if not data_root.exists():
        if expected:
            raise BackupIntegrityError("Retained authority storage is missing")
        return
    if data_root.is_symlink() or not data_root.is_dir():
        raise BackupIntegrityError("Retained authority storage is invalid")
    actual: set[str] = set()
    for root, directories, files in os.walk(data_root, followlinks=False):
        root_path = Path(root)
        for directory in directories:
            if (root_path / directory).is_symlink():
                raise BackupIntegrityError("Retained authority storage contains a symbolic link")
        for filename in files:
            candidate = root_path / filename
            if candidate.is_symlink() or not candidate.is_file():
                raise BackupIntegrityError("Retained authority storage contains an invalid file")
            actual.add(candidate.relative_to(data_root).as_posix())
    if actual != set(expected):
        raise BackupIntegrityError("Retained authority storage membership is inconsistent")


def _verified_data_file(
    root: Path,
    relative_path: str,
    expected_sha256: str,
    expected_size: int,
    policy: BackupPolicy,
) -> Path:
    relative = _portable_path(relative_path)
    candidate = root.joinpath(*relative.parts)
    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        file_stat = resolved.stat()
    except OSError as error:
        raise BackupIntegrityError("Retained authority file is unavailable") from error
    if (
        candidate.is_symlink()
        or not resolved.is_relative_to(resolved_root)
        or not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_size != expected_size
    ):
        raise BackupIntegrityError("Retained authority file is invalid")
    sha256, size_bytes = _hash_file(resolved, policy.chunk_size_bytes, policy.max_member_bytes)
    if sha256 != expected_sha256 or size_bytes != expected_size:
        raise BackupIntegrityError("Retained authority file checksum is invalid")
    return resolved


def _open_snapshot(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _safe_root(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise BackupIntegrityError("Backup storage root is invalid")
    return path.resolve(strict=True)


def _contained_directory(root: Path, relative: PurePosixPath, *, create: bool = True) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if create:
            current.mkdir(exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise BackupIntegrityError("Backup storage path is invalid")
        resolved = current.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise BackupIntegrityError("Backup storage path is invalid")
        current = resolved
    return current


def _publish_file(
    source: Path,
    destination: Path,
    expected_sha256: str,
    expected_size: int,
    policy: BackupPolicy,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink():
        raise BackupIntegrityError("Backup publication path is invalid")
    if os.path.lexists(destination):
        if destination.is_symlink() or not destination.is_file():
            raise BackupIntegrityError("Backup publication target is invalid")
        actual_sha256, actual_size = _hash_file(
            destination, policy.chunk_size_bytes, policy.max_total_bytes
        )
        if actual_sha256 != expected_sha256 or actual_size != expected_size:
            raise BackupIntegrityError("Backup publication target differs")
        return
    try:
        os.link(source, destination)
    except FileExistsError:
        _publish_file(source, destination, expected_sha256, expected_size, policy)


def _artifact(path: Path, manifest: dict[str, Any], sha256: str, size_bytes: int) -> BackupArtifact:
    modified = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="microseconds")
    return BackupArtifact(
        id=sha256,
        filename=f"lifecase-backup-{sha256[:16]}.zip",
        sha256=sha256,
        size_bytes=size_bytes,
        format_version=int(manifest["format_version"]),
        minimum_application_version=str(manifest["minimum_application_version"]),
        case_count=len(manifest["scope"]["case_ids"]),
        member_count=len(manifest["members"]) + 3,
        created_at=modified.replace("+00:00", "Z"),
    )


def _write_file(
    archive: zipfile.ZipFile,
    name: str,
    path: Path,
    expected_sha256: str,
    policy: BackupPolicy,
) -> None:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as source, archive.open(_zip_info(name), "w", force_zip64=False) as output:
        while chunk := source.read(policy.chunk_size_bytes):
            size_bytes += len(chunk)
            if size_bytes > policy.max_member_bytes:
                raise BackupLimitError("Backup member limit was exceeded")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != expected_sha256:
        raise BackupIntegrityError("Backup member changed while being written")


def _write_bytes(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    archive.writestr(_zip_info(name), content)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def _parse_checksums(value: str) -> dict[str, str]:
    if not value.endswith("\n"):
        raise BackupIntegrityError("Backup checksums are invalid")
    result: dict[str, str] = {}
    folded: set[str] = set()
    for line in value.splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise BackupIntegrityError("Backup checksums are invalid")
        digest = _sha256(line[:64], "Backup checksums are invalid")
        path = _portable_path(line[66:]).as_posix()
        if path in result or path.casefold() in folded:
            raise BackupIntegrityError("Backup checksums are invalid")
        result[path] = digest
        folded.add(path.casefold())
    return result


def _portable_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or len(value) > 500 or "\\" in value:
        raise BackupIntegrityError("Backup path is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise BackupIntegrityError("Backup path is invalid")
    if value.startswith("//") or value.endswith("/"):
        raise BackupIntegrityError("Backup path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupIntegrityError("Backup path is invalid")
    if ":" in path.parts[0]:
        raise BackupIntegrityError("Backup path is invalid")
    return path


def _target_name(value: object) -> str:
    if not isinstance(value, str) or _TARGET_NAME.fullmatch(value) is None or value in {".", ".."}:
        raise BackupValidationError("Restore target name is invalid")
    return value


def _sha256(value: object, message: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BackupIntegrityError(message)
    return value


def _command_sha256(value: object, message: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BackupValidationError(message)
    return value


def _bounded_size(value: object, policy: BackupPolicy) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= policy.max_member_bytes
    ):
        raise BackupLimitError("Backup member size is invalid")
    return value


def _hash_file(path: Path, chunk_size: int, maximum: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            size_bytes += len(chunk)
            if size_bytes > maximum:
                raise BackupLimitError("Backup file limit was exceeded")
            digest.update(chunk)
    return digest.hexdigest(), size_bytes


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackupIntegrityError("Backup manifest is invalid") from error
    if not isinstance(value, dict):
        raise BackupIntegrityError("Backup manifest is invalid")
    return value


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, separators=(",", ": "))
        + "\n"
    ).encode("utf-8")


def _compatible_version(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        required = tuple(int(part) for part in value.split("."))
        current = tuple(int(part) for part in __version__.split("."))
    except ValueError:
        return False
    return len(required) == 3 and len(current) == 3 and required <= current
