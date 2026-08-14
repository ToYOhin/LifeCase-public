"""M4-01 deterministic Case list and Today projection coverage."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifecase.api.cases as cases_api
from lifecase.actions import (
    ActionKind,
    ActionPayload,
    ActionRiskLevel,
    ActionService,
    CreateActionCommand,
)
from lifecase.cases import (
    CaseActivityKind,
    CaseDeadlineRisk,
    CasePersistenceError,
    CaseService,
)
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.storage import apply_migrations, connect_database

_T0 = "2026-08-02T00:00:00.000000Z"
_T1 = "2026-08-02T01:00:00.000000Z"
_T2 = "2026-08-02T02:00:00.000000Z"
_T3 = "2026-08-02T03:00:00.000000Z"


def test_empty_database_returns_an_empty_read_only_projection(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        before = _snapshot(connection)
        assert CaseService(connection).list_workbench_cases() == ()
        assert _snapshot(connection) == before
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_list_uses_authoritative_gaps_activity_and_deterministic_order(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_a", status="intake", priority=1, updated_at=_T1)
        _add_case(connection, "case_b", status="blocked", priority=2, updated_at=_T0)
        _add_case(connection, "case_closed", status="resolved", priority=99, updated_at=_T3)
        _add_source(connection, "case_a", "source_a", imported_at=_T2)
        _add_claim(connection, "case_a", "claim_confirmed", "merchant_or_manufacturer", "confirmed")
        _add_claim(connection, "case_a", "claim_candidate", "product_name", "candidate")
        _add_claim(connection, "case_a", "claim_inferred", "order_number", "inferred")
        _add_claim(
            connection,
            "case_a",
            "claim_contradictory",
            "desired_outcome",
            "confirmed",
            contradiction=True,
        )
        _add_claim(connection, "case_a", "claim_evidence", "purchase_proof", "corrected")
        before = _snapshot(connection)

        items = CaseService(connection).list_workbench_cases()

        assert [item.case.id for item in items] == ["case_b", "case_a", "case_closed"]
        case_a = items[1]
        assert case_a.is_today is True
        assert case_a.next_action is None
        assert case_a.deadline_risk is CaseDeadlineRisk.NOT_CALCULATED
        assert case_a.last_activity_at == _T2
        assert case_a.last_activity_kind is CaseActivityKind.SOURCE_IMPORTED
        assert case_a.missing_evidence.required_claims == (
            "product_name",
            "purchase_date",
            "order_number",
            "desired_outcome",
        )
        assert case_a.missing_evidence.evidence_requirements == (
            "defect_description",
            "defect_media",
        )
        assert case_a.missing_evidence.total == 6
        assert items[-1].is_today is False
        assert _snapshot(connection) == before
    finally:
        connection.close()


def test_projection_survives_restart_and_activity_ties_are_stable(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection = _database(tmp_path)
    _add_case(connection, "case_tie_b", status="ready", priority=0, updated_at=_T1)
    _add_case(connection, "case_tie_a", status="ready", priority=0, updated_at=_T1)
    before = CaseService(connection).list_workbench_cases()
    connection.close()

    restarted = connect_database(database_path)
    try:
        after = CaseService(restarted).list_workbench_cases()
        assert before == after
        assert [item.case.id for item in after] == ["case_tie_a", "case_tie_b"]
    finally:
        restarted.close()


def test_closed_database_fails_without_sql_or_path_details(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    service = CaseService(connection)
    connection.close()

    with pytest.raises(CasePersistenceError) as captured:
        service.list_workbench_cases()

    assert "SELECT" not in str(captured.value)
    assert str(tmp_path) not in str(captured.value)


def test_case_list_api_exposes_summary_without_source_or_claim_text(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        empty = client.get("/api/v1/cases")
        created = client.post(
            "/api/v1/cases",
            json={
                "title": "Synthetic docket",
                "goal": "Test the local workbench",
                "template_id": "warranty",
            },
        )
        listed = client.get("/api/v1/cases")

    assert empty.status_code == 200
    assert empty.json() == []
    assert created.status_code == 201
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    item = listed.json()[0]
    assert item["id"] == created.json()["id"]
    assert item["is_today"] is True
    assert item["next_action"] is None
    assert item["deadline_risk"] == "not_calculated"
    assert item["last_activity_kind"] == "case_updated"
    assert item["missing_evidence"]["total"] == 8
    assert "sources" not in item
    assert "claims" not in item


def test_case_list_projects_authoritative_next_action_and_action_activity(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_action", status="intake", priority=0, updated_at=_T1)
        action = ActionService(connection).create_action(
            CreateActionCommand(
                case_id="case_action",
                kind=ActionKind.COLLECT_EVIDENCE,
                risk_level=ActionRiskLevel.B,
                payload=ActionPayload.from_input(
                    ActionKind.COLLECT_EVIDENCE,
                    {"requirement_key": "purchase_date", "requirement_type": "claim"},
                ),
                requirements=(),
                dependencies=(),
                deadline=None,
                actor="local_user",
                reason="Synthetic next Action",
            )
        )

        item = CaseService(connection).list_workbench_cases()[0]

        assert item.next_action is not None
        assert (
            item.next_action.id,
            item.next_action.kind,
            item.next_action.status,
            item.next_action.risk_level,
            item.next_action.blocker_count,
            item.next_action.version,
        ) == (action.id, "collect_evidence", "ready", "B", 0, 1)
        assert item.last_activity_kind is CaseActivityKind.ACTION_UPDATED
        assert item.last_activity_at == action.updated_at
    finally:
        connection.close()


def test_case_list_api_redacts_storage_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)

    def fail_list(*_args: object, **_kwargs: object) -> None:
        raise CasePersistenceError("SELECT secret FROM private_path")

    monkeypatch.setattr(cases_api.CaseService, "list_workbench_cases", fail_list)
    with TestClient(create_app(config)) as client:
        response = client.get("/api/v1/cases")

    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "case_storage_failed",
    )
    assert response.json()["error"]["retryable"] is True
    assert "SELECT secret" not in response.text
    assert str(config.database_path) not in response.text


def _database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    return connection


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _add_case(
    connection: sqlite3.Connection,
    case_id: str,
    *,
    status: str,
    priority: int,
    updated_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO case_record (
            id, type, template_id, template_version, title, goal, status,
            priority, version, created_at, updated_at, closed_at
        ) VALUES (?, 'warranty', 'warranty_claim', 1, ?, 'Synthetic goal', ?,
                  ?, 1, ?, ?, ?)
        """,
        (
            case_id,
            f"Synthetic {case_id}",
            status,
            priority,
            _T0,
            updated_at,
            updated_at if status in {"resolved", "abandoned", "archived"} else None,
        ),
    )


def _add_source(
    connection: sqlite3.Connection,
    case_id: str,
    source_id: str,
    *,
    imported_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO source (
            id, case_id, original_name, media_type, sha256,
            size_bytes, object_path, imported_at
        ) VALUES (?, ?, 'synthetic.txt', 'text/plain', ?, 0, ?, ?)
        """,
        (source_id, case_id, "0" * 64, f"objects/00/00/{'0' * 64}", imported_at),
    )


def _add_claim(
    connection: sqlite3.Connection,
    case_id: str,
    claim_id: str,
    field: str,
    status: str,
    *,
    contradiction: bool = False,
) -> None:
    value_json = json.dumps(f"synthetic-{field}")
    value_hash = hashlib.sha256(value_json.encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO claim (
            id, case_id, field, value_json, value_sha256, comparison_sha256,
            status, confidence, created_by, contradiction, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, 'user', ?, 1, ?, ?)
        """,
        (
            claim_id,
            case_id,
            field,
            value_json,
            value_hash,
            value_hash,
            status,
            int(contradiction),
            _T0,
            _T1,
        ),
    )


def _snapshot(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    tables = (
        "case_record",
        "case_transition",
        "source",
        "source_segment",
        "source_segment_fts",
        "source_extraction",
        "claim",
        "claim_source",
        "claim_event",
        "claim_confirmation",
        "agent_run",
        "agent_stage_run",
    )
    return tuple(
        tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall())
        for table in tables
    )
