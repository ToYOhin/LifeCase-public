"""Integration coverage for deterministic M2-04 Evidence Context construction."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from lifecase.evidence.context import (
    EvidenceContextBudget,
    EvidenceContextCaseNotFoundError,
    EvidenceContextConfigurationError,
    EvidenceContextIntegrityError,
    EvidenceContextModelError,
    EvidenceContextOrigin,
    EvidenceContextPersistenceError,
    EvidenceContextRequest,
    EvidenceContextRequestError,
    EvidenceContextService,
    EvidencePolicy,
    EvidenceTrustLabel,
)
from lifecase.retrieval import RetrievalService
from lifecase.storage import apply_migrations, connect_database

_NOW = "2026-08-02T00:00:00.000000Z"
_ALLOW_IMPORTED = EvidencePolicy(frozenset({EvidenceTrustLabel.UNTRUSTED}))
_DEFAULT_BUDGET = EvidenceContextBudget(
    max_text_bytes=20_000,
    max_item_text_bytes=10_000,
)


def test_request_policy_and_budget_are_frozen_and_bounded() -> None:
    request = _request(case_id=" case_one ", query=" overlap marker ")

    assert request.case_id == "case_one"
    assert request.query == "overlap marker"
    with pytest.raises(AttributeError):
        request.case_id = "changed"  # type: ignore[misc]

    invalid_requests = (
        {"case_id": " "},
        {"query": ""},
        {"query": "ab"},
        {"query": "x" * 257},
        {"retrieval_limit": 0},
        {"retrieval_limit": 101},
        {"retrieval_limit": True},
        {"adjacent_before": -1},
        {"adjacent_after": 6},
    )
    for override in invalid_requests:
        with pytest.raises(EvidenceContextModelError):
            _request(**override)

    for values in (
        {"max_text_bytes": 0, "max_item_text_bytes": 1},
        {"max_text_bytes": 100_001, "max_item_text_bytes": 1},
        {"max_text_bytes": 100, "max_item_text_bytes": 0},
        {"max_text_bytes": 40_000, "max_item_text_bytes": 32_001},
        {"max_text_bytes": 10, "max_item_text_bytes": 11},
        {"max_text_bytes": True, "max_item_text_bytes": 1},
    ):
        with pytest.raises(EvidenceContextModelError):
            EvidenceContextBudget(**values)

    with pytest.raises(EvidenceContextModelError):
        EvidencePolicy(frozenset({"untrusted"}))  # type: ignore[arg-type]


def test_build_is_case_scoped_deterministic_and_preserves_ranked_matches(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_a")
        _add_case(connection, "case_b")
        _add_source(connection, "case_a", "source_a")
        _add_source(connection, "case_a", "source_b")
        _add_source(connection, "case_b", "source_other_case")
        _add_segment(connection, "source_a", "segment_a0", 0, "before A")
        hash_a = _add_segment(
            connection,
            "source_a",
            "segment_a1",
            1,
            "stable synthetic tie marker",
            page=2,
        )
        _add_segment(connection, "source_a", "segment_a2", 2, "after A")
        hash_b = _add_segment(
            connection,
            "source_b",
            "segment_b0",
            0,
            "stable synthetic tie marker",
            page=4,
        )
        _add_segment(
            connection,
            "source_other_case",
            "segment_other",
            0,
            "stable synthetic tie marker from another Case",
        )
        RetrievalService(connection).rebuild_index()
        service = EvidenceContextService(connection)
        request = _request(case_id="case_a", query='"synthetic tie"')

        first = service.build(request)
        second = service.build(request)

        assert first == second
        assert [match.citation.segment_id for match in first.matches] == [
            "segment_a1",
            "segment_b0",
        ]
        assert [match.rank for match in first.matches] == [1, 2]
        assert [item.citation.segment_id for item in first.items] == [
            "segment_a0",
            "segment_a1",
            "segment_a2",
            "segment_b0",
        ]
        assert all(item.citation.case_id == "case_a" for item in first.items)
        assert "segment_other" not in {item.citation.segment_id for item in first.items}
        citations = {item.citation.segment_id: item.citation for item in first.items}
        assert (
            citations["segment_a1"].source_id,
            citations["segment_a1"].sequence,
            citations["segment_a1"].page,
            citations["segment_a1"].text_sha256,
        ) == ("source_a", 1, 2, hash_a)
        assert (
            citations["segment_b0"].source_id,
            citations["segment_b0"].sequence,
            citations["segment_b0"].page,
            citations["segment_b0"].text_sha256,
        ) == ("source_b", 0, 4, hash_b)
    finally:
        connection.close()


def test_overlapping_windows_merge_without_duplicates_and_keep_stable_attribution(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_overlap")
        _add_source(connection, "case_overlap", "source_overlap")
        texts = (
            "synthetic window zero",
            "overlap marker first",
            "synthetic shared center",
            "overlap marker second",
            "synthetic window four",
        )
        for sequence, text in enumerate(texts):
            _add_segment(
                connection,
                "source_overlap",
                f"segment_{sequence}",
                sequence,
                text,
            )
        RetrievalService(connection).rebuild_index()
        service = EvidenceContextService(connection)

        first = service.build(_request(case_id="case_overlap", query='"overlap marker"'))
        second = service.build(_request(case_id="case_overlap", query='"overlap marker"'))

        assert first == second
        assert [match.citation.sequence for match in first.matches] == [1, 3]
        assert [item.citation.sequence for item in first.items] == [0, 1, 2, 3, 4]
        assert len({item.citation.segment_id for item in first.items}) == 5
        assert [item.origin for item in first.items] == [
            EvidenceContextOrigin.ADJACENT,
            EvidenceContextOrigin.MATCH,
            EvidenceContextOrigin.ADJACENT,
            EvidenceContextOrigin.MATCH,
            EvidenceContextOrigin.ADJACENT,
        ]
        assert first.items[2].match_ranks == (1, 2)
    finally:
        connection.close()


def test_utf8_budget_is_exact_and_never_partially_includes_a_segment(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_budget")
        _add_source(connection, "case_budget", "source_budget")
        _add_segment(connection, "source_budget", "segment_0", 0, "1234567890")
        _add_segment(connection, "source_budget", "segment_1", 1, "中文marker")
        _add_segment(connection, "source_budget", "segment_2", 2, "abcde")
        RetrievalService(connection).rebuild_index()
        service = EvidenceContextService(connection)
        budget = EvidenceContextBudget(max_text_bytes=22, max_item_text_bytes=12)

        result = service.build(_request(case_id="case_budget", query="marker", budget=budget))

        assert [(item.citation.segment_id, item.text_bytes) for item in result.items] == [
            ("segment_0", 10),
            ("segment_1", 12),
        ]
        assert result.used_text_bytes == 22
        assert result.used_text_bytes <= result.budget.max_text_bytes
        assert result.truncated is True
        assert all(
            item.text == _authoritative_text(connection, item.citation.segment_id)
            for item in result.items
        )

        item_limited = service.build(
            _request(
                case_id="case_budget",
                query="marker",
                budget=EvidenceContextBudget(max_text_bytes=20, max_item_text_bytes=9),
            )
        )
        assert [item.citation.segment_id for item in item_limited.items] == ["segment_2"]
        assert item_limited.used_text_bytes == 5
        assert item_limited.truncated is True
    finally:
        connection.close()


def test_policy_explicitly_allows_or_rejects_authoritative_trust_labels(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_policy")
        _add_source(connection, "case_policy", "source_policy")
        _add_segment(
            connection,
            "source_policy",
            "segment_policy",
            0,
            "synthetic policy marker",
        )
        RetrievalService(connection).rebuild_index()
        service = EvidenceContextService(connection)

        allowed = service.build(_request(case_id="case_policy", query="policy marker"))
        denied = service.build(
            _request(
                case_id="case_policy",
                query="policy marker",
                policy=EvidencePolicy(frozenset()),
            )
        )

        assert [item.citation.segment_id for item in allowed.items] == ["segment_policy"]
        assert denied.matches == ()
        assert denied.items == ()
        assert denied.used_text_bytes == 0
        assert denied.truncated is False
    finally:
        connection.close()


def test_missing_case_no_match_and_invalid_fts_query_are_safe(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_empty")
        _add_source(connection, "case_empty", "source_empty")
        _add_segment(
            connection,
            "source_empty",
            "segment_empty",
            0,
            "synthetic available evidence",
        )
        RetrievalService(connection).rebuild_index()
        service = EvidenceContextService(connection)

        empty = service.build(_request(case_id="case_empty", query="missing marker"))
        assert empty.matches == ()
        assert empty.items == ()
        assert empty.used_text_bytes == 0

        with pytest.raises(EvidenceContextCaseNotFoundError):
            service.build(_request(case_id="case_missing", query="available"))
        with pytest.raises(EvidenceContextRequestError) as captured:
            service.build(_request(case_id="case_empty", query="NEAR("))
        assert "synthetic available evidence" not in str(captured.value)
        assert str(tmp_path) not in str(captured.value)
    finally:
        connection.close()


def test_missing_fts_projection_is_a_typed_configuration_failure(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_config")
        connection.execute("DROP TABLE source_segment_fts")

        with pytest.raises(EvidenceContextConfigurationError) as captured:
            EvidenceContextService(connection).build(
                _request(case_id="case_config", query="configuration")
            )

        assert str(tmp_path) not in str(captured.value)
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_stale_cross_case_tampered_and_noncontinuous_authority_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_integrity")
        _add_case(connection, "case_other")
        _add_source(connection, "case_integrity", "source_integrity")
        _add_source(connection, "case_other", "source_other")
        _add_segment(
            connection,
            "source_integrity",
            "segment_integrity",
            0,
            "synthetic integrity marker",
        )
        _add_segment(
            connection,
            "source_other",
            "segment_other",
            0,
            "synthetic integrity marker",
        )
        RetrievalService(connection).rebuild_index()

        stale_result = RetrievalService(connection).search(
            case_id="case_integrity", query="integrity marker", limit=1
        )[0]
        connection.execute("DELETE FROM source_segment_fts WHERE segment_id = 'segment_integrity'")
        connection.execute("DELETE FROM source_segment WHERE id = 'segment_integrity'")
        original_search = RetrievalService.search
        monkeypatch.setattr(
            RetrievalService,
            "search",
            lambda _service, **_kwargs: (stale_result,),
        )
        with pytest.raises(EvidenceContextIntegrityError):
            EvidenceContextService(connection).build(
                _request(case_id="case_integrity", query="integrity marker")
            )

        _add_segment(
            connection,
            "source_integrity",
            "segment_replacement",
            0,
            "synthetic integrity marker",
        )
        monkeypatch.setattr(RetrievalService, "search", original_search)
        RetrievalService(connection).rebuild_index()
        monkeypatch.setattr(
            RetrievalService,
            "search",
            lambda _service, **_kwargs: (stale_result,),
        )
        with pytest.raises(EvidenceContextIntegrityError):
            EvidenceContextService(connection).build(
                _request(case_id="case_integrity", query="integrity marker")
            )

        cross_case_result = original_search(
            RetrievalService(connection),
            case_id="case_other",
            query="integrity marker",
            limit=1,
        )[0]
        monkeypatch.setattr(
            RetrievalService,
            "search",
            lambda _service, **_kwargs: (cross_case_result,),
        )
        with pytest.raises(EvidenceContextIntegrityError):
            EvidenceContextService(connection).build(
                _request(case_id="case_integrity", query="integrity marker")
            )

        monkeypatch.setattr(RetrievalService, "search", original_search)
        connection.execute(
            "UPDATE source_segment SET text = 'tampered' WHERE id = 'segment_replacement'"
        )
        with pytest.raises(EvidenceContextIntegrityError) as captured:
            EvidenceContextService(connection).build(
                _request(case_id="case_integrity", query="integrity marker")
            )
        assert "tampered" not in str(captured.value)
        assert str(tmp_path) not in str(captured.value)
    finally:
        connection.close()


def test_noncontinuous_adjacent_sequence_fails_closed(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_gap")
        _add_source(connection, "case_gap", "source_gap")
        _add_segment(connection, "source_gap", "segment_0", 0, "synthetic start")
        _add_segment(connection, "source_gap", "segment_1", 1, "synthetic middle")
        _add_segment(connection, "source_gap", "segment_2", 2, "gap marker target")
        RetrievalService(connection).rebuild_index()
        connection.execute("DELETE FROM source_segment_fts WHERE segment_id = 'segment_1'")
        connection.execute("DELETE FROM source_segment WHERE id = 'segment_1'")

        with pytest.raises(EvidenceContextIntegrityError):
            EvidenceContextService(connection).build(
                _request(case_id="case_gap", query="marker target")
            )
    finally:
        connection.close()


def test_build_is_read_only_for_case_claim_source_fts_and_template_state(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_read_only")
        _add_source(connection, "case_read_only", "source_read_only")
        _add_segment(
            connection,
            "source_read_only",
            "segment_read_only",
            0,
            "synthetic read only marker",
        )
        RetrievalService(connection).rebuild_index()
        before = _state_snapshot(connection)

        result = EvidenceContextService(connection).build(
            _request(case_id="case_read_only", query="read only marker")
        )

        assert result.items
        assert _state_snapshot(connection) == before
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_database_failure_is_typed_and_hides_storage_details(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _add_case(connection, "case_closed")
    service = EvidenceContextService(connection)
    connection.close()

    with pytest.raises(EvidenceContextPersistenceError) as captured:
        service.build(_request(case_id="case_closed", query="closed database"))

    assert "closed" not in str(captured.value).casefold()
    assert str(tmp_path) not in str(captured.value)


def _database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    return connection


def _request(
    *,
    case_id: object = "case_one",
    query: object = "synthetic marker",
    retrieval_limit: object = 20,
    adjacent_before: object = 1,
    adjacent_after: object = 1,
    budget: object = _DEFAULT_BUDGET,
    policy: object = _ALLOW_IMPORTED,
) -> EvidenceContextRequest:
    return EvidenceContextRequest(
        case_id=case_id,  # type: ignore[arg-type]
        query=query,  # type: ignore[arg-type]
        retrieval_limit=retrieval_limit,  # type: ignore[arg-type]
        adjacent_before=adjacent_before,  # type: ignore[arg-type]
        adjacent_after=adjacent_after,  # type: ignore[arg-type]
        budget=budget,  # type: ignore[arg-type]
        policy=policy,  # type: ignore[arg-type]
    )


def _add_case(connection: sqlite3.Connection, case_id: str) -> None:
    connection.execute(
        """
        INSERT INTO case_record (
            id, type, template_version, title, goal, status,
            priority, version, created_at, updated_at
        ) VALUES (?, 'warranty', 1, 'Synthetic Evidence Context Case',
                  'Test deterministic evidence assembly', 'intake', 0, 1, ?, ?)
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
    source_id: str,
    segment_id: str,
    sequence: int,
    text: str,
    *,
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


def _authoritative_text(connection: sqlite3.Connection, segment_id: str) -> str:
    row = connection.execute(
        "SELECT text FROM source_segment WHERE id = ?", (segment_id,)
    ).fetchone()
    assert row is not None
    return str(row[0])


def _state_snapshot(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    tables = (
        "case_record",
        "claim",
        "claim_source",
        "source",
        "source_segment",
        "source_segment_fts",
    )
    return tuple(
        tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()) for table in tables
    )
