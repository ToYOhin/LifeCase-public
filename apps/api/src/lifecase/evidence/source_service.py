"""Persistent Source intake orchestration and safe read projections."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from lifecase.evidence.extraction import ExtractionError, ExtractionService
from lifecase.evidence.source_import import SourceImporter


class SourceServiceError(RuntimeError):
    """Base class for Source projection failures."""


class SourceNotFoundError(SourceServiceError):
    """Raised when a Source does not exist in the requested scope."""


class SourceReadError(SourceServiceError):
    """Raised when persisted Source state is unavailable or invalid."""


@dataclass(frozen=True, slots=True)
class SourceNotice:
    code: str
    count: int


@dataclass(frozen=True, slots=True)
class SourceDetail:
    id: str
    case_id: str
    original_name: str
    media_type: str
    sha256: str
    size_bytes: int
    trust_label: str
    imported_at: str
    extraction_status: str
    extractor_version: str | None
    segment_count: int
    notices: tuple[SourceNotice, ...]
    extraction_error_code: str | None


@dataclass(frozen=True, slots=True)
class SourceSegmentDetail:
    """One extracted text segment safe for explicit local review."""

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


class SourceService:
    """Compose existing import/extraction services and read durable Source state."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        importer: SourceImporter | None = None,
        extractor: ExtractionService | None = None,
    ) -> None:
        self._connection = connection
        self._importer = importer
        self._extractor = extractor

    def import_source(
        self,
        *,
        case_id: str,
        selected_path: Path,
        display_name: str,
    ) -> SourceDetail:
        if self._importer is None or self._extractor is None:
            raise SourceServiceError("Source intake dependencies are unavailable")
        imported = self._importer.import_selected(
            case_id=case_id,
            selected_path=selected_path,
            display_name=display_name,
        )
        try:
            self._extractor.extract_source(imported.source_id)
        except ExtractionError:
            try:
                state = self._connection.execute(
                    "SELECT 1 FROM source_extraction WHERE source_id = ?",
                    (imported.source_id,),
                ).fetchone()
            except sqlite3.Error as error:
                raise SourceReadError("Extraction state could not be read") from error
            if state is None:
                raise
        return self.get_source(imported.source_id, case_id=case_id)

    def get_source(self, source_id: str, *, case_id: str | None = None) -> SourceDetail:
        query = """
            SELECT s.id, s.case_id, s.original_name, s.media_type, s.sha256,
                   s.size_bytes, s.trust_label, s.imported_at,
                   e.status AS extraction_status,
                   e.extractor_version, e.segment_count, e.notices_json,
                   e.error_code
            FROM source AS s
            LEFT JOIN source_extraction AS e ON e.source_id = s.id
            WHERE s.id = ?
        """
        parameters: tuple[str, ...] = (source_id,)
        if case_id is not None:
            query += " AND s.case_id = ?"
            parameters = (source_id, case_id)
        try:
            row = self._connection.execute(query, parameters).fetchone()
        except sqlite3.Error as error:
            raise SourceReadError("Source lookup failed") from error
        if row is None:
            raise SourceNotFoundError
        return _source_detail(row)

    def list_case_sources(self, case_id: str) -> tuple[SourceDetail, ...]:
        try:
            rows = self._connection.execute(
                """
                SELECT s.id, s.case_id, s.original_name, s.media_type, s.sha256,
                       s.size_bytes, s.trust_label, s.imported_at,
                       e.status AS extraction_status,
                       e.extractor_version, e.segment_count, e.notices_json,
                       e.error_code
                FROM source AS s
                LEFT JOIN source_extraction AS e ON e.source_id = s.id
                WHERE s.case_id = ?
                ORDER BY s.imported_at, s.id
                """,
                (case_id,),
            ).fetchall()
        except sqlite3.Error as error:
            raise SourceReadError("Case sources could not be read") from error
        return tuple(_source_detail(row) for row in rows)

    def list_case_segments(self, case_id: str) -> tuple[SourceSegmentDetail, ...]:
        """List authoritative extracted text for one explicitly selected Case."""

        try:
            rows = self._connection.execute(
                """
                SELECT ss.id, ss.source_id, s.original_name AS source_name,
                       ss.sequence, ss.page, ss.text, ss.extraction_method,
                       ss.extractor_version, ss.confidence, ss.text_sha256
                FROM source_segment AS ss
                JOIN source AS s ON s.id = ss.source_id
                WHERE s.case_id = ?
                ORDER BY s.imported_at, s.id, ss.sequence, ss.id
                LIMIT 2000
                """,
                (case_id,),
            ).fetchall()
        except sqlite3.Error as error:
            raise SourceReadError("Case source segments could not be read") from error

        segments: list[SourceSegmentDetail] = []
        for row in rows:
            text = str(row["text"])
            text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if str(row["text_sha256"]) != text_sha256:
                raise SourceReadError("Source segment integrity validation failed")
            segments.append(
                SourceSegmentDetail(
                    id=str(row["id"]),
                    source_id=str(row["source_id"]),
                    source_name=str(row["source_name"]),
                    sequence=int(row["sequence"]),
                    page=int(row["page"]) if row["page"] is not None else None,
                    text=text,
                    extraction_method=str(row["extraction_method"]),
                    extractor_version=(
                        str(row["extractor_version"])
                        if row["extractor_version"] is not None
                        else None
                    ),
                    confidence=(
                        float(row["confidence"]) if row["confidence"] is not None else None
                    ),
                    text_sha256=text_sha256,
                )
            )
        return tuple(segments)


def _source_detail(row: sqlite3.Row) -> SourceDetail:
    try:
        raw_notices = json.loads(str(row["notices_json"] or "[]"))
        notices = tuple(
            SourceNotice(code=str(item["code"]), count=int(item["count"])) for item in raw_notices
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SourceReadError("Source extraction notices are invalid") from error
    return SourceDetail(
        id=str(row["id"]),
        case_id=str(row["case_id"]),
        original_name=str(row["original_name"]),
        media_type=str(row["media_type"]),
        sha256=str(row["sha256"]),
        size_bytes=int(row["size_bytes"]),
        trust_label=str(row["trust_label"]),
        imported_at=str(row["imported_at"]),
        extraction_status=str(row["extraction_status"] or "pending"),
        extractor_version=(
            str(row["extractor_version"]) if row["extractor_version"] is not None else None
        ),
        segment_count=int(row["segment_count"] or 0),
        notices=notices,
        extraction_error_code=(str(row["error_code"]) if row["error_code"] else None),
    )
