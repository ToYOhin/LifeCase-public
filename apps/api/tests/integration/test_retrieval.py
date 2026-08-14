"""Integration coverage for case-scoped local FTS5 retrieval."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from lifecase.evidence.extraction import ExtractionConfig, ExtractionService
from lifecase.evidence.extraction.errors import SegmentPersistenceError
from lifecase.evidence.extraction.models import ParsedDocument, RawTextPart
from lifecase.retrieval import (
    RetrievalIndexError,
    RetrievalIntegrityError,
    RetrievalLimitError,
    RetrievalQueryError,
    RetrievalService,
)
from lifecase.storage import apply_migrations, connect_database

_NOW = "2026-08-01T00:00:00.000000Z"


def _database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    return connection


def _add_case(connection: sqlite3.Connection, case_id: str) -> None:
    connection.execute(
        """
        INSERT INTO case_record (
            id, type, template_version, title, goal, status,
            priority, version, created_at, updated_at
        ) VALUES (?, 'warranty', 1, 'Synthetic case', 'Test retrieval',
                  'intake', 0, 1, ?, ?)
        """,
        (case_id, _NOW, _NOW),
    )


def _add_source(connection: sqlite3.Connection, case_id: str, source_id: str) -> None:
    connection.execute(
        """
        INSERT INTO source (
            id, case_id, original_name, media_type, sha256,
            size_bytes, object_path, imported_at
        ) VALUES (?, ?, 'synthetic.txt', 'text/plain', ?, 0, ?, ?)
        """,
        (source_id, case_id, "0" * 64, f"objects/00/00/{'0' * 64}", _NOW),
    )


def _add_segment(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    segment_id: str,
    sequence: int,
    text: str,
    page: int | None = None,
) -> str:
    text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO source_segment (
            id, source_id, sequence, page, bbox_json, text,
            extraction_method, confidence, text_sha256, extractor_version
        ) VALUES (?, ?, ?, ?, NULL, ?, 'direct', 1.0, ?, 'synthetic.v1')
        """,
        (segment_id, source_id, sequence, page, text, text_sha256),
    )
    return text_sha256


def test_search_is_case_scoped_and_returns_authoritative_citations(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_a")
        _add_case(connection, "case_b")
        _add_source(connection, "case_a", "source_a")
        _add_source(connection, "case_b", "source_b")
        expected_hash = _add_segment(
            connection,
            source_id="source_a",
            segment_id="segment_a",
            sequence=3,
            page=2,
            text="Synthetic warranty reference alpha-7391.",
        )
        _add_segment(
            connection,
            source_id="source_b",
            segment_id="segment_b",
            sequence=0,
            text="Synthetic warranty reference alpha-7391 from another case.",
        )
        service = RetrievalService(connection)
        service.rebuild_index()

        results = service.search(case_id="case_a", query='"alpha-7391"')

        assert len(results) == 1
        result = results[0]
        assert (
            result.case_id,
            result.source_id,
            result.segment_id,
            result.sequence,
            result.page,
            result.text_sha256,
            result.text,
        ) == (
            "case_a",
            "source_a",
            "segment_a",
            3,
            2,
            expected_hash,
            "Synthetic warranty reference alpha-7391.",
        )
    finally:
        connection.close()


def test_bm25_ranking_and_tie_order_are_stable(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_rank")
        _add_source(connection, "case_rank", "source_a")
        _add_source(connection, "case_rank", "source_b")
        _add_segment(
            connection,
            source_id="source_a",
            segment_id="segment_frequent",
            sequence=0,
            text="warranty warranty warranty warranty claim",
        )
        _add_segment(
            connection,
            source_id="source_a",
            segment_id="segment_tie_a",
            sequence=1,
            text="stable synthetic tie marker",
        )
        _add_segment(
            connection,
            source_id="source_b",
            segment_id="segment_single",
            sequence=0,
            text="warranty claim",
        )
        _add_segment(
            connection,
            source_id="source_b",
            segment_id="segment_tie_b",
            sequence=1,
            text="stable synthetic tie marker",
        )
        service = RetrievalService(connection)
        service.rebuild_index()

        ranked = service.search(case_id="case_rank", query="warranty")
        first_ties = service.search(case_id="case_rank", query='"synthetic tie"')
        second_ties = service.search(case_id="case_rank", query='"synthetic tie"')

        assert [result.segment_id for result in ranked[:2]] == [
            "segment_frequent",
            "segment_single",
        ]
        assert ranked[0].score < ranked[1].score
        assert [result.segment_id for result in first_ties] == [
            "segment_tie_a",
            "segment_tie_b",
        ]
        assert first_ties == second_ties
        assert first_ties[0].score == first_ties[1].score
    finally:
        connection.close()


def test_drop_rebuild_and_repeated_rebuild_preserve_results(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_rebuild")
        _add_source(connection, "case_rebuild", "source_rebuild")
        _add_segment(
            connection,
            source_id="source_rebuild",
            segment_id="segment_rebuild",
            sequence=0,
            text="deterministic rebuild marker",
        )
        service = RetrievalService(connection)
        first_status = service.rebuild_index()
        before = service.search(case_id="case_rebuild", query="rebuild marker")

        connection.execute("DROP TABLE source_segment_fts")
        with pytest.raises(RetrievalIndexError):
            service.search(case_id="case_rebuild", query="rebuild marker")

        second_status = service.rebuild_index()
        after = service.search(case_id="case_rebuild", query="rebuild marker")
        third_status = service.rebuild_index()
        repeated = service.search(case_id="case_rebuild", query="rebuild marker")

        assert first_status == second_status == third_status
        assert first_status.authoritative_segments == first_status.indexed_segments == 1
        assert before == after == repeated
    finally:
        connection.close()


def test_successful_replacement_syncs_index_and_failure_rolls_back(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    data_root = tmp_path / "data"
    object_bytes = b"immutable synthetic object"
    object_hash = hashlib.sha256(object_bytes).hexdigest()
    relative_path = f"objects/{object_hash[:2]}/{object_hash[2:4]}/{object_hash}"
    object_path = data_root.joinpath(*relative_path.split("/"))
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(object_bytes)
    current_text = ["旧版保修凭证用于合成检索测试甲乙丙丁戊己庚辛"]

    def parser(_path: Path, _media_type: str, _config: ExtractionConfig) -> ParsedDocument:
        return ParsedDocument(
            extractor_version="synthetic.v1",
            parts=(
                RawTextPart(
                    text=current_text[0],
                    page=1,
                    extraction_method="direct",
                    confidence=1.0,
                ),
            ),
        )

    try:
        _add_case(connection, "case_replace")
        connection.execute(
            """
            INSERT INTO source (
                id, case_id, original_name, media_type, sha256,
                size_bytes, object_path, imported_at
            ) VALUES ('source_replace', 'case_replace', 'synthetic.txt',
                      'text/plain', ?, ?, ?, ?)
            """,
            (object_hash, len(object_bytes), relative_path, _NOW),
        )
        extractor = ExtractionService(
            connection,
            ExtractionConfig(data_root=data_root, max_segment_chars=12),
            parser_runner=parser,
        )
        retrieval = RetrievalService(connection)

        extractor.extract_source("source_replace")
        assert retrieval.search(case_id="case_replace", query="旧版保修凭证")

        current_text[0] = "新版退款凭证用于合成检索测试壹贰叁肆伍陆柒捌"
        extractor.extract_source("source_replace")
        assert retrieval.search(case_id="case_replace", query="旧版保修凭证") == ()
        new_results = retrieval.search(case_id="case_replace", query="新版退款凭证")
        rows_before_failure = connection.execute(
            """
            SELECT id, sequence, text, text_sha256
            FROM source_segment WHERE source_id = 'source_replace'
            ORDER BY sequence
            """
        ).fetchall()

        connection.execute(
            """
            CREATE TRIGGER fail_replacement_segment
            BEFORE INSERT ON source_segment
            WHEN NEW.sequence = 1
            BEGIN
                SELECT RAISE(ABORT, 'synthetic persistence failure');
            END
            """
        )
        current_text[0] = "失败写入内容不得替换现有合成检索材料甲乙丙丁戊己"
        with pytest.raises(SegmentPersistenceError):
            extractor.extract_source("source_replace")

        rows_after_failure = connection.execute(
            """
            SELECT id, sequence, text, text_sha256
            FROM source_segment WHERE source_id = 'source_replace'
            ORDER BY sequence
            """
        ).fetchall()
        assert retrieval.search(case_id="case_replace", query="新版退款凭证") == new_results
        assert retrieval.search(case_id="case_replace", query="失败写入内容") == ()
        assert [tuple(row) for row in rows_after_failure] == [
            tuple(row) for row in rows_before_failure
        ]
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_query_and_limit_boundaries_fail_safely(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_bounds")
        _add_source(connection, "case_bounds", "source_bounds")
        _add_segment(
            connection,
            source_id="source_bounds",
            segment_id="segment_warranty",
            sequence=0,
            text="warranty evidence marker",
        )
        _add_segment(
            connection,
            source_id="source_bounds",
            segment_id="segment_refund",
            sequence=1,
            text="refund evidence marker",
        )
        service = RetrievalService(connection)
        service.rebuild_index()

        special_syntax = service.search(case_id="case_bounds", query="warranty OR refund")
        assert {result.segment_id for result in special_syntax} == {
            "segment_warranty",
            "segment_refund",
        }
        for query in ("", "ab", "x" * 257, "NEAR("):
            with pytest.raises(RetrievalQueryError) as captured:
                service.search(case_id="case_bounds", query=query)
            assert "warranty evidence marker" not in str(captured.value)
            assert str(tmp_path) not in str(captured.value)
        for limit in (0, 101, True):
            with pytest.raises(RetrievalLimitError):
                service.search(case_id="case_bounds", query="warranty", limit=limit)
    finally:
        connection.close()


def test_no_match_is_empty_and_stale_projection_fails_closed(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_integrity")
        _add_source(connection, "case_integrity", "source_integrity")
        _add_segment(
            connection,
            source_id="source_integrity",
            segment_id="segment_integrity",
            sequence=0,
            text="authoritative integrity marker",
        )
        service = RetrievalService(connection)
        service.rebuild_index()

        assert service.search(case_id="case_integrity", query="missing marker") == ()
        connection.execute(
            """
            UPDATE source_segment_fts SET text_sha256 = ?
            WHERE segment_id = 'segment_integrity'
            """,
            ("f" * 64,),
        )

        with pytest.raises(RetrievalIntegrityError) as captured:
            service.search(case_id="case_integrity", query="integrity marker")
        assert "authoritative integrity marker" not in str(captured.value)
        assert str(tmp_path) not in str(captured.value)
    finally:
        connection.close()
