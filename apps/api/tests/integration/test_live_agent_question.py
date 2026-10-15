"""Question-bound read-only runs through the real local HTTP contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from test_live_agent_two_stage import _authorize, _Client, _config, _create_case_with_evidence

from lifecase.agent.service import StageRunner
from lifecase.claims import ClaimService, ClaimValue, CorrectClaimCommand
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.providers import OpenAIResponsesResource, canonical_json_object
from lifecase.storage import connect_database


class _QuestionResponses:
    """Deterministic transport; real adapters, RAG validation, and persistence remain active."""

    def __init__(
        self,
        *,
        fail_once: type[Exception] | None = None,
        before_reply: Callable[[], None] | None = None,
    ) -> None:
        self.stages: list[str] = []
        self.inputs: list[dict[str, Any]] = []
        self.fail_once = fail_once
        self.before_reply = before_reply

    def create(self, **kwargs: Any) -> Awaitable[Any]:
        payload = json.loads(kwargs["input"][1]["content"][0]["text"])
        properties = kwargs["text"]["format"]["schema"]["properties"]
        self.stages.append(properties["stage"]["const"])
        self.inputs.append(payload)

        async def reply() -> dict[str, object]:
            if self.fail_once is not None:
                failure, self.fail_once = self.fail_once, None
                raise failure("Synthetic question transport interruption")
            if self.before_reply is not None:
                callback, self.before_reply = self.before_reply, None
                callback()
            return {
                "status": "completed",
                "model": str(kwargs["model"]),
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": canonical_json_object(
                                    {
                                        "stage": "validate",
                                        "stage_version": properties["stage_version"]["const"],
                                        "checkpoint_schema_version": properties[
                                            "checkpoint_schema_version"
                                        ]["const"],
                                        "checkpoint": _question_answer(payload),
                                    },
                                    maximum_bytes=65_536,
                                ),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
            }

        return reply()


class _QuestionClient:
    def __init__(self, **kwargs: Any) -> None:
        self.resource = _QuestionResponses(**kwargs)
        self.responses: OpenAIResponsesResource = self.resource


def _question_answer(payload: dict[str, Any]) -> dict[str, object]:
    stage = payload["stage_payload"]
    statements: list[dict[str, object]] = []
    for fact in stage["current_facts"]["items"]:
        if not fact["usable_as_fact"]:
            continue
        value = fact["value"]
        literal = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        statements.append(
            {
                "text": f"当前已核实的 {fact['field']}：{literal}",
                "source_refs": [],
                "fact_refs": [
                    {
                        "case_id": payload["case_id"],
                        "claim_id": fact["claim_id"],
                        "version": fact["version"],
                        "value_sha256": fact["value_sha256"],
                    }
                ],
            }
        )
    # A policy quote is disjoint from the corrected product value in the same
    # source. It remains source evidence rather than a fabricated fact citation.
    for item in stage["tool_result"]["context"]["items"]:
        text = item["text"]
        marker = "Warranty marker alpha-7391 lasts twenty-four months."
        if marker in text:
            start = text.index(marker)
            statements.append(
                {
                    "text": "材料原文：" + marker,
                    "source_refs": [
                        {
                            **item["citation"],
                            "quote_start": start,
                            "quote_end": start + len(marker),
                            "quote_hash": hashlib.sha256(marker.encode()).hexdigest(),
                        }
                    ],
                    "fact_refs": [],
                }
            )
            break
    return {
        "schema_version": "evidence_backed_answer.v2",
        "status": "answered" if statements else "unknown",
        "statements": statements,
        "uncertainty_reason": None if statements else "合成材料未提供可核对的依据。",
        "tool": {
            "name": "evidence_context.search.v1",
            "call_count": 1,
            "context_sha256": stage["tool_result"]["context_sha256"],
        },
    }


def _authorize_question(client: TestClient, case_id: str, question: str) -> dict[str, Any]:
    preview = client.post(
        f"/api/v1/cases/{case_id}/live-agent/preview", json={"question": question}
    )
    assert preview.status_code == 200, preview.text
    created = client.post(
        f"/api/v1/cases/{case_id}/live-agent",
        json={
            "question": question,
            "confirm_read_only": True,
            "preview_sha256": preview.json()["preview"]["preview_sha256"],
        },
    )
    assert created.status_code == 201, created.text
    return cast(dict[str, Any], created.json()["run"])


def _corrected_product(client: TestClient, case_id: str) -> dict[str, Any]:
    segment = client.get(f"/api/v1/cases/{case_id}/source-segments").json()[0]
    start = segment["text"].index("SynthPhone")
    candidate = client.post(
        f"/api/v1/cases/{case_id}/claims",
        json={
            "field": "product_name",
            "value": "SynthPhone",
            "segment_id": segment["id"],
            "quote_start": start,
            "quote_end": start + len("SynthPhone"),
            "actor": "synthetic_question_operator",
            "reason": "从合成原文登记",
        },
    )
    assert candidate.status_code == 201, candidate.text
    return _correct_product(client, candidate.json(), "合成机型 H9")


def _correct_product(client: TestClient, prior: dict[str, Any], value: str) -> dict[str, Any]:
    corrected = client.post(
        f"/api/v1/claims/{prior['id']}/correct",
        json={
            "expected_version": prior["version"],
            "value": value,
            "actor": "synthetic_question_reviewer",
            "reason": "本人核对合成铭牌，明确修正型号",
        },
    )
    assert corrected.status_code == 200, corrected.text
    return cast(dict[str, Any], corrected.json())


def _stored_run(config: ApplicationConfig, run_id: str) -> tuple[tuple[object, ...], ...]:
    connection = connect_database(config.database_path)
    try:
        run = connection.execute("SELECT * FROM agent_run WHERE id = ?", (run_id,)).fetchone()
        stages = connection.execute(
            "SELECT * FROM agent_stage_run WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        return (tuple(run), *(tuple(stage) for stage in stages))
    finally:
        connection.close()


def test_question_preview_authorization_and_restart_do_not_change_case_goal(tmp_path: Path) -> None:
    config = _config(tmp_path)
    boundary = _Client()
    question = "请根据 alpha-7391 材料说明：本次还缺什么保修依据？"
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        original_case = client.get(f"/api/v1/cases/{case_id}").json()
        preview = client.post(
            f"/api/v1/cases/{case_id}/live-agent/preview", json={"question": question}
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["preview"]["question"] == question
        authorization = {
            "preview_sha256": preview.json()["preview"]["preview_sha256"],
            "confirm_read_only": True,
            "question": question,
        }
        wrong_question = client.post(
            f"/api/v1/cases/{case_id}/live-agent",
            json={**authorization, "question": "这个产品的购买日期是什么？"},
        )
        assert wrong_question.status_code == 409
        created = client.post(f"/api/v1/cases/{case_id}/live-agent", json=authorization)
        assert created.status_code == 201, created.text
        run = created.json()["run"]
        assert run["stage_order"] == ["validate"]
        assert run["analysis"]["question"] == question
        assert run["analysis"]["basis_status"] == "current"
        assert run["claims"] == []
        assert client.get(f"/api/v1/cases/{case_id}").json() == original_case
        assert boundary.resource.stages == []
    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        restored = restarted.get(f"/api/v1/cases/{case_id}/live-agent")
        assert restored.status_code == 200, restored.text
        assert restored.json()["run"] == run
        cancelled = restarted.post(
            f"/api/v1/cases/{case_id}/live-agent/cancel", json={"expected_run_id": run["id"]}
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["run"]["analysis"] == run["analysis"]
        assert cancelled.json()["run"]["status"] == "cancelled"


def test_changed_material_marks_binding_stale_and_refuses_execution(tmp_path: Path) -> None:
    boundary = _Client()
    with TestClient(create_app(_config(tmp_path), openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        other = _create_case_with_evidence(client)
        question = "alpha-7391 的保修说明包含什么？"
        preview = client.post(
            f"/api/v1/cases/{case_id}/live-agent/preview", json={"question": question}
        )
        assert preview.status_code == 200, preview.text
        authorization = {
            "preview_sha256": preview.json()["preview"]["preview_sha256"],
            "question": question,
            "confirm_read_only": True,
        }
        assert (
            client.post(f"/api/v1/cases/{other}/live-agent", json=authorization).status_code == 409
        )
        created = client.post(f"/api/v1/cases/{case_id}/live-agent", json=authorization)
        assert created.status_code == 201, created.text
        upload = client.post(
            f"/api/v1/cases/{case_id}/sources",
            files={"file": ("extra-synthetic.txt", "alpha-7391 补充合成说明", "text/plain")},
        )
        assert upload.status_code == 201
        stale = client.get(f"/api/v1/cases/{case_id}/live-agent").json()
        assert stale["run"]["analysis"]["basis_status"] == "changed"
        assert stale["run"]["analysis"]["question"] == question
        assert stale["run"]["can_execute"] is False
        refused = client.post(
            f"/api/v1/cases/{case_id}/live-agent/execute",
            json={"expected_run_id": created.json()["run"]["id"]},
        )
        assert refused.status_code == 409, refused.text
        assert boundary.resource.stages == []


def test_question_executes_one_adapter_call_with_current_correction_and_scoped_quotes(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient()
    question = "请回答当前人工修正的型号，并引用 alpha-7391 的保修说明。"
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        corrected = _corrected_product(client, case_id)
        original_goal = client.get(f"/api/v1/cases/{case_id}").json()["goal"]
        other = _create_case_with_evidence(client)
        other_claim = _corrected_product(client, other)
        _correct_product(client, other_claim, "另案型号 B99")
        claims_before = client.get(f"/api/v1/cases/{case_id}/claims").json()
        created = _authorize_question(client, case_id, question)
        completed = client.post(
            f"/api/v1/cases/{case_id}/live-agent/execute", json={"expected_run_id": created["id"]}
        )
        assert completed.status_code == 200, completed.text
        run = completed.json()["run"]
        assert run["id"] == created["id"]
        assert run["stage_order"] == ["validate"]
        assert run["status"] == "completed"
        assert run["usage"]["calls"] == 1
        assert run["usage"]["total_tokens"] == 30
        assert run["claims"] == []
        assert client.get(f"/api/v1/cases/{case_id}/claims").json() == claims_before
        assert client.get(f"/api/v1/cases/{case_id}").json()["goal"] == original_goal
        answer = run["answer"]
        assert answer["status"] == "answered"
        assert any("合成机型 H9" in statement for statement in answer["statements"])
        assert all(
            "SynthPhone" not in statement and "B99" not in statement
            for statement in answer["statements"]
        )
        fact = answer["fact_references"][0]
        assert fact["claim_id"] == corrected["id"]
        assert fact["version"] == corrected["version"]
        assert fact["value"] == "合成机型 H9"
        assert fact["confirmation_basis"] == "correction"
        assert fact["confirmation"]["actor"] == "synthetic_question_reviewer"
        assert fact["confirmation"]["reason"] == "本人核对合成铭牌，明确修正型号"
        assert fact["citations"][0]["quote_text"] == "SynthPhone"
        assert answer["citations"]
        assert {item["case_id"] for item in answer["citations"]} == {case_id}
        assert client.get(f"/api/v1/cases/{other}/live-agent").json()["run"] is None

    assert boundary.resource.stages == ["validate"]
    sent = boundary.resource.inputs[0]["stage_payload"]
    assert sent["question"] == question
    assert sent["retrieval_query"] != question
    assert sent["tool_result"]["context"]["query"] == sent["retrieval_query"]
    assert sent["current_facts"]["case_id"] == case_id
    assert {fact["claim_id"] for fact in sent["current_facts"]["items"]} == {corrected["id"]}
    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        assert restarted.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"] == run
        assert boundary.resource.stages == ["validate"]


def test_changed_fact_reanalysis_keeps_original_run_answer_and_binding_unchanged(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient()
    question = "当前型号与 alpha-7391 保修说明是什么？"
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        claim = _corrected_product(client, case_id)
        first = _authorize_question(client, case_id, question)
        response = client.post(
            f"/api/v1/cases/{case_id}/live-agent/execute", json={"expected_run_id": first["id"]}
        )
        assert response.status_code == 200, response.text
        original = response.json()["run"]
        original_rows = _stored_run(config, original["id"])
        corrected = _correct_product(client, claim, "合成机型 H10")
        stale = client.get(f"/api/v1/cases/{case_id}/live-agent").json()
        assert stale["run"]["analysis"]["basis_status"] == "changed"
        assert stale["run"]["answer"] == original["answer"]
        assert stale["run_history"][0]["question"] == question
        assert stale["run_history"][0]["basis_status"] == "changed"
        assert _stored_run(config, original["id"]) == original_rows
        new_question = "请按最新型号重新分析 alpha-7391 保修材料。"
        second = _authorize_question(client, case_id, new_question)
        assert second["id"] != original["id"]
        completed = client.post(
            f"/api/v1/cases/{case_id}/live-agent/execute", json={"expected_run_id": second["id"]}
        )
        assert completed.status_code == 200, completed.text
        result = completed.json()
        assert result["run"]["analysis"]["question"] == new_question
        assert result["run"]["analysis"]["basis_status"] == "current"
        assert result["run"]["answer"]["fact_references"][0]["version"] == corrected["version"]
        assert any("H10" in value for value in result["run"]["answer"]["statements"])
        old = next(item for item in result["run_history"] if item["id"] == original["id"])
        assert old["question"] == question
        assert old["answer"] == original["answer"]
        assert old["basis_status"] == "changed"
        assert _stored_run(config, original["id"]) == original_rows
    assert boundary.resource.stages == ["validate", "validate"]


def test_new_question_keeps_legacy_two_stage_history_readable_and_unmodified(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    legacy_transport = _Client()
    with TestClient(create_app(config, openai_client=legacy_transport)) as client:
        case_id = _create_case_with_evidence(client)
        _authorize(client, case_id)
        response = client.post(f"/api/v1/cases/{case_id}/live-agent/execute")
        assert response.status_code == 200, response.text
        legacy = response.json()["run"]
    rows = _stored_run(config, legacy["id"])
    transport = _QuestionClient()
    with TestClient(create_app(config, openai_client=transport)) as client:
        question_run = _authorize_question(client, case_id, "alpha-7391 的保修原文是什么？")
        response = client.post(
            f"/api/v1/cases/{case_id}/live-agent/execute",
            json={"expected_run_id": question_run["id"]},
        )
        assert response.status_code == 200, response.text
        history = response.json()["run_history"]
        old = next(item for item in history if item["id"] == legacy["id"])
        assert old["stage_order"] == ["extract", "validate"]
        assert old["question"] is None
        assert old["basis_status"] == "legacy"
        assert old["answer"] == legacy["answer"]
        assert _stored_run(config, legacy["id"]) == rows
    assert legacy_transport.resource.stages == ["extract", "validate"]
    assert transport.resource.stages == ["validate"]


@pytest.mark.parametrize("failure", [ConnectionError, TimeoutError])
def test_question_retry_after_restart_uses_identical_authorized_input(
    tmp_path: Path, failure: type[Exception]
) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient(fail_once=failure)
    question = "请引用 alpha-7391 的保修原文，失败后仍回答这个问题。"
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        created = _authorize_question(client, case_id, question)
        failed = client.post(
            f"/api/v1/cases/{case_id}/live-agent/execute", json={"expected_run_id": created["id"]}
        )
        assert failed.status_code == 503, failed.text
        stopped = client.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"]
        assert stopped["status"] == "needs_review"
        assert stopped["can_retry"] is True
        assert stopped["analysis"] == created["analysis"]
        assert stopped["answer"] is None
    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        other_preview = restarted.post(
            f"/api/v1/cases/{case_id}/live-agent/preview", json={"question": "尚未授权的另一个问题"}
        )
        assert other_preview.status_code == 200, other_preview.text
        assert other_preview.json()["run"]["analysis"]["question"] == question
        completed = restarted.post(
            f"/api/v1/cases/{case_id}/live-agent/retry", json={"expected_run_id": created["id"]}
        )
        assert completed.status_code == 200, completed.text
        run = completed.json()["run"]
        assert run["id"] == created["id"]
        assert run["status"] == "completed"
        assert run["usage"]["calls"] == 2
        assert run["analysis"] == created["analysis"]
    assert boundary.resource.stages == ["validate", "validate"]
    assert boundary.resource.inputs[0] == boundary.resource.inputs[1]


@pytest.mark.parametrize("failure", [ConnectionError, TimeoutError])
def test_question_two_failures_exhaust_retry_without_a_third_call(
    tmp_path: Path, failure: type[Exception]
) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        created = _authorize_question(client, case_id, "连续失败仍只分析 alpha-7391 原问题。")
        command = {"expected_run_id": created["id"]}
        original_case = client.get(f"/api/v1/cases/{case_id}").json()
        for count, action in enumerate(("execute", "retry"), start=1):
            boundary.resource.fail_once = failure
            failed = client.post(f"/api/v1/cases/{case_id}/live-agent/{action}", json=command)
            assert failed.status_code == 503, failed.text
            stopped = client.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"]
            assert stopped["status"] == "needs_review"
            assert stopped["usage"]["calls"] == count
            assert stopped["can_retry"] is (count == 1)
            assert stopped["analysis"] == created["analysis"]
            assert stopped["answer"] is None
            assert len(boundary.resource.stages) == count
        rows = _stored_run(config, created["id"])
        rejected = client.post(f"/api/v1/cases/{case_id}/live-agent/retry", json=command)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "agent_run_not_executable"
        assert _stored_run(config, created["id"]) == rows
        assert client.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"] == stopped
        assert client.get(f"/api/v1/cases/{case_id}").json() == original_case
        connection = connect_database(config.database_path)
        try:
            row = connection.execute(
                "SELECT checkpoint_json, reserved_total_tokens, reserved_cost_minor "
                "FROM agent_stage_run WHERE run_id = ?",
                (created["id"],),
            ).fetchone()
            assert tuple(row) == (None, 0, 0)
        finally:
            connection.close()
    assert boundary.resource.stages == ["validate", "validate"]
    assert boundary.resource.inputs[0] == boundary.resource.inputs[1]


def test_interrupted_question_reservation_recovers_with_original_input_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient()
    question = "中断恢复后继续核对 alpha-7391 保修原文。"
    original_inject = StageRunner._inject_failure

    def interrupt(self: StageRunner, event: str) -> None:
        original_inject(self, event)
        if event == "after_stage_reservation":
            raise RuntimeError("Synthetic question process interruption")

    with TestClient(
        create_app(config, openai_client=boundary), raise_server_exceptions=False
    ) as client:
        case_id = _create_case_with_evidence(client)
        created = _authorize_question(client, case_id, question)
        with monkeypatch.context() as patch:
            patch.setattr(StageRunner, "_inject_failure", interrupt)
            interrupted = client.post(
                f"/api/v1/cases/{case_id}/live-agent/execute",
                json={"expected_run_id": created["id"]},
            )
        assert interrupted.status_code == 500
        stopped = client.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"]
        assert stopped["recovery_required"] is True
        assert stopped["analysis"] == created["analysis"]
        assert stopped["usage"]["calls"] == 1
        assert boundary.resource.stages == []
    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        recovered = restarted.post(
            f"/api/v1/cases/{case_id}/live-agent/recover", json={"expected_run_id": created["id"]}
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["run"]["analysis"] == created["analysis"]
        assert recovered.json()["run"]["can_retry"] is True
        completed = restarted.post(
            f"/api/v1/cases/{case_id}/live-agent/retry", json={"expected_run_id": created["id"]}
        )
        assert completed.status_code == 200, completed.text
        run = completed.json()["run"]
        assert run["status"] == "completed"
        assert run["usage"]["calls"] == 2
        assert run["usage"]["input_tokens"] == 2020
        assert run["usage"]["output_tokens"] == 1010
        assert run["analysis"] == created["analysis"]
    assert boundary.resource.stages == ["validate"]
    assert boundary.resource.inputs[0]["stage_payload"]["question"] == question


def test_fact_changed_during_adapter_call_stops_without_checkpoint_and_keeps_charge(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        claim = _corrected_product(client, case_id)
        question = "以当前型号核对 alpha-7391 保修说明。"
        created = _authorize_question(client, case_id, question)

        def change_fact() -> None:
            connection = connect_database(config.database_path)
            try:
                ClaimService(connection).correct_claim(
                    CorrectClaimCommand(
                        claim_id=claim["id"],
                        expected_version=claim["version"],
                        value=ClaimValue.from_input("调用期间修正 H10"),
                        actor="synthetic_concurrent_reviewer",
                        reason="合成并发修正",
                    )
                )
            finally:
                connection.close()

        boundary.resource.before_reply = change_fact
        failed = client.post(
            f"/api/v1/cases/{case_id}/live-agent/execute", json={"expected_run_id": created["id"]}
        )
        assert failed.status_code == 409, failed.text
        stopped = client.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"]
        assert stopped["id"] == created["id"]
        assert stopped["status"] == "needs_review"
        assert stopped["analysis"]["question"] == question
        assert stopped["analysis"]["basis_status"] == "changed"
        assert stopped["analysis"]["facts"] == created["analysis"]["facts"]
        assert stopped["can_retry"] is False
        assert stopped["answer"] is None
        assert stopped["last_completed_stage"] is None
        assert stopped["usage"]["calls"] == 1
        assert stopped["usage"]["input_tokens"] == 20
        assert stopped["usage"]["output_tokens"] == 10
        assert stopped["stages"][0]["error_code"] == "agent_integrity_failure"
        assert boundary.resource.stages == ["validate"]
        connection = connect_database(config.database_path)
        try:
            row = connection.execute(
                "SELECT checkpoint_json, reserved_total_tokens "
                "FROM agent_stage_run WHERE run_id = ?",
                (created["id"],),
            ).fetchone()
            assert tuple(row) == (None, 0)
        finally:
            connection.close()


@pytest.mark.parametrize("run_state", ["completed", "queued", "interrupted"])
def test_unavailable_material_keeps_question_history_readable_and_safe_to_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_state: str
) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient()
    question = "请保留本次问题和 alpha-7391 分析结果供之后回看。"
    original_inject = StageRunner._inject_failure

    def interrupt(self: StageRunner, event: str) -> None:
        original_inject(self, event)
        if event == "after_stage_reservation":
            raise RuntimeError("Synthetic question process interruption")

    with TestClient(
        create_app(config, openai_client=boundary), raise_server_exceptions=False
    ) as client:
        case_id = _create_case_with_evidence(client)
        created = _authorize_question(client, case_id, question)
        command = {"expected_run_id": created["id"]}
        if run_state == "completed":
            completed = client.post(f"/api/v1/cases/{case_id}/live-agent/execute", json=command)
            assert completed.status_code == 200, completed.text
        elif run_state == "interrupted":
            with monkeypatch.context() as patch:
                patch.setattr(StageRunner, "_inject_failure", interrupt)
                assert (
                    client.post(
                        f"/api/v1/cases/{case_id}/live-agent/execute", json=command
                    ).status_code
                    == 500
                )
        prior = client.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"]
        connection = connect_database(config.database_path)
        try:
            connection.execute(
                "UPDATE source_segment SET text = text || ' synthetic corruption' "
                "WHERE source_id IN (SELECT id FROM source WHERE case_id = ?)",
                (case_id,),
            )
        finally:
            connection.close()
        read = client.get(f"/api/v1/cases/{case_id}/live-agent")
        assert read.status_code == 200, read.text
        result = read.json()
        assert result["preview"] is None
        assert result["run"]["analysis"]["basis_status"] == "unavailable"
        assert result["run"]["analysis"]["question"] == question
        assert result["run"]["answer"] == prior["answer"]
        assert result["run"]["can_execute"] is False
        assert result["run"]["can_retry"] is False
        assert result["run_history"][0]["answer"] == prior["answer"]
        preview = client.post(
            f"/api/v1/cases/{case_id}/live-agent/preview", json={"question": question}
        )
        assert preview.status_code == 409, preview.text
        stored = _stored_run(config, created["id"])
        calls = list(boundary.resource.stages)
        for action in ("execute", "retry"):
            rejected = client.post(f"/api/v1/cases/{case_id}/live-agent/{action}", json=command)
            assert rejected.status_code == 409, rejected.text
            assert _stored_run(config, created["id"]) == stored
            assert boundary.resource.stages == calls
        if run_state in {"queued", "interrupted"}:
            action = "cancel" if run_state == "queued" else "recover"
            stopped = client.post(f"/api/v1/cases/{case_id}/live-agent/{action}", json=command)
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["preview"] is None
            assert stopped.json()["run"]["analysis"]["question"] == question
            assert stopped.json()["run"]["analysis"]["basis_status"] == "unavailable"
            assert stopped.json()["run"]["can_retry"] is False


@pytest.mark.parametrize("action", ["execute", "retry", "cancel", "recover"])
def test_older_tab_or_missing_run_id_cannot_operate_on_a_new_question(
    tmp_path: Path, action: str
) -> None:
    config = _config(tmp_path)
    boundary = _QuestionClient()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        prior = _authorize_question(client, case_id, "第一个问题：alpha-7391 保修原文？")
        completed = client.post(
            f"/api/v1/cases/{case_id}/live-agent/execute", json={"expected_run_id": prior["id"]}
        )
        assert completed.status_code == 200, completed.text
        current = _authorize_question(client, case_id, "第二个问题：重新核对 alpha-7391 材料。")
        before = _stored_run(config, current["id"])
        for body in ({"expected_run_id": prior["id"]}, None):
            result = client.post(f"/api/v1/cases/{case_id}/live-agent/{action}", json=body)
            assert result.status_code == 409, result.text
            assert result.json()["error"]["code"] == "live_agent_run_stale"
            assert _stored_run(config, current["id"]) == before
            assert client.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"] == current
        assert boundary.resource.stages == ["validate"]
        allowed = client.post(
            f"/api/v1/cases/{case_id}/live-agent/execute", json={"expected_run_id": current["id"]}
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["run"]["analysis"]["question"] == current["analysis"]["question"]
    assert boundary.resource.stages == ["validate", "validate"]
