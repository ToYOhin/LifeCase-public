"""A fresh analysis creates separate authority while the old Action stays historical."""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from test_action_progress import _approval, _approve, _note, _path, _saved, _status
from test_analysis_action_draft import _command, _completed, _preview
from test_live_agent_question import _correct_product, _QuestionClient, _stored_run
from test_live_agent_two_stage import _config, _create_case_with_evidence

from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.storage import connect_database


def _authority(
    config: ApplicationConfig, action_id: str
) -> dict[str, tuple[tuple[object, ...], ...]]:
    """Compare stored rows, excluding presentation-only current-basis flags."""
    with connect_database(config.database_path) as connection:
        result = {
            "action": tuple(
                tuple(row)
                for row in connection.execute("SELECT * FROM action WHERE id = ?", (action_id,))
            )
        }
        for table in (
            "action_requirement_claim",
            "action_dependency",
            "action_deadline",
            "action_completion_evidence",
            "action_event",
            "action_progress",
            "approval_request",
            "approval_event",
        ):
            result[table] = tuple(
                tuple(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} WHERE action_id = ? ORDER BY rowid", (action_id,)
                )
            )
        return result


def _running_with_record(
    client: TestClient, config: ApplicationConfig
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    case_id, action, product = _saved(client, config, ready=True)
    action = _approve(client, action, _approval(client, action))
    started = client.post(f"/api/v1/actions/{action['id']}/start", json=_status(action))
    assert started.status_code == 200, started.text
    action = started.json()
    noted = client.post(_path(case_id, action), json=_note(action))
    assert noted.status_code == 201, noted.text
    return case_id, action, product, noted.json()["entries"][0]


def test_new_analysis_saves_independent_action_without_rebinding_or_inheriting_approval(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id, old, product, old_note = _running_with_record(client, config)
        old_run_id = old["payload"]["analysis_run_id"]
        original_run = _stored_run(config, old_run_id)
        original_authority = _authority(config, old["id"])
        original_case = client.get(f"/api/v1/cases/{case_id}").json()
        original_approvals = client.get(f"/api/v1/cases/{case_id}/approvals").json()
        assert original_approvals[0]["status"] == "approved"

        _correct_product(client, product, "人工重核后的合成机型 H10")
        current = _completed(client, case_id)
        assert current["id"] != old_run_id
        preview = _preview(client, case_id, current["id"])
        assert preview["saved_action"] is None
        assert "H10" in preview["draft_text"] and "H9" not in preview["draft_text"]
        command = _command(preview, "本人重新核对 H10 的材料后，另存新的报修准备待办。")
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        attempted_rebind = client.post(
            path,
            json={
                **command,
                "expected_action_id": old["id"],
                "expected_action_version": old["version"],
            },
        )
        assert attempted_rebind.status_code == 409, attempted_rebind.text
        assert len(client.get(f"/api/v1/cases/{case_id}/actions").json()) == 1
        assert _authority(config, old["id"]) == original_authority

        other_case = _create_case_with_evidence(client)
        isolated = client.post(f"/api/v1/cases/{other_case}/analysis-action-draft", json=command)
        assert isolated.status_code in {404, 409}, isolated.text
        assert client.get(f"/api/v1/cases/{other_case}/actions").json() == []
        assert client.get(_path(other_case, old)).status_code == 404

        saved = client.post(path, json=command)
        assert saved.status_code == 200, saved.text
        new_id = saved.json()["saved_action"]["id"]
        assert new_id != old["id"]
        actions = client.get(f"/api/v1/cases/{case_id}/actions").json()
        assert len(actions) == 2
        new = next(item for item in actions if item["id"] == new_id)
        historical = next(item for item in actions if item["id"] == old["id"])
        assert new["version"] == 1 and new["status"] == "ready"
        assert new["payload"]["analysis_run_id"] == current["id"]
        assert new["payload"]["analysis_basis_sha256"] == preview["basis_sha256"]
        assert new["analysis_basis_status"] == "current"
        assert new["completion_evidence"] == []
        assert client.get(_path(case_id, new)).json()["entries"] == []
        assert historical["status"] == old["status"] == "running"
        assert historical["version"] == old["version"]
        assert historical["payload"] == old["payload"]
        assert historical["analysis_basis_status"] == "changed"
        assert client.get(_path(case_id, old)).json()["entries"] == [old_note]
        assert client.get(f"/api/v1/cases/{case_id}/approvals").json() == original_approvals
        assert client.post(f"/api/v1/actions/{new_id}/start", json=_status(new)).status_code == 409
        wrong_approval = client.post(
            f"/api/v1/actions/{new_id}/approve",
            json={
                "approval_id": original_approvals[0]["id"],
                "expected_approval_version": original_approvals[0]["version"],
                "expected_action_version": new["version"],
                "actor": "synthetic_reviewer",
                "reason": "不得将旧审批当作新待办授权",
            },
        )
        assert wrong_approval.status_code == 409, wrong_approval.text
        assert _authority(config, old["id"]) == original_authority
        assert _stored_run(config, old_run_id) == original_run
        assert client.get(f"/api/v1/cases/{case_id}").json() == original_case
        snapshot = client.get(f"/api/v1/cases/{case_id}/actions").json()
        history = client.get(f"/api/v1/cases/{case_id}/live-agent").json()["run_history"]
        assert {item["id"] for item in history} == {old_run_id, current["id"]}
        assert len(boundary.resource.stages) == 2
    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        assert restarted.get(f"/api/v1/cases/{case_id}/actions").json() == snapshot
        assert restarted.get(_path(case_id, old)).json()["entries"] == [old_note]
        assert _stored_run(config, old_run_id) == original_run
        assert _authority(config, old["id"]) == original_authority


def test_cancel_old_action_is_separate_versioned_choice_and_keeps_new_authority_untouched(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id, old, _, old_note = _running_with_record(client, config)
        old_run = _stored_run(config, old["payload"]["analysis_run_id"])
        original_old = _authority(config, old["id"])
        imported = client.post(
            f"/api/v1/cases/{case_id}/sources",
            files={
                "file": (
                    "new-synthetic-repair-note.txt",
                    b"Synthetic new repair material",
                    "text/plain",
                )
            },
        )
        assert imported.status_code == 201, imported.text
        current = _completed(client, case_id)
        preview = _preview(client, case_id, current["id"])
        saved = client.post(
            f"/api/v1/cases/{case_id}/analysis-action-draft",
            json=_command(preview, "本人已核对新增材料，明确另存新的沟通准备待办。"),
        )
        assert saved.status_code == 200, saved.text
        new_id = saved.json()["saved_action"]["id"]
        original_new = _authority(config, new_id)
        assert _authority(config, old["id"]) == original_old
        stale_cancel = client.post(
            f"/api/v1/actions/{old['id']}/cancel",
            json={**_status(old), "expected_version": old["version"] - 1},
        )
        assert stale_cancel.status_code == 409, stale_cancel.text
        assert _authority(config, old["id"]) == original_old
        assert _authority(config, new_id) == original_new

        actor, reason = (
            "synthetic_retirement_operator",
            "新待办已另存，本人选择取消旧准备待办并保留历史",
        )
        cancelled = client.post(
            f"/api/v1/actions/{old['id']}/cancel",
            json={**_status(old), "actor": actor, "reason": reason},
        )
        assert cancelled.status_code == 200, cancelled.text
        historical = cancelled.json()
        assert historical["id"] == old["id"] and historical["status"] == "cancelled"
        assert historical["version"] == old["version"] + 1
        assert historical["payload"] == old["payload"]
        assert _authority(config, new_id) == original_new
        after_old = _authority(config, old["id"])
        for table in (
            "action_requirement_claim",
            "action_dependency",
            "action_deadline",
            "action_completion_evidence",
            "action_progress",
        ):
            assert after_old[table] == original_old[table]
        assert after_old["action_event"][:-1] == original_old["action_event"]
        assert after_old["approval_event"][:-1] == original_old["approval_event"]
        approvals = client.get(f"/api/v1/cases/{case_id}/approvals").json()
        assert len(approvals) == 1 and approvals[0]["action_id"] == old["id"]
        assert approvals[0]["status"] == "invalidated"
        timeline = client.get(f"/api/v1/cases/{case_id}/timeline")
        assert timeline.status_code == 200, timeline.text
        cancellations = [
            event for event in timeline.json()["events"] if event["kind"] == "action_cancelled"
        ]
        assert len(cancellations) == 1
        assert cancellations[0]["subject_id"] == old["id"]
        assert cancellations[0]["actor"] == actor and cancellations[0]["reason"] == reason
        assert client.get(_path(case_id, historical)).json()["entries"] == [old_note]
        assert _stored_run(config, old["payload"]["analysis_run_id"]) == old_run
        assert len(boundary.resource.stages) == 2
    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        records = restarted.get(f"/api/v1/cases/{case_id}/actions")
        assert records.status_code == 200, records.text
        assert (
            next(item for item in records.json() if item["id"] == old["id"])["status"]
            == "cancelled"
        )
        assert next(item for item in records.json() if item["id"] == new_id)["status"] == "ready"
        assert restarted.get(_path(case_id, old)).json()["entries"] == [old_note]
        assert _authority(config, new_id) == original_new
        assert _stored_run(config, old["payload"]["analysis_run_id"]) == old_run
