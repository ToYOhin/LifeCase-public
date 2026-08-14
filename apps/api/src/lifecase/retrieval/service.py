"""Case-scoped FTS5 retrieval with authoritative citation validation."""

import hashlib
import sqlite3

from lifecase.retrieval.errors import (
    RetrievalIndexError,
    RetrievalIntegrityError,
    RetrievalLimitError,
    RetrievalQueryError,
)
from lifecase.retrieval.index import rebuild_projection
from lifecase.retrieval.models import RetrievalConfig, RetrievalIndexStatus, RetrievalResult
from lifecase.retrieval.schema import FTS_TABLE_NAME, projection_exists


class RetrievalService:
    """Search only authoritative evidence belonging to an explicit Case."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._connection = connection
        self._config = config or RetrievalConfig()

    def search(
        self,
        *,
        case_id: str,
        query: str,
        limit: int | None = None,
    ) -> tuple[RetrievalResult, ...]:
        """Return BM25-ranked matches validated against authoritative rows."""

        normalized_case_id = case_id.strip()
        if not normalized_case_id:
            raise RetrievalQueryError("Case scope is required")
        normalized_query = query.strip()
        if not (
            self._config.min_query_chars <= len(normalized_query) <= self._config.max_query_chars
        ):
            raise RetrievalQueryError("Retrieval query length is outside allowed bounds")
        requested_limit = self._config.default_limit if limit is None else limit
        if not isinstance(requested_limit, int) or isinstance(requested_limit, bool):
            raise RetrievalLimitError("Retrieval limit must be an integer")
        if not 1 <= requested_limit <= self._config.max_limit:
            raise RetrievalLimitError("Retrieval limit is outside allowed bounds")

        try:
            if not projection_exists(self._connection):
                raise RetrievalIndexError("Retrieval index is unavailable")
            rows = self._connection.execute(
                f"""
                SELECT source.case_id,
                       segment.source_id,
                       segment.id AS segment_id,
                       segment.sequence,
                       segment.page,
                       segment.text_sha256,
                       segment.text,
                       projection.source_id AS indexed_source_id,
                       projection.segment_id AS indexed_segment_id,
                       projection.text_sha256 AS indexed_text_sha256,
                       projection.text AS indexed_text,
                       bm25({FTS_TABLE_NAME}) AS score
                FROM {FTS_TABLE_NAME} AS projection
                JOIN source_segment AS segment
                  ON segment.rowid = projection.rowid
                 AND segment.id = projection.segment_id
                 AND segment.source_id = projection.source_id
                JOIN source ON source.id = segment.source_id
                WHERE {FTS_TABLE_NAME} MATCH ?
                  AND source.case_id = ?
                ORDER BY score ASC,
                         segment.source_id ASC,
                         segment.sequence ASC,
                         segment.id ASC
                LIMIT ?
                """,
                (normalized_query, normalized_case_id, requested_limit),
            ).fetchall()
        except RetrievalIndexError:
            raise
        except sqlite3.OperationalError as error:
            raise RetrievalQueryError("Retrieval query is invalid") from error
        except sqlite3.Error as error:
            raise RetrievalIndexError("Retrieval index query failed") from error

        return tuple(_validated_result(row, expected_case_id=normalized_case_id) for row in rows)

    def rebuild_index(self) -> RetrievalIndexStatus:
        """Explicitly rebuild the complete projection in one transaction."""

        return rebuild_projection(self._connection)


def _validated_result(row: sqlite3.Row, *, expected_case_id: str) -> RetrievalResult:
    text = str(row["text"])
    text_sha256 = str(row["text_sha256"])
    indexed_text = str(row["indexed_text"])
    indexed_text_sha256 = str(row["indexed_text_sha256"])
    if (
        str(row["case_id"]) != expected_case_id
        or str(row["source_id"]) != str(row["indexed_source_id"])
        or str(row["segment_id"]) != str(row["indexed_segment_id"])
        or text_sha256 != indexed_text_sha256
        or text != indexed_text
        or hashlib.sha256(text.encode()).hexdigest() != text_sha256
    ):
        raise RetrievalIntegrityError("Retrieval citation validation failed")
    return RetrievalResult(
        case_id=expected_case_id,
        source_id=str(row["source_id"]),
        segment_id=str(row["segment_id"]),
        sequence=int(row["sequence"]),
        page=int(row["page"]) if row["page"] is not None else None,
        text_sha256=text_sha256,
        text=text,
        score=float(row["score"]),
    )
