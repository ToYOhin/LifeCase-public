"""Transactional extraction service for immutable Source objects."""

import hashlib
import json
import sqlite3
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import cast

from lifecase.evidence.extraction.eml_extractor import EML_EXTRACTOR_VERSION
from lifecase.evidence.extraction.errors import (
    EmptyContentError,
    ExtractionError,
    ObjectIntegrityError,
    ObjectPathError,
    OcrRequiredError,
    SegmentPersistenceError,
    SourceNotFoundError,
)
from lifecase.evidence.extraction.models import (
    ExtractedSegment,
    ExtractionConfig,
    ExtractionNotice,
    ExtractionResult,
    ParsedDocument,
    SegmentDraft,
)
from lifecase.evidence.extraction.pdf_extractor import PDF_EXTRACTOR_VERSION
from lifecase.evidence.extraction.segmentation import segment_text_parts
from lifecase.evidence.extraction.text_extractor import TEXT_EXTRACTOR_VERSION
from lifecase.evidence.extraction.worker import parse_in_isolated_process
from lifecase.retrieval import RetrievalError
from lifecase.retrieval.index import delete_source_projection, insert_source_projection

type ParserRunner = Callable[[Path, str, ExtractionConfig], ParsedDocument]

_EXPECTED_EXTRACTOR_VERSIONS = {
    "application/pdf": PDF_EXTRACTOR_VERSION,
    "message/rfc822": EML_EXTRACTOR_VERSION,
    "text/plain": TEXT_EXTRACTOR_VERSION,
}


class ExtractionService:
    """Validate, parse, segment, and atomically persist one Source."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        config: ExtractionConfig,
        *,
        parser_runner: ParserRunner = parse_in_isolated_process,
    ) -> None:
        self._connection = connection
        self._config = config
        self._parser_runner = parser_runner
        self._data_root = config.data_root.expanduser().resolve()

    def extract_source(self, source_id: str) -> ExtractionResult:
        """Extract only the immutable object referenced by an existing Source row."""

        _require_ready_connection(self._connection)
        source = _load_source(self._connection, source_id)
        object_path = _resolve_object_path(
            self._data_root,
            str(source["object_path"]),
            str(source["sha256"]),
        )
        _verify_object(
            object_path,
            expected_sha256=str(source["sha256"]),
            expected_size=int(source["size_bytes"]),
        )

        media_type = str(source["media_type"])
        try:
            parsed = self._parser_runner(object_path, media_type, self._config)
            drafts = segment_text_parts(
                parsed.parts,
                max_segment_chars=self._config.max_segment_chars,
            )
            if not drafts:
                raise EmptyContentError
        except ExtractionError as error:
            self._record_failure(
                source_id,
                _EXPECTED_EXTRACTOR_VERSIONS.get(media_type, "unavailable"),
                error,
            )
            raise

        _verify_object(
            object_path,
            expected_sha256=str(source["sha256"]),
            expected_size=int(source["size_bytes"]),
        )
        segments = _materialize_segments(source_id, parsed.extractor_version, drafts)
        status = "completed_with_notices" if parsed.notices else "completed"
        self._persist_success(
            source,
            parsed.extractor_version,
            status,
            segments,
            parsed.notices,
        )
        return ExtractionResult(
            source_id=source_id,
            status=status,
            extractor_version=parsed.extractor_version,
            segments=segments,
            notices=parsed.notices,
        )

    def _persist_success(
        self,
        source: sqlite3.Row,
        extractor_version: str,
        status: str,
        segments: tuple[ExtractedSegment, ...],
        notices: tuple[ExtractionNotice, ...],
    ) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            _require_source_unchanged(self._connection, source)
            delete_source_projection(self._connection, str(source["id"]))
            self._connection.execute(
                "DELETE FROM source_segment WHERE source_id = ?",
                (str(source["id"]),),
            )
            self._connection.executemany(
                """
                INSERT INTO source_segment (
                    id, source_id, sequence, page, bbox_json, text,
                    extraction_method, confidence, text_sha256, extractor_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        segment.id,
                        segment.source_id,
                        segment.sequence,
                        segment.page,
                        segment.bbox_json,
                        segment.text,
                        segment.extraction_method,
                        segment.confidence,
                        segment.text_sha256,
                        segment.extractor_version,
                    )
                    for segment in segments
                ],
            )
            insert_source_projection(self._connection, str(source["id"]))
            self._connection.execute(
                """
                INSERT INTO source_extraction (
                    source_id, extractor_version, status, segment_count,
                    notices_json, error_code, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    extractor_version = excluded.extractor_version,
                    status = excluded.status,
                    segment_count = excluded.segment_count,
                    notices_json = excluded.notices_json,
                    error_code = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    str(source["id"]),
                    extractor_version,
                    status,
                    len(segments),
                    _serialize_notices(notices),
                    _utc_now(),
                ),
            )
            self._connection.execute("COMMIT")
        except ExtractionError:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except (sqlite3.Error, RetrievalError) as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SegmentPersistenceError from error

    def _record_failure(
        self, source_id: str, extractor_version: str, error: ExtractionError
    ) -> None:
        status = "ocr_required" if isinstance(error, OcrRequiredError) else "failed"
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                "SELECT COUNT(*) FROM source_segment WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            segment_count = int(existing[0]) if existing is not None else 0
            self._connection.execute(
                """
                INSERT INTO source_extraction (
                    source_id, extractor_version, status, segment_count,
                    notices_json, error_code, updated_at
                ) VALUES (?, ?, ?, ?, '[]', ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    extractor_version = excluded.extractor_version,
                    status = excluded.status,
                    segment_count = excluded.segment_count,
                    notices_json = '[]',
                    error_code = excluded.error_code,
                    updated_at = excluded.updated_at
                """,
                (
                    source_id,
                    extractor_version,
                    status,
                    segment_count,
                    error.code,
                    _utc_now(),
                ),
            )
            self._connection.execute("COMMIT")
        except sqlite3.Error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")


def _require_ready_connection(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise SegmentPersistenceError
    try:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
    except sqlite3.Error as error:
        raise SegmentPersistenceError from error
    if foreign_keys is None or foreign_keys[0] != 1:
        raise SegmentPersistenceError


def _load_source(connection: sqlite3.Connection, source_id: str) -> sqlite3.Row:
    try:
        row = connection.execute(
            """
            SELECT id, case_id, media_type, sha256, size_bytes, object_path
            FROM source WHERE id = ?
            """,
            (source_id,),
        ).fetchone()
    except sqlite3.Error as error:
        raise SegmentPersistenceError from error
    if row is None:
        raise SourceNotFoundError
    return cast(sqlite3.Row, row)


def _resolve_object_path(data_root: Path, value: str, sha256: str) -> Path:
    relative = PurePosixPath(value)
    expected = PurePosixPath("objects", sha256[:2], sha256[2:4], sha256)
    if (
        not value
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
        or "\\" in value
        or ":" in value
        or relative.is_absolute()
        or ".." in relative.parts
        or relative != expected
    ):
        raise ObjectPathError
    try:
        resolved_root = data_root.resolve(strict=True)
        candidate = data_root.joinpath(*relative.parts)
        if candidate.is_symlink():
            raise ObjectPathError
        resolved = candidate.resolve(strict=True)
        file_stat = resolved.stat()
    except ExtractionError:
        raise
    except OSError as error:
        raise ObjectPathError from error
    if not resolved.is_relative_to(resolved_root) or not stat.S_ISREG(file_stat.st_mode):
        raise ObjectPathError
    return resolved


def _verify_object(path: Path, *, expected_sha256: str, expected_size: int) -> None:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise ObjectIntegrityError from error
    if size != expected_size or digest.hexdigest() != expected_sha256:
        raise ObjectIntegrityError


def _materialize_segments(
    source_id: str,
    extractor_version: str,
    drafts: tuple[SegmentDraft, ...],
) -> tuple[ExtractedSegment, ...]:
    return tuple(
        ExtractedSegment(
            id=_segment_id(source_id, extractor_version, draft),
            source_id=source_id,
            sequence=draft.sequence,
            page=draft.page,
            bbox_json=draft.bbox_json,
            text=draft.text,
            extraction_method=draft.extraction_method,
            extractor_version=extractor_version,
            confidence=draft.confidence,
            text_sha256=draft.text_sha256,
        )
        for draft in drafts
    )


def _segment_id(source_id: str, extractor_version: str, draft: SegmentDraft) -> str:
    payload = "\x1f".join(
        (
            source_id,
            extractor_version,
            str(draft.sequence),
            str(draft.page or ""),
            draft.text_sha256,
        )
    ).encode()
    return f"seg_{hashlib.sha256(payload).hexdigest()}"


def _require_source_unchanged(connection: sqlite3.Connection, expected: sqlite3.Row) -> None:
    actual = connection.execute(
        """
        SELECT case_id, media_type, sha256, size_bytes, object_path
        FROM source WHERE id = ?
        """,
        (str(expected["id"]),),
    ).fetchone()
    expected_values = tuple(
        expected[column]
        for column in ("case_id", "media_type", "sha256", "size_bytes", "object_path")
    )
    if actual is None or tuple(actual) != expected_values:
        raise ObjectIntegrityError


def _serialize_notices(notices: tuple[ExtractionNotice, ...]) -> str:
    return json.dumps(
        [{"code": notice.code, "count": notice.count} for notice in notices],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
