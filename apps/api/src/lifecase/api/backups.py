"""Thin local HTTP contracts for M6-02 backup and clean-target restore."""

import os
import tempfile
from pathlib import Path
from typing import Annotated, NoReturn

from fastapi import APIRouter, File, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from lifecase.api.dependencies import ConfigDependency, DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.storage.backup import (
    BackupArtifact,
    BackupIntegrityError,
    BackupLimitError,
    BackupNotFoundError,
    BackupPersistenceError,
    BackupPolicy,
    BackupRestoreError,
    BackupService,
    BackupValidationError,
    RestoreApplyError,
    RestoreResult,
    RestoreTargetNotCleanError,
    RestoreValidation,
)

router = APIRouter()


class BackupResponse(BaseModel):
    id: str
    filename: str
    sha256: str
    size_bytes: int
    format_version: int
    minimum_application_version: str
    case_count: int
    member_count: int
    created_at: str
    download_url: str


class RestoreValidationResponse(BaseModel):
    validation_id: str
    backup_sha256: str
    format_version: int
    minimum_application_version: str
    case_count: int
    member_count: int
    database_sha256: str


class RestoreApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validation_id: str = Field(min_length=64, max_length=64)
    target_name: str = Field(min_length=1, max_length=80)
    confirmation: str = Field(min_length=1, max_length=64)


class RestoreApplyResponse(BaseModel):
    validation_id: str
    runtime_id: str
    case_count: int
    migrated: bool


@router.post("/backups", response_model=BackupResponse, status_code=status.HTTP_201_CREATED)
def create_backup(
    database: DatabaseDependency,
    config: ConfigDependency,
) -> BackupResponse:
    try:
        artifact = BackupService(database, config.database_path, config.data_root).create()
    except BackupRestoreError as error:
        _raise_backup_error(error)
    return _backup_response(artifact)


@router.get("/backups", response_model=BackupResponse)
def get_latest_backup(
    database: DatabaseDependency,
    config: ConfigDependency,
) -> BackupResponse:
    try:
        artifact = BackupService(database, config.database_path, config.data_root).get_latest()
    except BackupRestoreError as error:
        _raise_backup_error(error)
    return _backup_response(artifact)


@router.get("/backups/{backup_id}")
def download_backup(
    backup_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
) -> FileResponse:
    try:
        download = BackupService(database, config.database_path, config.data_root).get_download(
            backup_id
        )
    except BackupRestoreError as error:
        _raise_backup_error(error)
    return FileResponse(
        path=download.path,
        media_type="application/zip",
        filename=download.artifact.filename,
        headers={
            "X-Content-Type-Options": "nosniff",
            "X-LifeCase-Content-SHA256": download.artifact.sha256,
            "X-LifeCase-Backup-Format": str(download.artifact.format_version),
        },
    )


@router.post("/restores/validate", response_model=RestoreValidationResponse)
def validate_restore(
    file: Annotated[UploadFile, File()],
    database: DatabaseDependency,
    config: ConfigDependency,
) -> RestoreValidationResponse:
    upload_path: Path | None = None
    try:
        runtime_root = config.database_path.parent.resolve(strict=True)
        recovery_root = runtime_root / "recovery"
        recovery_root.mkdir(exist_ok=True)
        if recovery_root.is_symlink() or not recovery_root.resolve(strict=True).is_relative_to(
            runtime_root
        ):
            raise BackupIntegrityError("Backup upload storage is invalid")
        upload_root = recovery_root / "incoming"
        upload_root.mkdir(exist_ok=True)
        if upload_root.is_symlink() or not upload_root.resolve(strict=True).is_relative_to(
            runtime_root
        ):
            raise BackupIntegrityError("Backup upload storage is invalid")
        descriptor, name = tempfile.mkstemp(
            prefix=".lifecase-upload-", suffix=".zip", dir=upload_root
        )
        upload_path = Path(name)
        size_bytes = 0
        with os.fdopen(descriptor, "wb") as output:
            while chunk := file.file.read(BackupPolicy().chunk_size_bytes):
                size_bytes += len(chunk)
                if size_bytes > BackupPolicy().max_total_bytes:
                    raise BackupLimitError("Backup upload limit was exceeded")
                output.write(chunk)
        validation = BackupService(database, config.database_path, config.data_root).validate(
            upload_path
        )
    except BackupRestoreError as error:
        _raise_backup_error(error)
    except OSError:
        _raise_backup_error(BackupPersistenceError("Backup upload is unavailable"))
    finally:
        if upload_path is not None:
            upload_path.unlink(missing_ok=True)
    return _validation_response(validation)


@router.post("/restores/apply", response_model=RestoreApplyResponse)
def apply_restore(
    request: RestoreApplyRequest,
    database: DatabaseDependency,
    config: ConfigDependency,
) -> RestoreApplyResponse:
    try:
        result = BackupService(database, config.database_path, config.data_root).apply(
            request.validation_id,
            request.target_name,
            request.confirmation,
        )
    except BackupRestoreError as error:
        _raise_backup_error(error)
    return _restore_response(result)


def _backup_response(artifact: BackupArtifact) -> BackupResponse:
    return BackupResponse(
        **{field: getattr(artifact, field) for field in artifact.__dataclass_fields__},
        download_url=f"/api/v1/backups/{artifact.id}",
    )


def _validation_response(value: RestoreValidation) -> RestoreValidationResponse:
    return RestoreValidationResponse(
        **{field: getattr(value, field) for field in value.__dataclass_fields__}
    )


def _restore_response(value: RestoreResult) -> RestoreApplyResponse:
    return RestoreApplyResponse(
        **{field: getattr(value, field) for field in value.__dataclass_fields__}
    )


def _raise_backup_error(error: BackupRestoreError) -> NoReturn:
    if isinstance(error, BackupValidationError):
        raise ApiError(
            status_code=422,
            code="backup_request_invalid",
            message="Backup request is invalid",
        ) from error
    if isinstance(error, BackupNotFoundError):
        raise ApiError(
            status_code=404, code="backup_not_found", message="Backup was not found"
        ) from error
    if isinstance(error, RestoreTargetNotCleanError):
        raise ApiError(
            status_code=409,
            code="restore_target_not_clean",
            message="Restore target must be a new empty location",
        ) from error
    if isinstance(error, BackupIntegrityError):
        raise ApiError(
            status_code=409,
            code="backup_integrity_failed",
            message="Backup validation failed",
        ) from error
    if isinstance(error, BackupLimitError):
        raise ApiError(
            status_code=413,
            code="backup_limit_exceeded",
            message="Backup exceeds a safe local limit",
        ) from error
    if isinstance(error, RestoreApplyError):
        raise ApiError(
            status_code=503,
            code="restore_apply_failed",
            message="Verified backup could not be restored",
            retryable=True,
        ) from error
    if isinstance(error, BackupPersistenceError):
        raise ApiError(
            status_code=503,
            code="backup_unavailable",
            message="Backup storage is unavailable",
            retryable=True,
        ) from error
    raise ApiError(
        status_code=503,
        code="backup_unavailable",
        message="Backup storage is unavailable",
    ) from error
