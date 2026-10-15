"""A reviewed question answer becomes one explicit, versioned local Action."""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from test_action_planner import _confirmed_claim
from test_live_agent_question import (
    _authorize_question,
    _correct_product,
    _corrected_product,
    _QuestionClient,
)
from test_live_agent_two_stage import _authorize, _config, _create_case_with_evidence

from lifecase.main import create_app
from lifecase.storage import connect_database


def _completed(client: TestClient, case_id: str) -> dict[str, Any]:
    run = _authorize_question(client, case_id, "alpha-7391 的报修需要说明哪些已核实事实？")
    response = client.post(
        f"/api/v1/cases/{case_id}/live-agent/execute", json={"expected_run_id": run["id"]}
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["run"]
    assert result["status"] == "completed" and result["answer"]["status"] == "answered"
    return result


def _preview(client: TestClient, case_id: str, run_id: str) -> dict[str, Any]:
    result = client.get(f"/api/v1/cases/{case_id}/analysis-action-draft", params={"run_id": run_id})
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    return dict(result.json())


def _command(
    preview: dict[str, Any], text: str = "请协助核对合成机型 H9 的报修流程。"
) -> dict[str, Any]:
    return {
        "expected_run_id": preview["run_id"],
        "expected_run_version": preview["run_version"],
        "basis_sha256": preview["basis_sha256"],
        "answer_sha256": preview["answer_sha256"],
        "draft_text": text,
        "actor": "synthetic_action_operator",
        "reason": "本人审阅后明确保存报修待办",
        "expected_action_id": None,
        "expected_action_version": None,
    }


def test_reviewed_text_saves_once_without_approval_and_survives_restart(tmp_path: Path) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        _corrected_product(client, case_id)
        run = _completed(client, case_id)
        preview = _preview(client, case_id, run["id"])
        assert preview["saved_action"] is None
        assert "合成机型 H9" in preview["draft_text"]
        assert "SynthPhone" not in preview["draft_text"]
        assert preview["question"] == run["analysis"]["question"]
        command = _command(preview, "  本人编辑：请协助核对 H9 报修。\n保留换行和空格。  ")
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        first = client.post(path, json=command)
        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == "no-store"
        saved = first.json()
        assert saved["saved_action"]["draft_text"] == command["draft_text"]
        assert saved["saved_action"]["version"] == 1
        assert saved["saved_action"]["status"] == "blocked"
        replay = client.post(path, json=command)
        assert replay.status_code == 200 and replay.json() == saved
        changed = client.post(path, json={**command, "draft_text": "另一个页面的不同正文"})
        assert changed.status_code == 409
        assert client.get(f"/api/v1/cases/{case_id}/approvals").json() == []
        assert len(boundary.resource.stages) == 1
        actions = client.get(f"/api/v1/cases/{case_id}/actions").json()
        assert len(actions) == 1 and actions[0]["risk_level"] == "B"
        assert actions[0]["communication_draft"] == command["draft_text"]
        assert actions[0]["payload"]["analysis_run_id"] == run["id"]
        with connect_database(config.database_path) as connection:
            event = connection.execute("SELECT actor, reason FROM action_event").fetchone()
            assert tuple(event) == (command["actor"], command["reason"])
            assert connection.execute("SELECT COUNT(*) FROM action_event").fetchone()[0] == 1
    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        assert _preview(restarted, case_id, run["id"]) == saved


def test_saved_edit_requires_exact_action_version_and_generic_api_cannot_strip_binding(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_config(tmp_path), openai_client=_QuestionClient())) as client:
        case_id = _create_case_with_evidence(client)
        _corrected_product(client, case_id)
        run = _completed(client, case_id)
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        command = _command(_preview(client, case_id, run["id"]))
        saved = client.post(path, json=command).json()["saved_action"]
        revised = {
            **command,
            "draft_text": "本人重新审阅后的报修正文。",
            "expected_action_id": saved["id"],
            "expected_action_version": saved["version"],
        }
        result = client.post(path, json=revised)
        assert result.status_code == 200, result.text
        assert result.json()["saved_action"]["version"] == 2
        assert client.post(path, json=revised).status_code == 409
        generic = {
            "kind": "prepare_communication",
            "risk_level": "B",
            "payload": {"intent": "request_remedy", "recipient_role": "merchant"},
            "actor": "synthetic_operator",
            "reason": "Try removing analysis authority",
        }
        stripped = client.post(
            f"/api/v1/actions/{saved['id']}/revise", json={**generic, "expected_version": 2}
        )
        assert stripped.status_code == 409
        action = client.get(f"/api/v1/cases/{case_id}/actions").json()[0]
        bypass = client.post(
            f"/api/v1/cases/{case_id}/actions", json={**generic, "payload": action["payload"]}
        )
        assert bypass.status_code in {409, 422}


def test_changed_fact_source_run_and_wrong_case_cannot_save_old_draft(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path), openai_client=_QuestionClient())) as client:
        case_id = _create_case_with_evidence(client)
        claim = _corrected_product(client, case_id)
        run = _completed(client, case_id)
        preview = _preview(client, case_id, run["id"])
        command = _command(preview)
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        assert client.post(path, json={**command, "expected_run_version": 1}).status_code == 409
        assert client.post(path, json={**command, "answer_sha256": "a" * 64}).status_code == 409
        other = _create_case_with_evidence(client)
        assert client.post(
            f"/api/v1/cases/{other}/analysis-action-draft", json=command
        ).status_code in {404, 409}
        _correct_product(client, claim, "更新后的合成型号 H10")
        assert client.post(path, json=command).status_code == 409
        assert client.get(path, params={"run_id": run["id"]}).status_code == 409
        newer = _completed(client, case_id)
        newer_preview = _preview(client, case_id, newer["id"])
        new_command = _command(newer_preview)
        _authorize_question(client, case_id, "这个案件现在还缺哪些材料？")
        assert client.post(path, json=new_command).status_code == 409
        assert client.get(f"/api/v1/cases/{case_id}/actions").json() == []


def test_source_only_change_keeps_saved_text_readable_but_blocks_progress(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path), openai_client=_QuestionClient())) as client:
        case_id = _create_case_with_evidence(client)
        _corrected_product(client, case_id)
        run = _completed(client, case_id)
        command = _command(_preview(client, case_id, run["id"]))
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        saved = client.post(path, json=command).json()["saved_action"]
        upload = client.post(
            f"/api/v1/cases/{case_id}/sources",
            files={"file": ("new-synthetic.txt", b"New synthetic service note", "text/plain")},
        )
        assert upload.status_code == 201, upload.text
        assert client.post(path, json=command).status_code == 409
        response = client.get(f"/api/v1/cases/{case_id}/actions")
        assert response.status_code == 200, response.text
        action = response.json()[0]
        assert action["communication_draft"] == command["draft_text"]
        assert action["analysis_basis_status"] == "changed"
        status = {"expected_version": saved["version"], "actor": "synthetic", "reason": "Recheck"}
        assert (
            client.post(f"/api/v1/actions/{saved['id']}/revalidate", json=status).status_code == 409
        )


def test_read_failure_and_insert_failure_never_publish_a_stale_or_partial_action(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id = _create_case_with_evidence(client)
        _corrected_product(client, case_id)
        run = _completed(client, case_id)
        command = _command(_preview(client, case_id, run["id"]))
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        with connect_database(config.database_path) as connection:
            connection.execute(
                "CREATE TRIGGER synthetic_action_failure BEFORE INSERT ON action_event "
                "BEGIN SELECT RAISE(ABORT, 'synthetic insert failure'); END"
            )
        result = client.post(path, json=command)
        assert result.status_code == 503, result.text
        with connect_database(config.database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM action").fetchone()[0] == 0
            assert (
                connection.execute("SELECT COUNT(*) FROM action_requirement_claim").fetchone()[0]
                == 0
            )
            connection.execute("DROP TRIGGER synthetic_action_failure")
            connection.execute("UPDATE source_segment SET text = text || 'tampered'")
        assert client.get(path, params={"run_id": run["id"]}).status_code in {409, 503}
        assert client.post(path, json=command).status_code in {409, 503}


def test_corrected_fact_keeps_saved_history_and_case_navigation_readable(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path), openai_client=_QuestionClient())) as client:
        case_id = _create_case_with_evidence(client)
        claim = _corrected_product(client, case_id)
        run = _completed(client, case_id)
        command = _command(_preview(client, case_id, run["id"]))
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        saved = client.post(path, json=command).json()["saved_action"]
        _correct_product(client, claim, "更新后的 H10")
        assert client.get("/api/v1/cases").status_code == 200
        actions = client.get(f"/api/v1/cases/{case_id}/actions")
        assert actions.status_code == 200, actions.text
        assert actions.json()[0]["communication_draft"] == command["draft_text"]
        assert actions.json()[0]["analysis_basis_status"] == "changed"
        assert actions.json()[0]["requirements"][0]["claim_version"] == claim["version"]
        status = {
            "expected_version": saved["version"],
            "actor": "synthetic",
            "reason": "Retire old draft",
        }
        assert (
            client.post(f"/api/v1/actions/{saved['id']}/revalidate", json=status).status_code == 409
        )
        assert client.post(f"/api/v1/actions/{saved['id']}/cancel", json=status).status_code == 200


def test_draft_payload_limits_and_partial_version_are_rejected(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path), openai_client=_QuestionClient())) as client:
        case_id = _create_case_with_evidence(client)
        _corrected_product(client, case_id)
        run = _completed(client, case_id)
        command = _command(_preview(client, case_id, run["id"]))
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        for invalid in ("", " ", "x" * 501, "contains\x00null"):
            assert client.post(path, json={**command, "draft_text": invalid}).status_code == 422
        assert client.post(path, json={**command, "expected_action_version": 1}).status_code == 422


def test_initial_save_replay_requires_original_actor_reason_and_unadvanced_version(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_config(tmp_path), openai_client=_QuestionClient())) as client:
        case_id = _create_case_with_evidence(client)
        run = _completed(client, case_id)
        command = _command(_preview(client, case_id, run["id"]))
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        saved = client.post(path, json=command).json()["saved_action"]
        for change in ({"actor": "another_operator"}, {"reason": "another explicit reason"}):
            assert client.post(path, json={**command, **change}).status_code == 409
        assert client.post(path, json={**command, "actor": " "}).status_code == 422
        revised = {**command, "expected_action_id": saved["id"], "expected_action_version": 1}
        assert client.post(path, json=revised).status_code == 200
        assert client.post(path, json=command).status_code == 409


def test_same_value_claims_do_not_duplicate_action_requirements(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id = _create_case_with_evidence(client)
        _corrected_product(client, case_id)
        with connect_database(config.database_path) as connection:
            _confirmed_claim(connection, case_id, "product_name", "合成机型 H9")
        run = _completed(client, case_id)
        command = _command(_preview(client, case_id, run["id"]))
        result = client.post(f"/api/v1/cases/{case_id}/analysis-action-draft", json=command)
        assert result.status_code == 200, result.text
        action = client.get(f"/api/v1/cases/{case_id}/actions").json()[0]
        assert len(action["requirements"]) == 1
        assert action["requirements"][0]["field"] == "product_name"


def test_unfinished_unknown_and_legacy_runs_cannot_create_an_action_draft(tmp_path: Path) -> None:
    boundary = _QuestionClient()
    with TestClient(create_app(_config(tmp_path), openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        queued = _authorize_question(client, case_id, "zz-no-evidence-9327")
        assert client.get(path, params={"run_id": queued["id"]}).status_code == 409
        unknown = client.post(
            f"/api/v1/cases/{case_id}/live-agent/execute", json={"expected_run_id": queued["id"]}
        )
        assert unknown.status_code == 200, unknown.text
        assert unknown.json()["run"]["answer"]["status"] == "unknown"
        assert client.get(path, params={"run_id": queued["id"]}).status_code == 409
        legacy_case = _create_case_with_evidence(client)
        _authorize(client, legacy_case)
        legacy = client.get(f"/api/v1/cases/{legacy_case}/live-agent").json()["run"]
        assert legacy["stage_order"] == ["extract", "validate"]
        assert (
            client.get(
                f"/api/v1/cases/{legacy_case}/analysis-action-draft",
                params={"run_id": legacy["id"]},
            ).status_code
            == 409
        )
        assert client.get(f"/api/v1/cases/{case_id}/actions").json() == []
        assert len(boundary.resource.stages) == 1


def test_approved_draft_revision_invalidates_approval_and_stale_basis_cannot_be_approved(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id = _create_case_with_evidence(client)
        with connect_database(config.database_path) as connection:
            for field, value in {
                "product_name": "合成机型 H9",
                "merchant_or_manufacturer": "合成商家",
                "purchase_date": "2026-09-01",
                "order_number": "SYNTHETIC-001",
                "desired_outcome": "repair",
            }.items():
                _confirmed_claim(connection, case_id, field, value)
        run = _completed(client, case_id)
        command = _command(_preview(client, case_id, run["id"]))
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        saved = client.post(path, json=command).json()["saved_action"]
        action_id = saved["id"]
        assert saved["status"] == "ready"
        action = client.get(f"/api/v1/cases/{case_id}/actions").json()[0]
        request = {
            "expected_action_version": 1,
            "payload_sha256": action["payload_sha256"],
            "summary": "人工审阅精确草案",
            "actor": "synthetic_reviewer",
            "reason": "核对后申请",
        }
        approval = client.post(f"/api/v1/actions/{action_id}/approval-requests", json=request)
        assert approval.status_code == 201, approval.text
        decision = {
            "approval_id": approval.json()["id"],
            "expected_approval_version": 1,
            "expected_action_version": 1,
            "actor": "synthetic_reviewer",
            "reason": "明确批准本地待办",
        }
        approved = client.post(f"/api/v1/actions/{action_id}/approve", json=decision)
        assert approved.status_code == 200, approved.text
        revised = client.post(
            path,
            json={
                **command,
                "draft_text": "人工再次编辑，询问受理资料。",
                "expected_action_id": action_id,
                "expected_action_version": 2,
            },
        )
        assert revised.status_code == 200, revised.text
        assert revised.json()["saved_action"]["status"] == "ready"
        assert revised.json()["saved_action"]["version"] == 3
        assert client.get(f"/api/v1/cases/{case_id}/approvals").json()[0]["status"] == "invalidated"
        action = client.get(f"/api/v1/cases/{case_id}/actions").json()[0]
        next_request = {
            **request,
            "expected_action_version": 3,
            "payload_sha256": action["payload_sha256"],
        }
        pending = client.post(f"/api/v1/actions/{action_id}/approval-requests", json=next_request)
        assert pending.status_code == 201, pending.text
        changed = client.post(
            f"/api/v1/cases/{case_id}/sources",
            files={"file": ("update.txt", b"Synthetic update", "text/plain")},
        )
        assert changed.status_code == 201
        cases = client.get("/api/v1/cases")
        assert cases.status_code == 200, cases.text
        assert next(item for item in cases.json() if item["id"] == case_id)["next_action"] is None
        next_decision = {
            **decision,
            "approval_id": pending.json()["id"],
            "expected_action_version": 3,
        }
        assert (
            client.post(f"/api/v1/actions/{action_id}/approve", json=next_decision).status_code
            == 409
        )
        assert (
            client.post(f"/api/v1/actions/{action_id}/reject", json=next_decision).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/actions/{action_id}/approval-requests", json=next_request
            ).status_code
            == 409
        )
        status = {
            "expected_version": 3,
            "actor": "synthetic_reviewer",
            "reason": "Check stale start",
        }
        assert client.post(f"/api/v1/actions/{action_id}/start", json=status).status_code == 409


def test_unreadable_source_preserves_saved_body_but_blocks_any_new_save(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, openai_client=_QuestionClient())) as client:
        case_id = _create_case_with_evidence(client)
        _corrected_product(client, case_id)
        run = _completed(client, case_id)
        command = _command(_preview(client, case_id, run["id"]))
        path = f"/api/v1/cases/{case_id}/analysis-action-draft"
        saved = client.post(path, json=command)
        assert saved.status_code == 200
        with connect_database(config.database_path) as connection:
            connection.execute("UPDATE source_segment SET text = text || 'synthetic corruption'")
        assert client.get(path, params={"run_id": run["id"]}).status_code in {409, 503}
        assert client.post(path, json=command).status_code in {409, 503}
        actions = client.get(f"/api/v1/cases/{case_id}/actions")
        assert actions.status_code == 200, actions.text
        assert actions.json()[0]["communication_draft"] == command["draft_text"]
        assert actions.json()[0]["analysis_basis_status"] == "unavailable"
