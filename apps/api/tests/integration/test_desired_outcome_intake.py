"""Explicit user-outcome intake keeps saved intent and fact confirmation separate."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lifecase.claims.models import (
    ClaimConfirmationBasis,
    ClaimCreatedBy,
    ClaimStatus,
    ClaimValue,
    ConfirmClaimCommand,
    CorrectClaimCommand,
    CreateClaimCommand,
    RejectClaimCommand,
)
from lifecase.claims.service import ClaimService
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.storage import connect_database


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _case(client: TestClient, case_type: str = "warranty") -> str:
    response = client.post(
        "/api/v1/cases",
        json={
            "title": "合成本人诉求",
            "goal": "案件目标不可代替本人诉求",
            "template_id": case_type,
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _save_intent(
    client: TestClient, case_id: str, intent: str = "请先告知检测流程。\n未经确认不接受付费维修。"
) -> dict[str, Any]:
    response = client.put(
        f"/api/v1/cases/{case_id}/preparation-intent",
        json={"intent": intent, "expected_version": 0},
    )
    assert response.status_code == 200
    return dict(response.json())


def _url(case_id: str) -> str:
    return f"/api/v1/cases/{case_id}/desired-outcome"


def _command(version: int = 1) -> dict[str, object]:
    return {
        "expected_intent_version": version,
        "actor": "synthetic_operator",
        "reason": "本人从已保存诉求登记候选，仍需单独确认",
    }


def _snapshot(config: ApplicationConfig, *, include_claims: bool = True) -> dict[str, object]:
    tables = [
        "case_record",
        "case_transition",
        "warranty_preparation_intent",
        "source",
        "source_segment",
        "source_extraction",
        "artifact",
        "action",
        "action_event",
        "action_progress",
        "approval_request",
        "approval_event",
        "agent_run",
        "agent_stage_run",
        "deadline",
        "deadline_basis_claim",
        "deadline_event",
        "claim_source",
    ]
    if include_claims:
        tables.extend(("claim", "claim_event", "claim_confirmation"))
    connection = connect_database(config.database_path)
    try:
        return {
            table: tuple(
                tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
            )
            for table in tables
        }
    finally:
        connection.close()


def _old_outcome(config: ApplicationConfig, case_id: str, status: str) -> str:
    connection = connect_database(config.database_path)
    try:
        service = ClaimService(connection)
        claim = service.create_claim(
            CreateClaimCommand(
                case_id=case_id,
                field="desired_outcome",
                value=ClaimValue.from_input("旧诉求不可覆盖"),
                status=ClaimStatus.INFERRED if status == "inferred" else ClaimStatus.CANDIDATE,
                confidence=None,
                created_by=ClaimCreatedBy.USER,
                actor="synthetic_prior_operator",
                reason="保留既有诉求与历史",
            )
        )
        if status == "confirmed":
            service.confirm_claim(
                ConfirmClaimCommand(
                    claim_id=claim.id,
                    expected_version=1,
                    basis=ClaimConfirmationBasis.USER,
                    actor="synthetic_prior_operator",
                    reason="本人已明确确认旧诉求",
                )
            )
        elif status == "corrected":
            service.correct_claim(
                CorrectClaimCommand(
                    claim_id=claim.id,
                    expected_version=1,
                    value=ClaimValue.from_input("修正后的旧诉求不可覆盖"),
                    actor="synthetic_prior_operator",
                    reason="保留先前修正历史",
                )
            )
        elif status == "rejected":
            service.reject_claim(
                RejectClaimCommand(
                    claim_id=claim.id,
                    expected_version=1,
                    actor="synthetic_prior_operator",
                    reason="本人撤回旧诉求",
                )
            )
        return claim.id
    finally:
        connection.close()


def _seed_independent_authorities(client: TestClient, case_id: str) -> None:
    text = "合成票据：商家为 SYNTHETIC STORE。"
    imported = client.post(
        f"/api/v1/cases/{case_id}/sources",
        files={"file": ("synthetic-receipt.txt", text.encode(), "text/plain")},
    )
    assert imported.status_code == 201
    segment = client.get(f"/api/v1/cases/{case_id}/source-segments").json()[0]
    quote = "SYNTHETIC STORE"
    start = segment["text"].index(quote)
    claim = client.post(
        f"/api/v1/cases/{case_id}/claims",
        json={
            "field": "merchant_or_manufacturer",
            "value": quote,
            "segment_id": segment["id"],
            "quote_start": start,
            "quote_end": start + len(quote),
            "actor": "synthetic_operator",
            "reason": "保留已有证据和另一字段候选",
        },
    )
    assert claim.status_code == 201
    action = client.post(
        f"/api/v1/cases/{case_id}/actions",
        json={
            "kind": "collect_evidence",
            "risk_level": "B",
            "payload": {"requirement_key": "purchase_proof", "requirement_type": "evidence"},
            "requirements": [],
            "dependencies": [],
            "deadline": None,
            "actor": "synthetic_operator",
            "reason": "保留已有本地行动",
        },
    )
    assert action.status_code == 201
    action_record = action.json()
    validated = client.post(
        f"/api/v1/actions/{action_record['id']}/revalidate",
        json={
            "expected_version": action_record["version"],
            "actor": "synthetic_operator",
            "reason": "准备精确审批",
        },
    )
    assert validated.status_code == 200
    action_record = validated.json()
    approval = client.post(
        f"/api/v1/actions/{action_record['id']}/approval-requests",
        json={
            "expected_action_version": action_record["version"],
            "payload_sha256": action_record["payload_sha256"],
            "summary": "合成本地行动精确审批",
            "actor": "synthetic_operator",
            "reason": "本人诉求登记不得改变既有审批",
        },
    )
    assert approval.status_code == 201


def test_saved_intent_does_not_automatically_create_or_confirm_a_claim(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        before = _snapshot(config)
        saved = _save_intent(client, case_id)
        after = _snapshot(config)
        assert after.pop("warranty_preparation_intent") != before.pop("warranty_preparation_intent")
        assert after == before
        assert saved["version"] == 1
        assert client.get(f"/api/v1/cases/{case_id}/current-facts").json()["items"] == []


def test_candidate_requires_explicit_user_confirmation_and_preserves_other_authorities(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        _seed_independent_authorities(client, case_id)
        saved = _save_intent(client, case_id)
        before = _snapshot(config, include_claims=False)
        prior_facts = client.get(f"/api/v1/cases/{case_id}/current-facts").json()["items"]
        created = client.post(_url(case_id), json=_command())
        assert created.status_code == 201
        claim = created.json()
        assert (claim["case_id"], claim["field"], claim["value"]) == (
            case_id,
            "desired_outcome",
            saved["intent"],
        )
        assert (claim["status"], claim["created_by"], claim["version"], claim["confidence"]) == (
            "candidate",
            "user",
            1,
            None,
        )
        assert claim["contradiction"] is False
        fact = next(
            item
            for item in client.get(f"/api/v1/cases/{case_id}/current-facts").json()["items"]
            if item["claim"]["id"] == claim["id"]
        )
        assert fact["usable_as_fact"] is False
        assert fact["confirmation_basis"] is fact["confirmation"] is None
        assert fact["citations"] == []
        assert _snapshot(config, include_claims=False) == before
        snapshot = _snapshot(config)
        evidence = client.post(
            f"/api/v1/claims/{claim['id']}/confirm",
            json={
                "expected_version": 1,
                "basis": "evidence",
                "actor": "synthetic_reviewer",
                "reason": "不能伪造原文依据",
            },
        )
        assert (evidence.status_code, evidence.json()["error"]["code"]) == (
            409,
            "claim_source_required",
        )
        assert _snapshot(config) == snapshot

    with TestClient(create_app(config)) as restarted:
        confirm = {
            "expected_version": 1,
            "basis": "user",
            "actor": "synthetic_reviewer",
            "reason": "本人逐字核对并明确确认诉求",
        }
        confirmed = restarted.post(f"/api/v1/claims/{claim['id']}/confirm", json=confirm)
        assert confirmed.status_code == 200
        assert (confirmed.json()["status"], confirmed.json()["version"]) == ("confirmed", 2)
        fact = next(
            item
            for item in restarted.get(f"/api/v1/cases/{case_id}/current-facts").json()["items"]
            if item["claim"]["id"] == claim["id"]
        )
        assert fact["usable_as_fact"] is True
        assert fact["confirmation_basis"] == "user"
        assert fact["confirmation"]["actor"] == confirm["actor"]
        assert fact["confirmation"]["reason"] == confirm["reason"]
        assert fact["citations"] == []
        assert [
            item
            for item in restarted.get(f"/api/v1/cases/{case_id}/current-facts").json()["items"]
            if item["claim"]["id"] != claim["id"]
        ] == prior_facts
        assert restarted.get(f"/api/v1/cases/{case_id}/preparation-intent").json() == saved
        assert _snapshot(config, include_claims=False) == before
    connection = connect_database(config.database_path)
    try:
        service = ClaimService(connection)
        events = service.list_claim_events(claim["id"])
        assert [event.event_type.value for event in events] == ["created", "confirmed"]
        assert events[0].actor == _command()["actor"]
        assert events[0].reason == _command()["reason"]
        assert service.list_claim_sources(claim["id"]) == ()
    finally:
        connection.close()


@pytest.mark.parametrize("old_status", ("candidate", "inferred", "confirmed", "corrected"))
def test_non_rejected_outcome_is_never_overwritten_or_duplicated(
    tmp_path: Path,
    old_status: str,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        _save_intent(client, case_id)
        _old_outcome(config, case_id, old_status)
        before = _snapshot(config)
        response = client.post(_url(case_id), json=_command())
        assert (response.status_code, response.json()["error"]["code"]) == (
            409,
            "desired_outcome_conflict",
        )
        assert _snapshot(config) == before


def test_rejected_outcome_retains_history_and_allows_a_new_candidate(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id, other_id = _case(client), _case(client)
        saved = _save_intent(client, case_id)
        old_id = _old_outcome(config, case_id, "rejected")
        _old_outcome(config, other_id, "confirmed")
        connection = connect_database(config.database_path)
        try:
            old_record = ClaimService(connection).get_claim(old_id)
            old_events = ClaimService(connection).list_claim_events(old_id)
        finally:
            connection.close()
        created = client.post(_url(case_id), json=_command())
        assert created.status_code == 201
        assert created.json()["id"] != old_id
        assert created.json()["value"] == saved["intent"]
        before_retry = _snapshot(config)
        retry = client.post(_url(case_id), json=_command())
        assert (retry.status_code, retry.json()["error"]["code"]) == (
            409,
            "desired_outcome_conflict",
        )
        assert _snapshot(config) == before_retry
        connection = connect_database(config.database_path)
        try:
            assert ClaimService(connection).get_claim(old_id) == old_record
            assert ClaimService(connection).list_claim_events(old_id) == old_events
        finally:
            connection.close()


def test_stale_intent_version_cannot_register_old_or_replacement_text(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        _save_intent(client, case_id, "旧页面保存的诉求")
        saved = client.put(
            f"/api/v1/cases/{case_id}/preparation-intent",
            json={"intent": "最新版本人诉求", "expected_version": 1},
        )
        assert saved.status_code == 200
        before = _snapshot(config)
        response = client.post(_url(case_id), json=_command())
        assert (response.status_code, response.json()["error"]["code"]) == (
            409,
            "preparation_intent_version_conflict",
        )
        assert response.json()["error"]["details"] == {"current_version": 2}
        assert _snapshot(config) == before
        created = client.post(_url(case_id), json=_command(2))
        assert created.status_code == 201
        assert created.json()["value"] == "最新版本人诉求"


@pytest.mark.parametrize("intent", ("", " \t\n", "诉" * 2000))
def test_empty_or_claim_incompatible_saved_text_does_not_register(
    tmp_path: Path, intent: str
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        _save_intent(client, case_id, intent)
        before = _snapshot(config)
        response = client.post(_url(case_id), json=_command())
        assert (response.status_code, response.json()["error"]["code"]) == (422, "claim_invalid")
        assert _snapshot(config) == before


def test_missing_intent_case_or_unsupported_case_does_not_register(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        saved_case = _case(client)
        _save_intent(client, saved_case)
        empty_case = _case(client)
        unsupported_case = _case(client, "return_refund")
        before = _snapshot(config)
        for case_id, status, code in (
            (empty_case, 422, "claim_invalid"),
            (unsupported_case, 422, "claim_invalid"),
            ("case_missing", 404, "case_not_found"),
        ):
            response = client.post(_url(case_id), json=_command())
            assert (response.status_code, response.json()["error"]["code"]) == (status, code)
            assert _snapshot(config) == before


@pytest.mark.parametrize("terminal", ("resolved", "abandoned", "archived"))
def test_terminal_case_does_not_register_outcome(tmp_path: Path, terminal: str) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        _save_intent(client, case_id)
        targets = ("ready", "in_progress", "resolved") if terminal == "resolved" else ("abandoned",)
        if terminal == "archived":
            targets += ("archived",)
        version = 1
        for target in targets:
            transitioned = client.post(
                f"/api/v1/cases/{case_id}/transitions",
                json={
                    "expected_version": version,
                    "target_status": target,
                    "actor": "synthetic_operator",
                    "reason": "合成终结案件",
                },
            )
            assert transitioned.status_code == 200
            version = transitioned.json()["new_version"]
        before = _snapshot(config)
        response = client.post(_url(case_id), json=_command())
        assert (response.status_code, response.json()["error"]["code"]) == (422, "claim_invalid")
        assert _snapshot(config) == before


@pytest.mark.parametrize(
    "changes",
    (
        {"expected_intent_version": 0},
        {"expected_intent_version": -1},
        {"expected_intent_version": True},
        {"expected_intent_version": "1"},
        {"expected_intent_version": 1.0},
        {"value": "不能以请求文本替换已保存诉求"},
        {"field": "purchase_date"},
        {"case_id": "case_other"},
        {"sources": []},
        {"actor": ""},
        {"reason": ""},
    ),
)
def test_request_contract_rejects_versions_and_value_replacement(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        _save_intent(client, case_id)
        before = _snapshot(config)
        response = client.post(_url(case_id), json={**_command(), **changes})
        assert (response.status_code, response.json()["error"]["code"]) == (422, "validation_error")
        assert _snapshot(config) == before


@pytest.mark.parametrize("field", ("actor", "reason"))
def test_blank_decision_metadata_cannot_create_candidate(tmp_path: Path, field: str) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        _save_intent(client, case_id)
        before = _snapshot(config)
        response = client.post(_url(case_id), json={**_command(), field: " \t\n"})
        assert (response.status_code, response.json()["error"]["code"]) == (422, "claim_invalid")
        assert _snapshot(config) == before


def test_concurrent_posts_have_one_candidate_and_one_conflict(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        saved = _save_intent(client, case_id)

        def register() -> tuple[int, dict[str, Any]]:
            response = client.post(_url(case_id), json=_command())
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: register(), range(2)))
        assert sorted(result[0] for result in results) == [201, 409]
        conflict = next(result[1] for result in results if result[0] == 409)
        assert conflict["error"]["code"] == "desired_outcome_conflict"
        facts = client.get(f"/api/v1/cases/{case_id}/current-facts").json()["items"]
        assert len(facts) == 1
        assert facts[0]["claim"]["value"] == saved["intent"]
        assert facts[0]["claim"]["status"] == "candidate"
        assert facts[0]["usable_as_fact"] is False


def test_concurrent_intent_save_and_registration_use_one_locked_version(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        _save_intent(client, case_id, "初版本人诉求")

        def register() -> tuple[int, dict[str, Any]]:
            response = client.post(_url(case_id), json=_command())
            return response.status_code, response.json()

        def update() -> int:
            response = client.put(
                f"/api/v1/cases/{case_id}/preparation-intent",
                json={"intent": "下一版本人诉求", "expected_version": 1},
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            registering = executor.submit(register)
            saving = executor.submit(update)
            status, result = registering.result()
            assert saving.result() == 200
        facts = client.get(f"/api/v1/cases/{case_id}/current-facts").json()["items"]
        if status == 201:
            assert result["value"] == "初版本人诉求"
            assert len(facts) == 1
            assert facts[0]["claim"]["value"] == "初版本人诉求"
        else:
            assert status == 409
            assert result["error"]["code"] == "preparation_intent_version_conflict"
            assert result["error"]["details"] == {"current_version": 2}
            assert facts == []
        saved = client.get(f"/api/v1/cases/{case_id}/preparation-intent").json()
        assert (saved["intent"], saved["version"]) == ("下一版本人诉求", 2)


def test_history_failure_rolls_back_claim_and_can_retry(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        _save_intent(client, case_id)
        connection = connect_database(config.database_path)
        try:
            connection.execute(
                """
                CREATE TRIGGER fail_desired_outcome_history
                BEFORE INSERT ON claim_event
                BEGIN SELECT RAISE(ABORT, 'synthetic history failure'); END
                """
            )
        finally:
            connection.close()
        before = _snapshot(config)
        response = client.post(_url(case_id), json=_command())
        assert (response.status_code, response.json()["error"]["code"]) == (
            503,
            "claim_storage_failed",
        )
        assert "synthetic history failure" not in response.text
        assert _snapshot(config) == before
        connection = connect_database(config.database_path)
        try:
            connection.execute("DROP TRIGGER fail_desired_outcome_history")
        finally:
            connection.close()
        retry = client.post(_url(case_id), json=_command())
        assert retry.status_code == 201
        assert retry.json()["status"] == "candidate"
