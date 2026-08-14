"""M5-03 deterministic, local-only ICS export coverage."""

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifecase.api.calendar_export as calendar_api
from lifecase.calendar_export import (
    CalendarExportDeadlineInvalidError,
    CalendarExportDeadlineNotFoundError,
    CalendarExportPersistenceError,
    CalendarExportService,
)
from lifecase.cases import CaseService
from lifecase.config import ApplicationConfig
from lifecase.deadlines import (
    CreateSuggestedDeadlineCommand,
    DeadlineDate,
    DeadlineDateKind,
    DeadlineRecord,
    DeadlineService,
    DeadlineStatusCommand,
)
from lifecase.main import create_app
from lifecase.storage import apply_migrations, connect_database


def test_confirmed_local_date_export_is_deterministic_read_only_and_restart_safe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lifecase.sqlite3"
    connection, case_id = _database(path)
    deadline = _confirmed_deadline(
        connection,
        case_id,
        date_kind=DeadlineDateKind.LOCAL_DATE,
        value="2026-11-01",
        timezone="America/New_York",
    )
    before = _authority_counts(connection)
    service = CalendarExportService(DeadlineService(connection))
    first = service.export(case_id, deadline.id)
    second = service.export(case_id, deadline.id)

    assert first == second
    assert first.sha256 == hashlib.sha256(first.content).hexdigest()
    assert first.filename.startswith("lifecase-deadline-")
    assert first.filename.endswith(".ics")
    assert b"DTSTART;VALUE=DATE:20261101\r\n" in first.content
    assert b"X-LIFECASE-DATE-KIND:local_date\r\n" in first.content
    assert b"X-LIFECASE-TIMEZONE:America/New_York\r\n" in first.content
    assert b"BEGIN:VEVENT\r\n" in first.content
    assert first.content.endswith(b"END:VCALENDAR\r\n")
    assert all(len(line) <= 75 for line in first.content.split(b"\r\n"))
    assert _authority_counts(connection) == before
    connection.close()

    restarted = connect_database(path)
    try:
        after_restart = CalendarExportService(DeadlineService(restarted)).export(
            case_id, deadline.id
        )
        assert after_restart == first
        assert _authority_counts(restarted) == before
    finally:
        restarted.close()


def test_confirmed_utc_instant_preserves_instant_and_timezone_metadata(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    deadline = _confirmed_deadline(
        connection,
        case_id,
        date_kind=DeadlineDateKind.UTC_INSTANT,
        value="2026-11-01T01:30:00-05:00",
        timezone="America/New_York",
    )

    exported = CalendarExportService(DeadlineService(connection)).export(case_id, deadline.id)

    assert b"DTSTART:20261101T063000Z\r\n" in exported.content
    assert b"X-LIFECASE-DATE-KIND:utc_instant\r\n" in exported.content
    assert b"X-LIFECASE-TIMEZONE:America/New_York\r\n" in exported.content
    assert b"20261101T013000" not in exported.content
    connection.close()


def test_export_rejects_unconfirmed_missing_and_cross_case_deadlines(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    other = CaseService(connection).create_case(
        title="Other synthetic Case",
        goal="Prove calendar export Case isolation",
        template_id="warranty",
    )
    proposed = DeadlineService(connection).create_suggested(
        CreateSuggestedDeadlineCommand(
            case_id=case_id,
            due=DeadlineDate(DeadlineDateKind.LOCAL_DATE, "2026-08-10", "UTC"),
            actor="local_user",
            reason="Synthetic proposed preference",
        )
    )
    service = CalendarExportService(DeadlineService(connection))

    with pytest.raises(CalendarExportDeadlineInvalidError):
        service.export(case_id, proposed.id)
    with pytest.raises(CalendarExportDeadlineNotFoundError):
        service.export(case_id, "deadline_missing")
    with pytest.raises(CalendarExportDeadlineNotFoundError):
        service.export(other.id, proposed.id)
    connection.close()


def test_calendar_export_api_download_headers_and_safe_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config.database_path)
    deadline = _confirmed_deadline(
        connection,
        case_id,
        date_kind=DeadlineDateKind.LOCAL_DATE,
        value="2026-08-10",
        timezone="Asia/Shanghai",
    )
    connection.close()

    with TestClient(create_app(config)) as client:
        response = client.get(f"/api/v1/cases/{case_id}/deadlines/{deadline.id}/calendar-export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/calendar; charset=utf-8"
    assert response.headers["content-disposition"].startswith("attachment; filename=")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (
        response.headers["x-lifecase-content-sha256"]
        == hashlib.sha256(response.content).hexdigest()
    )
    assert b"SUMMARY:LifeCase suggested deadline" in response.content

    def fail_export(*_args: object, **_kwargs: object) -> None:
        raise CalendarExportPersistenceError(
            "SELECT secret_body FROM private_table PRIVATE_PATH_MARKER token=synthetic"
        )

    monkeypatch.setattr(calendar_api.CalendarExportService, "export", fail_export)
    with TestClient(create_app(config)) as client:
        failed = client.get(f"/api/v1/cases/{case_id}/deadlines/{deadline.id}/calendar-export")
    assert (failed.status_code, failed.json()["error"]["code"]) == (
        503,
        "calendar_export_unavailable",
    )
    assert failed.json()["error"]["retryable"] is True
    assert all(
        marker not in failed.text
        for marker in ("secret_body", "private_table", "PRIVATE_PATH_MARKER", "token=")
    )


def _database(path: Path) -> tuple[sqlite3.Connection, str]:
    connection = connect_database(path)
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic Calendar Export Case",
        goal="Test deterministic local ICS bytes",
        template_id="warranty",
    )
    return connection, case.id


def _confirmed_deadline(
    connection: sqlite3.Connection,
    case_id: str,
    *,
    date_kind: DeadlineDateKind,
    value: str,
    timezone: str,
) -> DeadlineRecord:
    service = DeadlineService(connection)
    proposed = service.create_suggested(
        CreateSuggestedDeadlineCommand(
            case_id=case_id,
            due=DeadlineDate(date_kind, value, timezone),
            actor="local_user",
            reason="Synthetic local calendar preference",
        )
    )
    return service.confirm(
        DeadlineStatusCommand(
            deadline_id=proposed.id,
            expected_version=proposed.version,
            actor="local_user",
            reason="Synthetic calendar confirmation",
        )
    )


def _authority_counts(connection: sqlite3.Connection) -> tuple[int, ...]:
    return tuple(
        int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in (
            "deadline",
            "deadline_basis_claim",
            "deadline_event",
            "artifact",
        )
    )


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )
