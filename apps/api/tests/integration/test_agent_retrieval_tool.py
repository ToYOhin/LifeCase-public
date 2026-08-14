"""Integration coverage for the controlled M3-04 read-only retrieval tool."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from lifecase.evidence.context import (
    EvidenceContextBudget,
    EvidenceContextRequest,
    EvidencePolicy,
    EvidenceTrustLabel,
)
from lifecase.retrieval import RetrievalService
from lifecase.storage import apply_migrations, connect_database
from lifecase.tools import (
    EVIDENCE_CONTEXT_TOOL_NAME,
    EvidenceContextReadTool,
    EvidenceContextToolRequest,
    ReadOnlyToolBudget,
    ToolCaseNotFoundError,
    ToolErrorCode,
    ToolIntegrityError,
    ToolNotAllowedError,
    ToolRisk,
    ToolTimeoutError,
    ToolUnavailableError,
)

_NOW = "2026-08-02T00:00:00.000000Z"
_ALLOW_IMPORTED = EvidencePolicy(frozenset({EvidenceTrustLabel.UNTRUSTED}))


def test_tool_contract_is_frozen_named_and_strictly_budgeted() -> None:
    request = _tool_request(case_id="case_contract", query="contract marker")

    assert request.tool_name == EVIDENCE_CONTEXT_TOOL_NAME
    assert request.budget == ReadOnlyToolBudget(max_calls=1, timeout_ms=2_000)
    with pytest.raises(AttributeError):
        request.tool_name = "changed"  # type: ignore[misc]
    for values in (
        {"max_calls": 0, "timeout_ms": 10},
        {"max_calls": 2, "timeout_ms": 10},
        {"max_calls": True, "timeout_ms": 10},
        {"max_calls": 1, "timeout_ms": 0},
        {"max_calls": 1, "timeout_ms": 5_001},
    ):
        with pytest.raises(ValueError, match="budget"):
            ReadOnlyToolBudget(**values)
    with pytest.raises(ValueError, match="name"):
        EvidenceContextToolRequest(
            tool_name="source.search",
            context_request=request.context_request,
            budget=request.budget,
        )


def test_allowed_tool_is_case_scoped_authoritative_deterministic_and_read_only(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_one")
        _add_case(connection, "case_other")
        _add_source(connection, "case_one", "source_one")
        _add_source(connection, "case_other", "source_other")
        first_hash = _add_segment(
            connection,
            source_id="source_one",
            segment_id="segment_before",
            sequence=0,
            page=1,
            text="Adjacent synthetic warranty preface.",
        )
        match_hash = _add_segment(
            connection,
            source_id="source_one",
            segment_id="segment_match",
            sequence=1,
            page=2,
            text="Authoritative warranty alpha-7391 evidence.",
        )
        _add_segment(
            connection,
            source_id="source_other",
            segment_id="segment_cross_case",
            sequence=0,
            page=9,
            text="Other Case warranty alpha-7391 evidence.",
        )
        RetrievalService(connection).rebuild_index()
        before = _state_snapshot(connection)
        tool = EvidenceContextReadTool(
            connection,
            allowed_tools=(EVIDENCE_CONTEXT_TOOL_NAME,),
        )
        request = _tool_request(case_id="case_one", query='"alpha-7391"')
        busy_timeout_before = connection.execute("PRAGMA busy_timeout").fetchone()[0]

        first = tool.execute(request)
        second = tool.execute(request)

        assert first == second
        assert first.risk is ToolRisk.READ_ONLY
        assert first.call_count == 1
        assert first.context.case_id == "case_one"
        assert [item.citation.segment_id for item in first.context.items] == [
            "segment_before",
            "segment_match",
        ]
        assert [item.citation.text_sha256 for item in first.context.items] == [
            first_hash,
            match_hash,
        ]
        assert all(item.citation.case_id == "case_one" for item in first.context.items)
        assert "segment_cross_case" not in str(first.to_stage_payload())
        assert _state_snapshot(connection) == before
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == busy_timeout_before
    finally:
        connection.close()


def test_tool_allowlist_missing_case_empty_result_and_stale_projection_fail_closed(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_scope")
        _add_source(connection, "case_scope", "source_scope")
        _add_segment(
            connection,
            source_id="source_scope",
            segment_id="segment_scope",
            sequence=0,
            text="Authoritative integrity marker.",
        )
        RetrievalService(connection).rebuild_index()
        request = _tool_request(case_id="case_scope", query="missing marker")

        with pytest.raises(ToolNotAllowedError) as denied:
            EvidenceContextReadTool(connection, allowed_tools=()).execute(request)
        assert denied.value.code is ToolErrorCode.NOT_ALLOWED

        result = EvidenceContextReadTool(
            connection, allowed_tools=(EVIDENCE_CONTEXT_TOOL_NAME,)
        ).execute(request)
        assert result.context.matches == ()
        assert result.context.items == ()

        with pytest.raises(ToolCaseNotFoundError) as missing:
            EvidenceContextReadTool(
                connection, allowed_tools=(EVIDENCE_CONTEXT_TOOL_NAME,)
            ).execute(_tool_request(case_id="absent_case", query="missing marker"))
        assert missing.value.code is ToolErrorCode.CASE_NOT_FOUND

        connection.execute(
            "UPDATE source_segment_fts SET text_sha256 = ? WHERE segment_id = ?",
            ("f" * 64, "segment_scope"),
        )
        with pytest.raises(ToolIntegrityError) as stale:
            EvidenceContextReadTool(
                connection, allowed_tools=(EVIDENCE_CONTEXT_TOOL_NAME,)
            ).execute(_tool_request(case_id="case_scope", query="integrity marker"))
        assert stale.value.code is ToolErrorCode.INTEGRITY
        assert "Authoritative integrity marker" not in str(stale.value)
        assert str(tmp_path) not in str(stale.value)
    finally:
        connection.close()


def test_tool_timeout_and_closed_database_fail_with_safe_typed_errors(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _add_case(connection, "case_timeout")
    _add_source(connection, "case_timeout", "source_timeout")
    _add_segment(
        connection,
        source_id="source_timeout",
        segment_id="segment_timeout",
        sequence=0,
        text="Synthetic timeout marker.",
    )
    RetrievalService(connection).rebuild_index()
    ticks = iter((0.0, 1.0, 1.0, 1.0, 1.0))
    tool = EvidenceContextReadTool(
        connection,
        allowed_tools=(EVIDENCE_CONTEXT_TOOL_NAME,),
        clock=lambda: next(ticks, 1.0),
    )
    with pytest.raises(ToolTimeoutError) as timed_out:
        tool.execute(
            _tool_request(
                case_id="case_timeout",
                query="timeout marker",
                tool_budget=ReadOnlyToolBudget(max_calls=1, timeout_ms=1),
            )
        )
    assert timed_out.value.code is ToolErrorCode.TIMEOUT
    assert timed_out.value.retryable is True

    connection.close()
    with pytest.raises(ToolUnavailableError) as unavailable:
        EvidenceContextReadTool(
            connection,
            allowed_tools=(EVIDENCE_CONTEXT_TOOL_NAME,),
        ).execute(_tool_request(case_id="case_timeout", query="timeout marker"))
    assert unavailable.value.code is ToolErrorCode.UNAVAILABLE
    assert "timeout marker" not in str(unavailable.value)
    assert str(tmp_path) not in str(unavailable.value)


def _database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    return connection


def _tool_request(
    *,
    case_id: str,
    query: str,
    tool_budget: ReadOnlyToolBudget | None = None,
) -> EvidenceContextToolRequest:
    return EvidenceContextToolRequest(
        tool_name=EVIDENCE_CONTEXT_TOOL_NAME,
        context_request=EvidenceContextRequest(
            case_id=case_id,
            query=query,
            retrieval_limit=10,
            adjacent_before=1,
            adjacent_after=1,
            budget=EvidenceContextBudget(
                max_text_bytes=20_000,
                max_item_text_bytes=10_000,
            ),
            policy=_ALLOW_IMPORTED,
        ),
        budget=tool_budget or ReadOnlyToolBudget(),
    )


def _add_case(connection: sqlite3.Connection, case_id: str) -> None:
    connection.execute(
        """
        INSERT INTO case_record (
            id, type, template_version, title, goal, status,
            priority, version, created_at, updated_at
        ) VALUES (?, 'warranty', 1, 'Synthetic tool Case', 'Test read-only tool',
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


def _state_snapshot(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    tables = (
        "case_record",
        "claim",
        "claim_source",
        "source",
        "source_segment",
        "source_segment_fts",
        "agent_run",
        "agent_stage_run",
    )
    return tuple(
        tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()) for table in tables
    )
