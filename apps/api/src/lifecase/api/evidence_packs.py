"""Thin HTTP contracts for retained M6-01 Evidence Pack artifacts."""

from typing import NoReturn

from fastapi import APIRouter, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from lifecase.api.dependencies import ConfigDependency, DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.evidence.pack import (
    EvidencePackArtifact,
    EvidencePackArtifactNotFoundError,
    EvidencePackCaseNotFoundError,
    EvidencePackError,
    EvidencePackIntegrityError,
    EvidencePackLimitError,
    EvidencePackPersistenceError,
    EvidencePackService,
    EvidencePackValidationError,
)

router = APIRouter()


class EvidencePackResponse(BaseModel):
    id: str
    case_id: str
    filename: str
    sha256: str
    size_bytes: int
    format_version: int
    created_at: str
    download_url: str


@router.post(
    "/cases/{case_id}/evidence-pack",
    response_model=EvidencePackResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_evidence_pack(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
) -> EvidencePackResponse:
    try:
        artifact = EvidencePackService(database, config.data_root).create(case_id)
    except EvidencePackError as error:
        _raise_pack_error(error)
    return _response(artifact)


@router.get(
    "/cases/{case_id}/evidence-pack",
    response_model=EvidencePackResponse,
)
def get_latest_evidence_pack(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
) -> EvidencePackResponse:
    try:
        artifact = EvidencePackService(database, config.data_root).get_latest(case_id)
    except EvidencePackError as error:
        _raise_pack_error(error)
    return _response(artifact)


@router.get("/artifacts/{artifact_id}")
def download_artifact(
    artifact_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
) -> FileResponse:
    try:
        download = EvidencePackService(database, config.data_root).get_download(artifact_id)
    except EvidencePackError as error:
        _raise_pack_error(error)
    return FileResponse(
        path=download.path,
        media_type="application/zip",
        filename=download.artifact.filename,
        headers={
            "X-Content-Type-Options": "nosniff",
            "X-LifeCase-Content-SHA256": download.artifact.sha256,
            "X-LifeCase-Evidence-Pack-Format": str(download.artifact.format_version),
        },
    )


def _response(artifact: EvidencePackArtifact) -> EvidencePackResponse:
    return EvidencePackResponse(
        id=artifact.id,
        case_id=artifact.case_id,
        filename=artifact.filename,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        format_version=artifact.format_version,
        created_at=artifact.created_at,
        download_url=f"/api/v1/artifacts/{artifact.id}",
    )


def _raise_pack_error(error: EvidencePackError) -> NoReturn:
    if isinstance(error, EvidencePackValidationError):
        raise ApiError(
            status_code=422,
            code="evidence_pack_invalid",
            message="Evidence Pack request is invalid",
        ) from error
    if isinstance(error, EvidencePackCaseNotFoundError):
        raise ApiError(
            status_code=404,
            code="case_not_found",
            message="Case was not found",
        ) from error
    if isinstance(error, EvidencePackArtifactNotFoundError):
        raise ApiError(
            status_code=404,
            code="artifact_not_found",
            message="Artifact was not found",
        ) from error
    if isinstance(error, EvidencePackIntegrityError):
        raise ApiError(
            status_code=409,
            code="evidence_pack_integrity_failed",
            message="Evidence Pack authority is invalid",
        ) from error
    if isinstance(error, EvidencePackLimitError):
        raise ApiError(
            status_code=413,
            code="evidence_pack_limit_exceeded",
            message="Evidence Pack exceeds a safe local limit",
        ) from error
    if isinstance(error, EvidencePackPersistenceError):
        raise ApiError(
            status_code=503,
            code="evidence_pack_unavailable",
            message="Evidence Pack is unavailable",
            retryable=True,
        ) from error
    raise ApiError(
        status_code=503,
        code="evidence_pack_unavailable",
        message="Evidence Pack is unavailable",
    ) from error
