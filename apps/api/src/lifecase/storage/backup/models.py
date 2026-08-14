"""Immutable M6-02 backup and restore values."""

from dataclasses import dataclass
from pathlib import Path

BACKUP_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class BackupPolicy:
    max_members: int = 4096
    max_member_bytes: int = 512 * 1024 * 1024
    max_total_bytes: int = 4 * 1024 * 1024 * 1024
    chunk_size_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if (
            self.max_members < 3
            or self.max_member_bytes <= 0
            or self.max_total_bytes <= 0
            or self.chunk_size_bytes <= 0
            or self.max_member_bytes > self.max_total_bytes
        ):
            raise ValueError("Backup policy is invalid")


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    id: str
    filename: str
    sha256: str
    size_bytes: int
    format_version: int
    minimum_application_version: str
    case_count: int
    member_count: int
    created_at: str


@dataclass(frozen=True, slots=True)
class BackupDownload:
    artifact: BackupArtifact
    path: Path


@dataclass(frozen=True, slots=True)
class RestoreValidation:
    validation_id: str
    backup_sha256: str
    format_version: int
    minimum_application_version: str
    case_count: int
    member_count: int
    database_sha256: str


@dataclass(frozen=True, slots=True)
class RestoreResult:
    validation_id: str
    runtime_id: str
    case_count: int
    migrated: bool
