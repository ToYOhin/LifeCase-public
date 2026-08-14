"""M5-01 Deadline authority, rule, time, concurrency, and recovery coverage."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifecase.api.deadlines as deadlines_api
from lifecase.cases import CaseService
from lifecase.claims import (
    ClaimConfirmationBasis,
    ClaimCreatedBy,
    ClaimRecord,
    ClaimService,
    ClaimStatus,
    ClaimValue,
    ConfirmClaimCommand,
    CorrectClaimCommand,
    CreateClaimCommand,
)
from lifecase.config import ApplicationConfig
from lifecase.deadlines import (
    CALENDAR_DAYS_AFTER_BASIS,
    CreateCalculatedDeadlineCommand,
    CreateExplicitDeadlineCommand,
    CreateSuggestedDeadlineCommand,
    DeadlineBasisClaimNotFoundError,
    DeadlineBasisInput,
    DeadlineBasisInvalidError,
    DeadlineBasisStaleError,
    DeadlineCaseNotFoundError,
    DeadlineCommandValidationError,
    DeadlineDate,
    DeadlineDateKind,
    DeadlineKind,
    DeadlinePersistenceError,
    DeadlineRuleInvalidError,
    DeadlineRuleParameters,
    DeadlineService,
    DeadlineStatus,
    DeadlineStatusCommand,
    DeadlineTransitionNotAllowedError,
    DeadlineUncertainty,
    DeadlineVersionConflictError,
)
from lifecase.main import create_app
from lifecase.storage import apply_migrations, connect_database


def test_deadline_types_dates_and_statuses_are_closed_and_unambiguous() -> None:
    assert {item.value for item in DeadlineKind} == {"explicit", "calculated", "suggested"}
    assert {item.value for item in DeadlineStatus} == {
        "proposed",
        "confirmed",
        "passed",
        "completed",
        "dismissed",
    }
    assert (
        DeadlineDate(
            DeadlineDateKind.UTC_INSTANT,
            "2026-08-03T10:15:00+08:00",
            "Asia/Shanghai",
        ).value
        == "2026-08-03T02:15:00.000000Z"
    )
    assert (
        DeadlineDate(DeadlineDateKind.LOCAL_DATE, "2026-08-03", "Asia/Shanghai").value
        == "2026-08-03"
    )
    with pytest.raises(DeadlineCommandValidationError):
        DeadlineDate(DeadlineDateKind.UTC_INSTANT, "2026-08-03T10:15:00", "Asia/Shanghai")
    with pytest.raises(DeadlineCommandValidationError):
        DeadlineDate(DeadlineDateKind.LOCAL_DATE, "2026-08-03T10:15:00Z", "UTC")
    with pytest.raises(DeadlineCommandValidationError):
        DeadlineDate(DeadlineDateKind.LOCAL_DATE, "2026-08-03", "Invalid/Zone")


def test_local_wall_time_rejects_dst_gap_and_requires_fold_for_overlap() -> None:
    with pytest.raises(DeadlineCommandValidationError, match="does not exist"):
        DeadlineDate.utc_from_local(
            local_datetime="2026-03-08T02:30:00.000000",
            timezone="America/New_York",
        )
    with pytest.raises(DeadlineCommandValidationError, match="ambiguous"):
        DeadlineDate.utc_from_local(
            local_datetime="2026-11-01T01:30:00.000000",
            timezone="America/New_York",
        )
    first = DeadlineDate.utc_from_local(
        local_datetime="2026-11-01T01:30:00.000000",
        timezone="America/New_York",
        fold=0,
    )
    second = DeadlineDate.utc_from_local(
        local_datetime="2026-11-01T01:30:00.000000",
        timezone="America/New_York",
        fold=1,
    )
    assert first.value == "2026-11-01T05:30:00.000000Z"
    assert second.value == "2026-11-01T06:30:00.000000Z"


def test_three_kinds_persist_with_distinct_authority_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "lifecase.sqlite3"
    connection, case_id = _database(path)
    explicit_claim = _confirmed_claim(connection, case_id, "policy_deadline", "2026-09-01")
    basis_claim = _corrected_claim(connection, case_id, "purchase_date", "2026-08-01")
    service = DeadlineService(connection)
    explicit = service.create_explicit(
        CreateExplicitDeadlineCommand(
            case_id=case_id,
            basis=_basis(explicit_claim),
            timezone="Asia/Shanghai",
            actor="local_user",
            reason="Synthetic confirmed policy date",
        )
    )
    calculated = service.create_calculated(
        CreateCalculatedDeadlineCommand(
            case_id=case_id,
            bases=(_basis(basis_claim),),
            timezone="Asia/Shanghai",
            rule_id=CALENDAR_DAYS_AFTER_BASIS,
            rule_version=1,
            rule_parameters=DeadlineRuleParameters.from_input({"days": 30}),
            actor="local_rule",
            reason="Synthetic deterministic calculation",
        )
    )
    suggested = service.create_suggested(
        CreateSuggestedDeadlineCommand(
            case_id=case_id,
            due=DeadlineDate(DeadlineDateKind.LOCAL_DATE, "2026-08-10", "Asia/Shanghai"),
            actor="local_user",
            reason="Personal follow-up preference",
        )
    )

    assert (explicit.kind, explicit.due.value, explicit.uncertainty) == (
        DeadlineKind.EXPLICIT,
        "2026-09-01",
        DeadlineUncertainty.EVIDENCE_BACKED,
    )
    assert (calculated.kind, calculated.due.value, calculated.rule_id) == (
        DeadlineKind.CALCULATED,
        "2026-08-31",
        CALENDAR_DAYS_AFTER_BASIS,
    )
    assert calculated.rule_version == 1 and calculated.bases[0].claim_id == basis_claim.id
    assert (suggested.kind, suggested.bases, suggested.rule_id, suggested.uncertainty) == (
        DeadlineKind.SUGGESTED,
        (),
        None,
        DeadlineUncertainty.PERSONAL_PREFERENCE,
    )
    expected = service.list_case_deadlines(case_id)
    connection.close()

    restarted = connect_database(path)
    try:
        assert DeadlineService(restarted).list_case_deadlines(case_id) == expected
    finally:
        restarted.close()


def test_explicit_basis_fails_closed_for_missing_cross_case_candidate_and_stale(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    other = CaseService(connection).create_case(
        title="Other synthetic Case", goal="Prove Deadline isolation", template_id="warranty"
    )
    candidate = _candidate_claim(connection, case_id, "policy_deadline", "2026-09-01")
    authoritative = _confirmed_claim(connection, case_id, "contract_deadline", "2026-09-02")
    cross = _confirmed_claim(connection, other.id, "policy_deadline", "2026-09-01")
    service = DeadlineService(connection)
    try:
        with pytest.raises(DeadlineBasisClaimNotFoundError):
            service.create_explicit(_explicit(case_id, _missing_basis()))
        with pytest.raises(DeadlineBasisInvalidError):
            service.create_explicit(_explicit(case_id, _basis(candidate)))
        with pytest.raises(DeadlineBasisInvalidError):
            service.create_explicit(_explicit(case_id, _basis(cross)))
        connection.execute("UPDATE claim SET contradiction = 1 WHERE id = ?", (authoritative.id,))
        with pytest.raises(DeadlineBasisInvalidError):
            service.create_explicit(_explicit(case_id, _basis(authoritative)))
        connection.execute("UPDATE claim SET contradiction = 0 WHERE id = ?", (authoritative.id,))
        with pytest.raises(DeadlineBasisStaleError):
            service.create_explicit(
                _explicit(
                    case_id,
                    DeadlineBasisInput(
                        claim_id=authoritative.id,
                        expected_claim_version=authoritative.version,
                        value_sha256="f" * 64,
                        date_kind=DeadlineDateKind.LOCAL_DATE,
                    ),
                )
            )
        assert connection.execute("SELECT COUNT(*) FROM deadline").fetchone()[0] == 0
    finally:
        connection.close()


def test_explicit_read_revalidates_current_claim_version_and_hash(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    claim = _confirmed_claim(connection, case_id, "policy_deadline", "2026-09-01")
    service = DeadlineService(connection)
    deadline = service.create_explicit(_explicit(case_id, _basis(claim)))
    ClaimService(connection).correct_claim(
        CorrectClaimCommand(
            claim_id=claim.id,
            expected_version=claim.version,
            value=ClaimValue.from_input("2026-09-02"),
            actor="local_user",
            reason="Synthetic authority changed",
        )
    )

    with pytest.raises(DeadlineBasisStaleError):
        service.get_deadline(deadline.id)
    connection.close()


def test_calculated_rule_is_deterministic_and_unknown_or_old_rule_fails(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    claim = _confirmed_claim(connection, case_id, "purchase_instant", "2026-03-07T15:30:00-05:00")
    command = CreateCalculatedDeadlineCommand(
        case_id=case_id,
        bases=(_basis(claim, DeadlineDateKind.UTC_INSTANT),),
        timezone="America/New_York",
        rule_id=CALENDAR_DAYS_AFTER_BASIS,
        rule_version=1,
        rule_parameters=DeadlineRuleParameters.from_input({"days": 1}),
        actor="local_rule",
        reason="DST deterministic rule fixture",
    )
    service = DeadlineService(connection)
    first = service.create_calculated(command)
    second = service.create_calculated(command)
    assert first.due == second.due
    assert first.due.value == "2026-03-08T19:30:00.000000Z"

    repeated_hour_claim = _confirmed_claim(
        connection,
        case_id,
        "fall_purchase_instant",
        "2026-11-01T01:30:00-05:00",
    )
    after_repeated_hour = service.create_calculated(
        CreateCalculatedDeadlineCommand(
            case_id=case_id,
            bases=(_basis(repeated_hour_claim, DeadlineDateKind.UTC_INSTANT),),
            timezone=command.timezone,
            rule_id=command.rule_id,
            rule_version=command.rule_version,
            rule_parameters=command.rule_parameters,
            actor=command.actor,
            reason="DST overlap deterministic rule fixture",
        )
    )
    assert after_repeated_hour.due.value == "2026-11-02T06:30:00.000000Z"

    for rule_id, version in (("unknown_rule", 1), (CALENDAR_DAYS_AFTER_BASIS, 2)):
        with pytest.raises(DeadlineRuleInvalidError):
            service.create_calculated(
                CreateCalculatedDeadlineCommand(
                    case_id=case_id,
                    bases=command.bases,
                    timezone=command.timezone,
                    rule_id=rule_id,
                    rule_version=version,
                    rule_parameters=command.rule_parameters,
                    actor=command.actor,
                    reason=command.reason,
                )
            )
    connection.close()


def test_status_commands_are_optimistic_and_increment_exactly_once(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    service = DeadlineService(connection)
    deadline = service.create_suggested(
        CreateSuggestedDeadlineCommand(
            case_id=case_id,
            due=DeadlineDate(DeadlineDateKind.LOCAL_DATE, "2026-08-10", "UTC"),
            actor="local_user",
            reason="Synthetic preference",
        )
    )
    command = DeadlineStatusCommand(deadline.id, 1, "local_user", "Confirm reminder")
    confirmed = service.confirm(command)
    with pytest.raises(DeadlineVersionConflictError) as stale:
        service.confirm(command)
    assert confirmed.version == 2 and stale.value.current_version == 2
    passed = service.mark_passed(
        DeadlineStatusCommand(deadline.id, 2, "local_user", "Observed deadline pass")
    )
    completed = service.complete(
        DeadlineStatusCommand(deadline.id, 3, "local_user", "Completed follow-up")
    )
    assert (passed.version, completed.version, completed.status) == (
        3,
        4,
        DeadlineStatus.COMPLETED,
    )
    with pytest.raises(DeadlineTransitionNotAllowedError):
        service.dismiss(DeadlineStatusCommand(deadline.id, 4, "local_user", "Too late"))
    assert [event.new_version for event in service.list_deadline_events(deadline.id)] == [
        1,
        2,
        3,
        4,
    ]
    connection.close()


def test_competing_connections_allow_at_most_one_status_command(tmp_path: Path) -> None:
    path = tmp_path / "lifecase.sqlite3"
    connection, case_id = _database(path)
    deadline = DeadlineService(connection).create_suggested(
        CreateSuggestedDeadlineCommand(
            case_id=case_id,
            due=DeadlineDate(DeadlineDateKind.LOCAL_DATE, "2026-08-10", "UTC"),
            actor="local_user",
            reason="Synthetic competing command",
        )
    )
    connection.close()
    first_connection = connect_database(path)
    second_connection = connect_database(path)
    command = DeadlineStatusCommand(deadline.id, 1, "local_user", "Competing confirmation")
    try:
        committed = DeadlineService(first_connection).confirm(command)
        with pytest.raises(DeadlineVersionConflictError) as conflict:
            DeadlineService(second_connection).confirm(command)
        assert committed.version == 2
        assert conflict.value.current_version == 2
        assert len(DeadlineService(second_connection).list_deadline_events(deadline.id)) == 2
    finally:
        first_connection.close()
        second_connection.close()


def test_deadline_basis_and_history_failure_roll_back_together(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    claim = _confirmed_claim(connection, case_id, "policy_deadline", "2026-09-01")
    connection.execute(
        """
        CREATE TRIGGER fail_deadline_history BEFORE INSERT ON deadline_event
        BEGIN SELECT RAISE(ABORT, 'private synthetic SQL detail'); END
        """
    )
    with pytest.raises(DeadlinePersistenceError) as captured:
        DeadlineService(connection).create_explicit(_explicit(case_id, _basis(claim)))
    assert "private" not in str(captured.value)
    assert connection.execute("SELECT COUNT(*) FROM deadline").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM deadline_basis_claim").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM deadline_event").fetchone()[0] == 0
    assert connection.in_transaction is False
    connection.close()


def test_missing_case_and_invalid_or_overlong_metadata_are_typed(tmp_path: Path) -> None:
    connection, _case_id = _database(tmp_path / "lifecase.sqlite3")
    service = DeadlineService(connection)
    with pytest.raises(DeadlineCaseNotFoundError):
        service.create_suggested(
            CreateSuggestedDeadlineCommand(
                case_id="case_missing",
                due=DeadlineDate(DeadlineDateKind.LOCAL_DATE, "2026-08-10", "UTC"),
                actor="local_user",
                reason="Synthetic missing Case",
            )
        )
    with pytest.raises(DeadlineCommandValidationError):
        DeadlineStatusCommand("deadline", 1, " ", "reason")
    with pytest.raises(DeadlineCommandValidationError):
        DeadlineStatusCommand("deadline", 1, "actor", "x" * 1001)
    connection.close()


def test_deadline_api_exposes_distinct_kind_basis_rule_timezone_and_uncertainty(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config.database_path)
    explicit_claim = _confirmed_claim(connection, case_id, "policy_deadline", "2026-09-01")
    calculated_claim = _confirmed_claim(connection, case_id, "purchase_date", "2026-08-01")
    connection.close()

    basis_payload = {
        "claim_id": explicit_claim.id,
        "expected_claim_version": explicit_claim.version,
        "value_sha256": explicit_claim.value.sha256,
        "date_kind": "local_date",
    }
    with TestClient(create_app(config)) as client:
        explicit = client.post(
            f"/api/v1/cases/{case_id}/deadlines/explicit",
            json={
                "basis": basis_payload,
                "timezone": "Asia/Shanghai",
                "actor": "local_user",
                "reason": "Synthetic API explicit date",
            },
        )
        calculated = client.post(
            f"/api/v1/cases/{case_id}/deadlines/calculated",
            json={
                "bases": [
                    {
                        "claim_id": calculated_claim.id,
                        "expected_claim_version": calculated_claim.version,
                        "value_sha256": calculated_claim.value.sha256,
                        "date_kind": "local_date",
                    }
                ],
                "timezone": "Asia/Shanghai",
                "rule_id": CALENDAR_DAYS_AFTER_BASIS,
                "rule_version": 1,
                "rule_parameters": {"days": 30},
                "actor": "local_rule",
                "reason": "Synthetic API calculation",
            },
        )
        suggested = client.post(
            f"/api/v1/cases/{case_id}/deadlines/suggested",
            json={
                "date_kind": "local_date",
                "due_value": "2026-08-10",
                "timezone": "Asia/Shanghai",
                "actor": "local_user",
                "reason": "Personal reminder",
            },
        )
        listed = client.get(f"/api/v1/cases/{case_id}/deadlines")
        confirmed = client.post(
            f"/api/v1/deadlines/{explicit.json()['id']}/confirm",
            json={
                "expected_version": 1,
                "actor": "local_user",
                "reason": "Confirm explicit date",
            },
        )
        stale = client.post(
            f"/api/v1/deadlines/{explicit.json()['id']}/dismiss",
            json={
                "expected_version": 1,
                "actor": "local_user",
                "reason": "Stale command",
            },
        )

    assert explicit.status_code == calculated.status_code == suggested.status_code == 201
    assert listed.status_code == 200
    payloads = listed.json()
    assert {item["kind"] for item in payloads} == {"explicit", "calculated", "suggested"}
    payload_by_kind = {item["kind"]: item for item in payloads}
    assert payload_by_kind["explicit"]["bases"][0]["claim_id"] == explicit_claim.id
    assert payload_by_kind["explicit"]["uncertainty"] == "evidence_backed_date"
    assert payload_by_kind["calculated"]["rule"]["id"] == CALENDAR_DAYS_AFTER_BASIS
    assert payload_by_kind["calculated"]["rule"]["version"] == 1
    assert payload_by_kind["suggested"]["bases"] == []
    assert payload_by_kind["suggested"]["rule"] is None
    assert (
        payload_by_kind["suggested"]["uncertainty"]
        == "personal_follow_up_preference_not_external_obligation"
    )
    assert all(item["timezone"] == "Asia/Shanghai" for item in payloads)
    assert (confirmed.status_code, confirmed.json()["status"], confirmed.json()["version"]) == (
        200,
        "confirmed",
        2,
    )
    assert (stale.status_code, stale.json()["error"]["code"]) == (
        409,
        "deadline_version_conflict",
    )


def test_deadline_api_failures_are_typed_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config.database_path)
    candidate = _candidate_claim(connection, case_id, "policy_deadline", "2026-09-01")
    connection.close()
    with TestClient(create_app(config)) as client:
        invalid_basis = client.post(
            f"/api/v1/cases/{case_id}/deadlines/explicit",
            json={
                "basis": {
                    "claim_id": candidate.id,
                    "expected_claim_version": candidate.version,
                    "value_sha256": candidate.value.sha256,
                    "date_kind": "local_date",
                },
                "timezone": "Asia/Shanghai",
                "actor": "local_user",
                "reason": "Candidate is not authority",
            },
        )
        invalid_timezone = client.post(
            f"/api/v1/cases/{case_id}/deadlines/suggested",
            json={
                "date_kind": "local_date",
                "due_value": "2026-08-10",
                "timezone": "Private/Invalid",
                "actor": "local_user",
                "reason": "Invalid timezone",
            },
        )
    assert (invalid_basis.status_code, invalid_basis.json()["error"]["code"]) == (
        409,
        "deadline_basis_invalid",
    )
    assert (invalid_timezone.status_code, invalid_timezone.json()["error"]["code"]) == (
        422,
        "deadline_invalid",
    )

    def fail_list(*_args: object, **_kwargs: object) -> None:
        raise DeadlinePersistenceError("SELECT secret FROM private_table")

    monkeypatch.setattr(deadlines_api.DeadlineService, "list_case_deadlines", fail_list)
    with TestClient(create_app(config)) as client:
        failed = client.get(f"/api/v1/cases/{case_id}/deadlines")
    assert (failed.status_code, failed.json()["error"]["code"]) == (
        503,
        "deadline_storage_failed",
    )
    assert failed.json()["error"]["retryable"] is True
    assert "secret" not in failed.text and "private_table" not in failed.text


def _database(path: Path) -> tuple[sqlite3.Connection, str]:
    connection = connect_database(path)
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic Deadline Case",
        goal="Test deterministic Deadline authority",
        template_id="warranty",
    )
    return connection, case.id


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _candidate_claim(
    connection: sqlite3.Connection, case_id: str, field: str, value: str
) -> ClaimRecord:
    return ClaimService(connection).create_claim(
        CreateClaimCommand(
            case_id=case_id,
            field=field,
            value=ClaimValue.from_input(value),
            status=ClaimStatus.CANDIDATE,
            confidence=0.95,
            created_by=ClaimCreatedBy.AGENT,
            actor="synthetic_agent",
            reason="Synthetic date candidate",
        )
    )


def _confirmed_claim(
    connection: sqlite3.Connection, case_id: str, field: str, value: str
) -> ClaimRecord:
    candidate = _candidate_claim(connection, case_id, field, value)
    return ClaimService(connection).confirm_claim(
        ConfirmClaimCommand(
            candidate.id,
            candidate.version,
            ClaimConfirmationBasis.USER,
            "local_user",
            "Explicit synthetic confirmation",
        )
    )


def _corrected_claim(
    connection: sqlite3.Connection, case_id: str, field: str, value: str
) -> ClaimRecord:
    candidate = _candidate_claim(connection, case_id, field, "2026-07-31")
    return ClaimService(connection).correct_claim(
        CorrectClaimCommand(
            candidate.id,
            candidate.version,
            ClaimValue.from_input(value),
            "local_user",
            "Explicit synthetic correction",
        )
    )


def _basis(
    claim: ClaimRecord, date_kind: DeadlineDateKind = DeadlineDateKind.LOCAL_DATE
) -> DeadlineBasisInput:
    return DeadlineBasisInput(claim.id, claim.version, claim.value.sha256, date_kind)


def _missing_basis() -> DeadlineBasisInput:
    return DeadlineBasisInput("claim_missing", 1, "0" * 64, DeadlineDateKind.LOCAL_DATE)


def _explicit(case_id: str, basis: DeadlineBasisInput) -> CreateExplicitDeadlineCommand:
    return CreateExplicitDeadlineCommand(
        case_id=case_id,
        basis=basis,
        timezone="Asia/Shanghai",
        actor="local_user",
        reason="Synthetic explicit Deadline",
    )
