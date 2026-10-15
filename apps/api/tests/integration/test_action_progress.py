"""Human notes remain separate from Action authority and explicit completion."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_action_planner import _confirmed_claim
from test_analysis_action_draft import _command, _completed, _preview
from test_live_agent_question import _correct_product, _corrected_product, _QuestionClient
from test_live_agent_two_stage import _config, _create_case_with_evidence

from lifecase.actions import ActionKind, ActionPayload, ActionRiskLevel, ActionService
from lifecase.actions.models import CreateActionCommand
from lifecase.cases import CaseService
from lifecase.claims import ClaimValue
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.storage import MIGRATIONS, apply_migrations, connect_database
from lifecase.storage.backup import BackupService


def _saved(
    client: TestClient,
    config: ApplicationConfig,
    *,
    ready: bool = False,
    conflicting_serial: bool = False,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    case_id = _create_case_with_evidence(client)
    product = _corrected_product(client, case_id)
    if ready:
        with connect_database(config.database_path) as connection:
            for field, value in {
                "merchant_or_manufacturer": "合成商家",
                "purchase_date": "2026-09-01",
                "order_number": "SYNTHETIC-PROGRESS-001",
                "desired_outcome": "repair",
            }.items():
                _confirmed_claim(connection, case_id, field, value)
            if conflicting_serial:
                _confirmed_claim(connection, case_id, "serial_number", "SYNTHETIC-SERIAL-A")
                _confirmed_claim(connection, case_id, "serial_number", "SYNTHETIC-SERIAL-B")
    run = _completed(client, case_id)
    saved = client.post(
        f"/api/v1/cases/{case_id}/analysis-action-draft",
        json=_command(_preview(client, case_id, run["id"])),
    )
    assert saved.status_code == 200, saved.text
    action = client.get(f"/api/v1/cases/{case_id}/actions").json()[0]
    assert action["status"] == ("ready" if ready else "blocked")
    return case_id, action, product


def _path(case_id: str, action: dict[str, Any]) -> str:
    return f"/api/v1/cases/{case_id}/actions/{action['id']}/progress"


def _note(action: dict[str, Any], last: str | None = None) -> dict[str, Any]:
    return {
        "expected_version": action["version"],
        "expected_last_progress_id": last,
        "kind": "progress",
        "text": "  本人记录：已核对报修受理要求，等待人工联系。\n这是合成进展。  ",
        "actor": "synthetic_progress_operator",
        "reason": "手工留存本次核对经过",
    }


def _status(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "expected_version": action["version"],
        "actor": "synthetic_progress_operator",
        "reason": "明确人工更新本地待办状态",
    }


def _approval(client: TestClient, action: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/actions/{action['id']}/approval-requests",
        json={
            "expected_action_version": action["version"],
            "payload_sha256": action["payload_sha256"],
            "summary": "人工核对合成报修沟通准备草案",
            "actor": "synthetic_progress_reviewer",
            "reason": "本人明确请求本地审批",
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _approve(
    client: TestClient, action: dict[str, Any], approval: dict[str, Any]
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/actions/{action['id']}/approve",
        json={
            "approval_id": approval["id"],
            "expected_approval_version": approval["version"],
            "expected_action_version": action["version"],
            "actor": "synthetic_progress_reviewer",
            "reason": "明确批准本地准备待办，不代表已发送报修",
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["action"])


def test_notes_append_without_authority_changes_and_survive_restart(tmp_path: Path) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id, action, _ = _saved(client, config)
        path = _path(case_id, action)
        before_claims = client.get(f"/api/v1/cases/{case_id}/claims").json()
        before_case = client.get(f"/api/v1/cases/{case_id}").json()
        empty = client.get(path)
        assert empty.status_code == 200, empty.text
        assert empty.headers["cache-control"] == "no-store"
        assert empty.json() == {"action": action, "entries": []}
        command = _note(action)
        first = client.post(path, json=command)
        assert first.status_code == 201, first.text
        assert first.headers["cache-control"] == "no-store"
        entry = first.json()["entries"][0]
        assert entry["action_id"] == action["id"] and entry["case_id"] == case_id
        assert entry["action_version"] == action["version"]
        assert {key: entry[key] for key in ("kind", "text", "actor", "reason")} == {
            key: command[key] for key in ("kind", "text", "actor", "reason")
        }
        second = client.post(
            path,
            json={
                **_note(action, entry["id"]),
                "kind": "result",
                "text": "核对结果：材料尚待补齐。",
            },
        )
        assert second.status_code == 201, second.text
        snapshot = second.json()
        assert snapshot["action"] == action and len(snapshot["entries"]) == 2
        assert client.get(f"/api/v1/cases/{case_id}/approvals").json() == []
        assert client.get(f"/api/v1/cases/{case_id}/claims").json() == before_claims
        assert client.get(f"/api/v1/cases/{case_id}").json() == before_case
        workbench = client.get("/api/v1/cases").json()
        item = next(item for item in workbench if item["id"] == case_id)
        assert item["last_activity_at"] == snapshot["entries"][-1]["created_at"]
        assert item["last_activity_kind"] == "action_updated"
        assert len(boundary.resource.stages) == 1
        timeline = client.get(f"/api/v1/cases/{case_id}/timeline")
        assert timeline.status_code == 200, timeline.text
        projected = [
            item for item in timeline.json()["events"] if item["kind"] == "action_progress_recorded"
        ]
        assert [item["authority_id"] for item in projected] == [
            item["id"] for item in snapshot["entries"]
        ]
        assert projected[0]["authority_type"] == "action_progress"
        assert projected[0]["subject_id"] == action["id"]
        assert projected[0]["reason"] == command["reason"]
        assert command["text"] not in timeline.text
    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        assert restarted.get(path).json() == snapshot
    with connect_database(config.database_path) as connection:
        backup = BackupService(connection, config.database_path, config.data_root)
        artifact = backup.create()
        validated = backup.validate(backup.get_download(artifact.id).path)
        restored = backup.apply(
            validated.validation_id, "synthetic-progress", "RESTORE INTO NEW DIRECTORY"
        )
    restored_path = (
        config.data_root.parent / "recovery" / "restored" / restored.runtime_id / "lifecase.sqlite3"
    )
    with connect_database(restored_path) as connection:
        entries = connection.execute(
            "SELECT id, text, actor, reason FROM action_progress ORDER BY position"
        ).fetchall()
        assert [tuple(row) for row in entries] == [
            (entry["id"], entry["text"], entry["actor"], entry["reason"])
            for entry in snapshot["entries"]
        ]


def test_progress_conflicts_scope_and_basis_changes_leave_notes_unchanged(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id, action, product = _saved(client, config)
        path = _path(case_id, action)
        command = _note(action)
        saved = client.post(path, json=command)
        assert saved.status_code == 201, saved.text
        original_entries = saved.json()["entries"]
        assert client.post(path, json=command).status_code == 409
        current = _note(action, original_entries[-1]["id"])
        assert client.post(path, json={**current, "expected_version": 2}).status_code == 409
        other = _create_case_with_evidence(client)
        assert client.get(_path(other, action)).status_code == 404
        assert client.post(_path(other, action), json=current).status_code == 404
        _correct_product(client, product, "合成修正型号 H10")
        assert client.post(path, json=current).status_code == 409
        read = client.get(path)
        assert read.status_code == 200, read.text
        assert read.json()["entries"] == original_entries
        assert read.json()["action"]["analysis_basis_status"] == "changed"
        assert client.get(f"/api/v1/cases/{other}/timeline").status_code == 200


def test_progress_failure_rolls_back_and_unavailable_basis_remains_readable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id, action, _ = _saved(client, config)
        path = _path(case_id, action)
        initial = client.get(path)
        assert initial.status_code == 200, initial.text
        with connect_database(config.database_path) as connection:
            connection.execute(
                "CREATE TRIGGER synthetic_progress_failure AFTER INSERT ON action_progress "
                "BEGIN SELECT RAISE(ABORT, 'synthetic append failure'); END"
            )
        failed = client.post(path, json=_note(action))
        assert failed.status_code == 503, failed.text
        assert "synthetic append failure" not in failed.text
        assert client.get(path).json() == initial.json()
        with connect_database(config.database_path) as connection:
            connection.execute("DROP TRIGGER synthetic_progress_failure")
        first = client.post(path, json=_note(action))
        assert first.status_code == 201, first.text
        with connect_database(config.database_path) as connection:
            connection.execute("UPDATE source_segment SET text = text || 'synthetic corruption'")
        unavailable = client.get(path)
        assert unavailable.status_code == 200, unavailable.text
        assert unavailable.json()["entries"] == first.json()["entries"]
        assert unavailable.json()["action"]["analysis_basis_status"] == "unavailable"
        assert (
            client.post(path, json=_note(action, first.json()["entries"][0]["id"])).status_code
            == 409
        )


def test_missing_current_progress_table_fails_closed_instead_of_hiding_history(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id, action, _ = _saved(client, config)
        path = _path(case_id, action)
        saved = client.post(path, json=_note(action))
        assert saved.status_code == 201, saved.text
        with connect_database(config.database_path) as connection:
            assert (
                connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 19
            )
            connection.execute("DROP TABLE action_progress")
        assert client.get(path).status_code == 503
        assert client.post(path, json=_note(action)).status_code == 503
        assert client.get(f"/api/v1/cases/{case_id}/timeline").status_code == 503


def test_pending_approved_running_and_failed_notes_do_not_change_approval_or_action(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id, action, _ = _saved(client, config, ready=True)
        path = _path(case_id, action)
        approval = _approval(client, action)
        last = None
        for step in ("pending", "approved", "running", "failed"):
            approvals_before = client.get(f"/api/v1/cases/{case_id}/approvals").json()
            saved = client.post(path, json={**_note(action, last), "text": f"合成人工进展：{step}"})
            assert saved.status_code == 201, saved.text
            assert saved.json()["action"] == action
            assert client.get(f"/api/v1/cases/{case_id}/approvals").json() == approvals_before
            last = saved.json()["entries"][-1]["id"]
            if step == "pending":
                action = _approve(client, action, approval)
            elif step == "approved":
                started = client.post(f"/api/v1/actions/{action['id']}/start", json=_status(action))
                assert started.status_code == 200, started.text
                action = started.json()
            elif step == "running":
                failed = client.post(f"/api/v1/actions/{action['id']}/fail", json=_status(action))
                assert failed.status_code == 200, failed.text
                action = failed.json()
        cancelled = client.post(f"/api/v1/actions/{action['id']}/cancel", json=_status(action))
        assert cancelled.status_code == 200, cancelled.text
        assert client.post(path, json=_note(cancelled.json(), last)).status_code == 409
        assert len(client.get(path).json()["entries"]) == 4


def test_note_does_not_count_as_completion_evidence_and_done_is_read_only(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id, action, _ = _saved(client, config, ready=True)
        action = _approve(client, action, _approval(client, action))
        started = client.post(f"/api/v1/actions/{action['id']}/start", json=_status(action))
        assert started.status_code == 200, started.text
        action = started.json()
        path = _path(case_id, action)
        note = client.post(
            path, json={**_note(action), "kind": "result", "text": "本人已核对报修沟通准备。"}
        )
        assert note.status_code == 201, note.text
        entry = note.json()["entries"][0]
        completed_path = f"/api/v1/actions/{action['id']}/complete"
        assert (
            client.post(completed_path, json={**_status(action), "evidence": []}).status_code == 422
        )
        forged = {
            "authority_type": "source",
            "authority_id": entry["id"],
            "authority_sha256": "0" * 64,
            "expected_version": None,
        }
        assert (
            client.post(completed_path, json={**_status(action), "evidence": [forged]}).status_code
            == 409
        )
        source = client.get(f"/api/v1/cases/{case_id}").json()["sources"][0]
        evidence = {
            "authority_type": "source",
            "authority_id": source["id"],
            "authority_sha256": source["sha256"],
            "expected_version": None,
        }
        completed = client.post(completed_path, json={**_status(action), "evidence": [evidence]})
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "done"
        assert completed.json()["version"] == action["version"] + 1
        assert client.post(path, json=_note(completed.json(), entry["id"])).status_code == 409
        read = client.get(path)
        assert read.status_code == 200, read.text
        assert read.json()["entries"] == [entry]


def test_completed_analysis_action_keeps_historical_claim_evidence_after_correction(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id, action, product = _saved(client, config, ready=True)
        action = _approve(client, action, _approval(client, action))
        started = client.post(f"/api/v1/actions/{action['id']}/start", json=_status(action))
        assert started.status_code == 200, started.text
        action = started.json()
        requirement = next(
            item for item in action["requirements"] if item["claim_id"] == product["id"]
        )
        evidence = {
            "authority_type": "claim",
            "authority_id": product["id"],
            "expected_version": product["version"],
            "authority_sha256": requirement["value_sha256"],
        }
        completed = client.post(
            f"/api/v1/actions/{action['id']}/complete",
            json={**_status(action), "evidence": [evidence]},
        )
        assert completed.status_code == 200, completed.text
        original = completed.json()
        _correct_product(client, product, "完成后另行核对的 H10")
        actions = client.get(f"/api/v1/cases/{case_id}/actions")
        assert actions.status_code == 200, actions.text
        historical = actions.json()[0]
        assert historical["completion_evidence"] == original["completion_evidence"]
        assert historical["status"] == "done" and historical["version"] == original["version"]
        assert historical["analysis_basis_status"] == "changed"
        assert client.get(f"/api/v1/cases/{case_id}/timeline").status_code == 200
        assert client.get("/api/v1/cases").status_code == 200


def test_changed_running_action_cannot_complete_with_old_claim_authority(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id, action, product = _saved(client, config, ready=True)
        action = _approve(client, action, _approval(client, action))
        started = client.post(f"/api/v1/actions/{action['id']}/start", json=_status(action))
        assert started.status_code == 200, started.text
        action = started.json()
        requirement = next(
            item for item in action["requirements"] if item["claim_id"] == product["id"]
        )
        evidence = {
            "authority_type": "claim",
            "authority_id": product["id"],
            "expected_version": product["version"],
            "authority_sha256": requirement["value_sha256"],
        }
        _correct_product(client, product, "人工进一步修正的型号 H10")
        failed = client.post(
            f"/api/v1/actions/{action['id']}/complete",
            json={**_status(action), "evidence": [evidence]},
        )
        assert failed.status_code == 409, failed.text
        reread = client.get(f"/api/v1/cases/{case_id}/actions")
        assert reread.status_code == 200, reread.text
        assert reread.json()[0]["status"] == "running"
        assert reread.json()[0]["version"] == action["version"]
        assert reread.json()[0]["completion_evidence"] == []


def test_unusable_analysis_claim_cannot_commit_an_unreadable_completion(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id, action, _ = _saved(client, config, ready=True, conflicting_serial=True)
        action = _approve(client, action, _approval(client, action))
        started = client.post(f"/api/v1/actions/{action['id']}/start", json=_status(action))
        assert started.status_code == 200, started.text
        action = started.json()
        facts = client.get(f"/api/v1/cases/{case_id}/current-facts").json()["items"]
        unusable = next(item for item in facts if item["claim"]["field"] == "serial_number")
        assert unusable["usable_as_fact"] is False and unusable["claim"]["status"] == "confirmed"
        claim = unusable["claim"]
        evidence = {
            "authority_type": "claim",
            "authority_id": claim["id"],
            "expected_version": claim["version"],
            "authority_sha256": ClaimValue.from_input(claim["value"]).sha256,
        }
        failed = client.post(
            f"/api/v1/actions/{action['id']}/complete",
            json={**_status(action), "evidence": [evidence]},
        )
        assert failed.status_code == 409, failed.text
        with connect_database(config.database_path) as connection:
            stored = connection.execute(
                "SELECT status, version FROM action WHERE id = ?", (action["id"],)
            ).fetchone()
            assert tuple(stored) == ("running", action["version"])
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM action_completion_evidence WHERE action_id = ?",
                    (action["id"],),
                ).fetchone()[0]
                == 0
            )
        assert client.get(f"/api/v1/cases/{case_id}/actions").status_code == 200


def test_progress_input_is_bounded_and_last_record_binding_is_required(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id, action, _ = _saved(client, config)
        path = _path(case_id, action)
        command = _note(action)
        invalids = (
            {"text": ""},
            {"text": " "},
            {"text": "x" * 2001},
            {"text": "x\x00y"},
            {"text": 123},
            {"kind": "completed"},
            {"actor": " "},
            {"reason": " "},
            {"expected_version": True},
            {"evidence": []},
        )
        for change in invalids:
            rejected = client.post(path, json={**command, **change})
            assert rejected.status_code == 422, rejected.text
        missing = {
            key: value for key, value in command.items() if key != "expected_last_progress_id"
        }
        assert client.post(path, json=missing).status_code == 422
        assert client.get(path).json()["entries"] == []


@pytest.mark.parametrize("corruption", ("missing_event", "confirmation_actor"))
def test_completed_claim_history_corruption_is_not_trusted(tmp_path: Path, corruption: str) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id, action, product = _saved(client, config, ready=True)
        action = _approve(client, action, _approval(client, action))
        started = client.post(f"/api/v1/actions/{action['id']}/start", json=_status(action))
        assert started.status_code == 200, started.text
        action = started.json()
        requirement = next(
            item for item in action["requirements"] if item["claim_id"] == product["id"]
        )
        evidence = {
            "authority_type": "claim",
            "authority_id": product["id"],
            "expected_version": product["version"],
            "authority_sha256": requirement["value_sha256"],
        }
        completed = client.post(
            f"/api/v1/actions/{action['id']}/complete",
            json={**_status(action), "evidence": [evidence]},
        )
        assert completed.status_code == 200, completed.text
        _correct_product(client, product, "后续独立修正 H10")
        assert client.get(f"/api/v1/cases/{case_id}/actions").status_code == 200
        with connect_database(config.database_path) as connection:
            if corruption == "missing_event":
                connection.execute(
                    "DELETE FROM claim_event WHERE claim_id = ? AND new_version = ?",
                    (product["id"], product["version"]),
                )
            else:
                connection.execute(
                    "UPDATE claim_event SET actor = 'synthetic_tampered_actor' "
                    "WHERE claim_id = ? AND new_version = ?",
                    (product["id"], product["version"]),
                )
        assert client.get(f"/api/v1/cases/{case_id}/actions").status_code == 409
        assert client.get(_path(case_id, action)).status_code == 409


def test_migration_19_adds_notes_without_rewriting_18_action_authority(tmp_path: Path) -> None:
    with connect_database(tmp_path / "upgrade.sqlite3") as connection:
        apply_migrations(connection, tuple(item for item in MIGRATIONS if item.version <= 18))
        case = CaseService(connection).create_case(
            title="合成旧案", goal="保留旧待办", template_id="warranty"
        )
        action = ActionService(connection).create_action(
            CreateActionCommand(
                case_id=case.id,
                kind=ActionKind.COLLECT_EVIDENCE,
                risk_level=ActionRiskLevel.A,
                payload=ActionPayload.from_input(
                    ActionKind.COLLECT_EVIDENCE,
                    {"requirement_type": "claim", "requirement_key": "product_name"},
                ),
                requirements=(),
                dependencies=(),
                deadline=None,
                actor="synthetic",
                reason="合成旧版记录",
            )
        )
        old_action = tuple(
            connection.execute("SELECT * FROM action WHERE id = ?", (action.id,)).fetchone()
        )
        old_events = [tuple(row) for row in connection.execute("SELECT * FROM action_event")]
        apply_migrations(connection)
        assert connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 19
        assert (
            tuple(connection.execute("SELECT * FROM action WHERE id = ?", (action.id,)).fetchone())
            == old_action
        )
        assert [
            tuple(row) for row in connection.execute("SELECT * FROM action_event")
        ] == old_events
        assert connection.execute("SELECT COUNT(*) FROM action_progress").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
