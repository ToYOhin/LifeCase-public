"""M1 Source upload and detail HTTP contracts."""

import sqlite3
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, File, UploadFile, status
from pydantic import BaseModel

from lifecase.api.dependencies import ConfigDependency, DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.cases.service import (
    CaseNotFoundError as CaseRecordNotFoundError,
)
from lifecase.cases.service import (
    CaseService,
    CaseServiceError,
)
from lifecase.evidence import (
    CaseNotFoundError,
    SourceDetail,
    SourceDisplayNameError,
    SourceMetadataError,
    SourceNotFoundError,
    SourceReadError,
    SourceSegmentDetail,
    SourceService,
)
from lifecase.evidence.extraction import (
    ExtractionConfig,
    ExtractionError,
    ExtractionService,
)
from lifecase.evidence.extraction import (
    ObjectIntegrityError as ExtractionObjectIntegrityError,
)
from lifecase.evidence.source_import import SourceImporter
from lifecase.evidence.upload import UploadStagingError, stage_upload
from lifecase.storage.object_store import (
    ContentAddressedObjectStore,
    InvalidSourcePathError,
    ObjectIntegrityError,
    ObjectStoreConfig,
    ObjectWriteError,
    SourceChangedError,
    SourceOutsideAllowedRootError,
    SourceSizeLimitError,
    UnsupportedMediaTypeError,
)

router = APIRouter()


class SourceNoticeResponse(BaseModel):
    code: str
    count: int


class SourceExtractionResponse(BaseModel):
    status: str
    extractor_version: str | None
    segment_count: int
    notices: list[SourceNoticeResponse]
    error_code: str | None


class SourceResponse(BaseModel):
    id: str
    case_id: str
    original_name: str
    media_type: str
    sha256: str
    size_bytes: int
    trust_label: str
    imported_at: str
    extraction: SourceExtractionResponse


class SourceSegmentResponse(BaseModel):
    id: str
    source_id: str
    source_name: str
    sequence: int
    page: int | None
    text: str
    extraction_method: str
    extractor_version: str | None
    confidence: float | None
    text_sha256: str


@router.post(
    "/cases/{case_id}/sources",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_source(
    case_id: str,
    file: Annotated[UploadFile, File(description="Explicitly selected evidence file")],
    database: DatabaseDependency,
    config: ConfigDependency,
) -> SourceResponse:
    """Stream one browser-selected file through existing safe intake services."""

    staged = None
    try:
        staged = stage_upload(
            file.file,
            submitted_name=file.filename,
            upload_root=config.upload_root,
            max_size_bytes=config.max_upload_bytes,
        )
        object_store = ContentAddressedObjectStore(
            ObjectStoreConfig(
                data_root=config.data_root,
                allowed_source_roots=(config.upload_root,),
                max_file_size_bytes=config.max_upload_bytes,
                allowed_media_types=config.allowed_media_types,
            )
        )
        service = SourceService(
            database,
            SourceImporter(database, object_store),
            ExtractionService(database, ExtractionConfig(data_root=config.data_root)),
        )
        detail = service.import_source(
            case_id=case_id,
            selected_path=staged.path,
            display_name=staged.display_name,
        )
    except CaseNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="case_not_found",
            message="Case was not found",
        ) from error
    except SourceSizeLimitError as error:
        raise ApiError(
            status_code=413,
            code="source_too_large",
            message="Selected file exceeds the configured size limit",
        ) from error
    except UnsupportedMediaTypeError as error:
        raise ApiError(
            status_code=415,
            code="media_type_unsupported",
            message="Selected file type is not supported",
        ) from error
    except SourceOutsideAllowedRootError as error:
        raise ApiError(
            status_code=403,
            code="source_path_outside_allowed_root",
            message="Selected file is outside the allowed import area",
        ) from error
    except (InvalidSourcePathError, SourceDisplayNameError, UploadStagingError) as error:
        raise ApiError(
            status_code=400,
            code="source_path_invalid",
            message="Selected file is unavailable or invalid",
        ) from error
    except (
        ObjectIntegrityError,
        ExtractionObjectIntegrityError,
        SourceChangedError,
    ) as error:
        raise ApiError(
            status_code=409,
            code="source_integrity_failed",
            message="Selected file failed integrity validation",
        ) from error
    except ExtractionError as error:
        raise ApiError(
            status_code=422,
            code="extraction_failed",
            message="Selected file could not be safely extracted",
        ) from error
    except (ObjectWriteError, SourceMetadataError, SourceReadError) as error:
        raise ApiError(
            status_code=503,
            code="source_storage_failed",
            message="Selected file could not be stored",
            retryable=True,
        ) from error
    finally:
        if staged is not None:
            staged.discard()
        with suppress(OSError):
            file.file.close()
    return source_response(detail)


@router.get("/sources/{source_id}", response_model=SourceResponse)
def get_source(source_id: str, database: DatabaseDependency) -> SourceResponse:
    """Return persisted Source metadata without storage paths or document bodies."""

    try:
        detail = _read_service(database).get_source(source_id)
    except SourceNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="source_not_found",
            message="Source was not found",
        ) from error
    except SourceReadError as error:
        raise ApiError(
            status_code=503,
            code="source_storage_failed",
            message="Source could not be read",
            retryable=True,
        ) from error
    return source_response(detail)


@router.get(
    "/cases/{case_id}/source-segments",
    response_model=list[SourceSegmentResponse],
)
def list_case_source_segments(
    case_id: str,
    database: DatabaseDependency,
) -> list[SourceSegmentResponse]:
    """Return extracted text only within one explicit Case scope."""

    try:
        CaseService(database).get_case(case_id)
        segments = _read_service(database).list_case_segments(case_id)
    except CaseRecordNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="case_not_found",
            message="Case was not found",
        ) from error
    except (CaseServiceError, SourceReadError) as error:
        raise ApiError(
            status_code=503,
            code="source_storage_failed",
            message="Case source segments could not be read",
            retryable=True,
        ) from error
    return [_source_segment_response(segment) for segment in segments]


def list_case_source_responses(database: sqlite3.Connection, case_id: str) -> list[SourceResponse]:
    """Return response projections for one Case from authoritative storage."""

    try:
        return [
            source_response(item) for item in _read_service(database).list_case_sources(case_id)
        ]
    except SourceReadError as error:
        raise ApiError(
            status_code=503,
            code="source_storage_failed",
            message="Case sources could not be read",
            retryable=True,
        ) from error


def source_response(detail: SourceDetail) -> SourceResponse:
    return SourceResponse(
        id=detail.id,
        case_id=detail.case_id,
        original_name=detail.original_name,
        media_type=detail.media_type,
        sha256=detail.sha256,
        size_bytes=detail.size_bytes,
        trust_label=detail.trust_label,
        imported_at=detail.imported_at,
        extraction=SourceExtractionResponse(
            status=detail.extraction_status,
            extractor_version=detail.extractor_version,
            segment_count=detail.segment_count,
            notices=[
                SourceNoticeResponse(code=notice.code, count=notice.count)
                for notice in detail.notices
            ],
            error_code=detail.extraction_error_code,
        ),
    )


def _source_segment_response(detail: SourceSegmentDetail) -> SourceSegmentResponse:
    return SourceSegmentResponse(
        id=detail.id,
        source_id=detail.source_id,
        source_name=detail.source_name,
        sequence=detail.sequence,
        page=detail.page,
        text=detail.text,
        extraction_method=detail.extraction_method,
        extractor_version=detail.extractor_version,
        confidence=detail.confidence,
        text_sha256=detail.text_sha256,
    )


def _read_service(database: sqlite3.Connection) -> SourceService:
    return SourceService(database)
