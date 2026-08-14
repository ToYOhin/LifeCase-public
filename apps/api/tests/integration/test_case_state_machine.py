"""Integration coverage for the deterministic M2-01 Case lifecycle."""

import sqlite3
from pathlib import Path

import pytest

from lifecase.cases import (
    CaseNotFoundError,
    CasePersistenceError,
    CaseService,
    CaseTransitionNotAllowedError,
    CaseVersionConflictError,
)
from lifecase.cases.models import (
    CaseCommandValidationError,
    CaseStatus,
    CaseTransitionCommand,
)
from lifecase.cases.state_machine import ALLOWED_TRANSITIONS, is_transition_allowed
from lifecase.storage import apply_migrations, connect_database

_NOW = "2026-08-01T00:00:00.000000Z"
_CLOSED_AT = "2026-08-01T00:01:00.000000Z"

EXPECTED_EDGES = {
    CaseStatus.INTAKE: {CaseStatus.NEEDS_REVIEW, CaseStatus.READY, CaseStatus.ABANDONED},
    CaseStatus.NEEDS_REVIEW: {CaseStatus.INTAKE, CaseStatus.READY, CaseStatus.ABANDONED},
    CaseStatus.READY: {
        CaseStatus.NEEDS_REVIEW,
        CaseStatus.IN_PROGRESS,
        CaseStatus.ABANDONED,
    },
    CaseStatus.IN_PROGRESS: {
        CaseStatus.NEEDS_REVIEW,
        CaseStatus.WAITING_EXTERNAL,
        CaseStatus.BLOCKED,
        CaseStatus.RESOLVED,
        CaseStatus.ABANDONED,
    },
    CaseStatus.WAITING_EXTERNAL: {
        CaseStatus.IN_PROGRESS,
        CaseStatus.BLOCKED,
        CaseStatus.RESOLVED,
        CaseStatus.ABANDONED,
    },
    CaseStatus.BLOCKED: {
        CaseStatus.NEEDS_REVIEW,
        CaseStatus.IN_PROGRESS,
        CaseStatus.WAITING_EXTERNAL,
        CaseStatus.ABANDONED,
    },
    CaseStatus.RESOLVED: {CaseStatus.ARCHIVED},
    CaseStatus.ABANDONED: {CaseStatus.ARCHIVED},
    CaseStatus.ARCHIVED: set(),
}


def test_state_values_and_allowed_edges_are_explicit() -> None:
    assert {status.value for status in CaseStatus} == {
        "intake",
        "needs_review",
        "ready",
        "in_progress",
        "waiting_external",
        "blocked",
        "resolved",
        "abandoned",
        "archived",
    }
    assert {status: set(targets) for status, targets in ALLOWED_TRANSITIONS.items()} == (
        EXPECTED_EDGES
    )
    for prior in CaseStatus:
        for target in CaseStatus:
            assert is_transition_allowed(prior, target) is (target in EXPECTED_EDGES[prior])


@pytest.mark.parametrize(
    ("override",),
    [
        ({"case_id": " "},),
        ({"case_id": "x" * 201},),
        ({"expected_version": 0},),
        ({"expected_version": True},),
        ({"target_status": "ready"},),
        ({"actor": " "},),
        ({"actor": "x" * 121},),
        ({"reason": " "},),
        ({"reason": "x" * 1001},),
    ],
)
def test_transition_command_rejects_invalid_boundaries(override: dict[str, object]) -> None:
    values: dict[str, object] = {
        "case_id": "case_synthetic",
        "expected_version": 1,
        "target_status": CaseStatus.READY,
        "actor": "local_user",
        "reason": "Synthetic lifecycle test",
    }
    values.update(override)

    with pytest.raises(CaseCommandValidationError):
        CaseTransitionCommand(**values)  # type: ignore[arg-type]


def test_transition_command_normalizes_metadata() -> None:
    command = CaseTransitionCommand(
        case_id=" case_synthetic ",
        expected_version=1,
        target_status=CaseStatus.READY,
        actor=" local_user ",
        reason=" Synthetic lifecycle test ",
    )

    assert command.case_id == "case_synthetic"
    assert command.actor == "local_user"
    assert command.reason == "Synthetic lifecycle test"


def test_every_allowed_edge_commits_case_and_complete_history(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    service = CaseService(connection)
    try:
        edge_index = 0
        for prior, targets in EXPECTED_EDGES.items():
            for target in targets:
                case_id = f"case_allowed_{edge_index}"
                edge_index += 1
                _insert_case(connection, case_id, prior)
                result = service.transition_case(
                    CaseTransitionCommand(
                        case_id=case_id,
                        expected_version=1,
                        target_status=target,
                        actor="local_user",
                        reason=f"Synthetic {prior.value} to {target.value}",
                    )
                )

                assert result.case.status is target
                assert result.case.version == 2
                assert result.case.updated_at == result.transition.transitioned_at
                assert result.transition.case_id == case_id
                assert result.transition.prior_status is prior
                assert result.transition.new_status is target
                assert (result.transition.prior_version, result.transition.new_version) == (1, 2)
                assert result.transition.actor == "local_user"
                assert result.transition.reason == f"Synthetic {prior.value} to {target.value}"
                assert result.transition.transitioned_at.endswith("Z")
                assert service.list_case_transitions(case_id) == (result.transition,)
                if target in {
                    CaseStatus.RESOLVED,
                    CaseStatus.ABANDONED,
                    CaseStatus.ARCHIVED,
                }:
                    assert result.case.closed_at is not None
                else:
                    assert result.case.closed_at is None
                if prior in {CaseStatus.RESOLVED, CaseStatus.ABANDONED}:
                    assert result.case.closed_at == _CLOSED_AT
    finally:
        connection.close()


def test_every_forbidden_edge_leaves_case_and_history_unchanged(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    service = CaseService(connection)
    try:
        edge_index = 0
        for prior in CaseStatus:
            for target in CaseStatus:
                if target in EXPECTED_EDGES[prior]:
                    continue
                case_id = f"case_forbidden_{edge_index}"
                edge_index += 1
                _insert_case(connection, case_id, prior)

                with pytest.raises(CaseTransitionNotAllowedError):
                    service.transition_case(
                        CaseTransitionCommand(
                            case_id=case_id,
                            expected_version=1,
                            target_status=target,
                            actor="local_user",
                            reason="Synthetic forbidden transition",
                        )
                    )

                row = connection.execute(
                    "SELECT status, version, updated_at, closed_at FROM case_record WHERE id = ?",
                    (case_id,),
                ).fetchone()
                assert tuple(row) == (
                    prior.value,
                    1,
                    _NOW,
                    _CLOSED_AT
                    if prior in {CaseStatus.RESOLVED, CaseStatus.ABANDONED, CaseStatus.ARCHIVED}
                    else None,
                )
                assert service.list_case_transitions(case_id) == ()
                assert connection.in_transaction is False
    finally:
        connection.close()


def test_stale_competing_command_fails_without_a_second_write(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    first_connection = connect_database(database_path)
    apply_migrations(first_connection)
    _insert_case(first_connection, "case_competing", CaseStatus.INTAKE)
    second_connection = connect_database(database_path)
    command = CaseTransitionCommand(
        case_id="case_competing",
        expected_version=1,
        target_status=CaseStatus.READY,
        actor="local_user",
        reason="Synthetic competing transition",
    )

    try:
        first = CaseService(first_connection).transition_case(command)
        with pytest.raises(CaseVersionConflictError) as captured:
            CaseService(second_connection).transition_case(command)

        assert first.case.version == 2
        assert captured.value.current_version == 2
        assert CaseService(second_connection).get_case("case_competing").version == 2
        assert len(CaseService(second_connection).list_case_transitions("case_competing")) == 1
        assert second_connection.in_transaction is False
    finally:
        second_connection.close()
        first_connection.close()


def test_history_write_failure_rolls_back_case_update(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_case(connection, "case_rollback", CaseStatus.READY)
    connection.execute(
        """
        CREATE TRIGGER fail_case_transition_history
        BEFORE INSERT ON case_transition
        BEGIN
            SELECT RAISE(ABORT, 'synthetic private persistence detail');
        END
        """
    )
    service = CaseService(connection)

    try:
        with pytest.raises(CasePersistenceError) as captured:
            service.transition_case(
                CaseTransitionCommand(
                    case_id="case_rollback",
                    expected_version=1,
                    target_status=CaseStatus.IN_PROGRESS,
                    actor="local_user",
                    reason="Synthetic rollback test",
                )
            )

        record = service.get_case("case_rollback")
        assert (record.status, record.version, record.updated_at) == (
            CaseStatus.READY,
            1,
            _NOW,
        )
        assert service.list_case_transitions("case_rollback") == ()
        assert connection.in_transaction is False
        assert "synthetic private persistence detail" not in str(captured.value)
        assert str(tmp_path) not in str(captured.value)
    finally:
        connection.close()


def test_transition_and_history_survive_restarted_connection(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection = connect_database(database_path)
    apply_migrations(connection)
    _insert_case(connection, "case_restart", CaseStatus.IN_PROGRESS)
    expected = CaseService(connection).transition_case(
        CaseTransitionCommand(
            case_id="case_restart",
            expected_version=1,
            target_status=CaseStatus.RESOLVED,
            actor="local_user",
            reason="Synthetic completed outcome",
        )
    )
    connection.close()

    restarted = connect_database(database_path)
    try:
        restored_case = CaseService(restarted).get_case("case_restart")
        restored_history = CaseService(restarted).list_case_transitions("case_restart")

        assert restored_case == expected.case
        assert restored_history == (expected.transition,)
        assert restored_case.closed_at == expected.transition.transitioned_at
    finally:
        restarted.close()


def test_missing_case_fails_without_history(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        with pytest.raises(CaseNotFoundError):
            CaseService(connection).transition_case(
                CaseTransitionCommand(
                    case_id="case_missing",
                    expected_version=1,
                    target_status=CaseStatus.READY,
                    actor="local_user",
                    reason="Synthetic missing Case",
                )
            )
        with pytest.raises(CaseNotFoundError):
            CaseService(connection).list_case_transitions("case_missing")
        assert connection.in_transaction is False
    finally:
        connection.close()


def _database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    return connection


def _insert_case(
    connection: sqlite3.Connection,
    case_id: str,
    status: CaseStatus,
) -> None:
    closed_at = (
        _CLOSED_AT
        if status in {CaseStatus.RESOLVED, CaseStatus.ABANDONED, CaseStatus.ARCHIVED}
        else None
    )
    connection.execute(
        """
        INSERT INTO case_record (
            id, type, template_version, title, goal, status,
            priority, version, created_at, updated_at, closed_at
        ) VALUES (?, 'warranty', 1, 'Synthetic lifecycle Case',
                  'Test deterministic transitions', ?, 0, 1, ?, ?, ?)
        """,
        (case_id, status.value, _NOW, _NOW, closed_at),
    )
