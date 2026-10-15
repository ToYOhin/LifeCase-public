"""Read-only current facts use real import and Claim decision boundaries."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lifecase.claims.current_facts import CurrentFactsPersistenceError, CurrentFactsService
from lifecase.claims.models import ClaimCreatedBy, ClaimStatus, ClaimValue, CreateClaimCommand
from lifecase.claims.service import ClaimService
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.storage import connect_database


@pytest.mark.parametrize(
    ("case_type", "field"),
    [
        ("warranty", "product_name"),
        ("return_refund", "merchant"),
        ("reimbursement", "amount"),
        ("subscription_cancellation", "order_or_reference"),
    ],
)
def test_current_fact_exposes_stored_value_hash_for_version_bound_actions(
    tmp_path: Path, case_type: str, field: str
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/api/v1/cases",
            json={"title": "合成跨场景事实", "goal": "绑定既有行动", "template_id": case_type},
        )
        assert response.status_code == 201
        case_id = response.json()["id"]
        claim = _candidate(client, case_id, field=field, value="200.0")
        _confirm(client, claim)
        corrected_value: str | int = 200 if field == "amount" else "合成修正值"
        assert (
            client.post(
                f"/api/v1/claims/{claim['id']}/correct",
                json={
                    "expected_version": 2,
                    "value": corrected_value,
                    "actor": "synthetic_operator",
                    "reason": "核实合成修正值并保留原单位",
                },
            ).status_code
            == 200
        )
        before = _snapshot(config)
        fact = _facts(client, case_id)["items"][0]
        assert fact["claim"]["version"] == 3
        assert fact["value_sha256"] == ClaimValue.from_input(corrected_value).sha256
        assert fact["citations"][0]["quote_text"] == "200.0"
        assert _snapshot(config) == before


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _case(client: TestClient, title: str = "合成家电案件") -> str:
    response = client.post(
        "/api/v1/cases",
        json={"title": title, "goal": "准备报修材料", "template_id": "warranty"},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _candidate(
    client: TestClient,
    case_id: str,
    *,
    value: str = "海星 H8",
    field: str = "product_name",
) -> dict[str, object]:
    text = f"🧾 合成材料：{value}。Warranty service record."
    imported = client.post(
        f"/api/v1/cases/{case_id}/sources",
        files={"file": (f"{field}-receipt.txt", text.encode(), "text/plain")},
    )
    assert imported.status_code == 201
    segments = client.get(f"/api/v1/cases/{case_id}/source-segments").json()
    segment = next(item for item in segments if item["source_id"] == imported.json()["id"])
    start = segment["text"].index(value)
    response = client.post(
        f"/api/v1/cases/{case_id}/claims",
        json={
            "field": field,
            "value": value,
            "segment_id": segment["id"],
            "quote_start": start,
            "quote_end": start + len(value),
            "actor": "local_user",
            "reason": "从合成原文登记事实",
        },
    )
    assert response.status_code == 201
    return response.json()


def _confirm(client: TestClient, claim: dict[str, object], basis: str = "evidence") -> None:
    response = client.post(
        f"/api/v1/claims/{claim['id']}/confirm",
        json={
            "expected_version": claim["version"],
            "basis": basis,
            "actor": "local_user",
            "reason": "本人核对了合成材料",
        },
    )
    assert response.status_code == 200


def _facts(client: TestClient, case_id: str) -> dict[str, object]:
    response = client.get(f"/api/v1/cases/{case_id}/current-facts")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _snapshot(config: ApplicationConfig) -> tuple[str, ...]:
    connection = connect_database(config.database_path)
    try:
        return tuple(connection.iterdump())
    finally:
        connection.close()


def test_current_facts_retains_confirmed_authority_and_is_read_only_after_restart(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        claim = _candidate(client, case_id)
        _confirm(client, claim)
        assert client.get(f"/api/v1/cases/{case_id}/evidence-review").json()["items"] == []
        before = _snapshot(config)
        result = _facts(client, case_id)
        assert _snapshot(config) == before
        assert result["case_id"] == case_id
        assert result["case_type"] == "warranty"
        item = result["items"][0]
        assert item["claim"]["status"] == "confirmed"
        assert item["claim"]["value"] == "海星 H8"
        assert item["confirmation_basis"] == "evidence"
        assert item["confirmation"]["reason"] == "本人核对了合成材料"
        assert item["usable_as_fact"] is True
        assert item["conflict"] is False
        assert item["evidence_status"] == "valid"
        citation = item["citations"][0]
        assert citation["source_name"] == "product_name-receipt.txt"
        assert citation["quote_text"] == "海星 H8"
        assert citation["text"][citation["quote_start"] : citation["quote_end"]] == "海星 H8"
        assert "🧾" in citation["text"]
        assert "object_path" not in str(result)
        assert str(tmp_path) not in str(result)

    with TestClient(create_app(config)) as restarted:
        assert _facts(restarted, case_id) == result


def test_current_facts_exposes_candidate_correction_and_rejection_without_stale_value(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        claim = _candidate(client, case_id, value="Original H8")
        initial = _facts(client, case_id)["items"][0]
        assert initial["usable_as_fact"] is False
        assert initial["confirmation_basis"] is None
        corrected = client.post(
            f"/api/v1/claims/{claim['id']}/correct",
            json={
                "expected_version": 1,
                "value": "本人核实的 H9",
                "actor": "local_user",
                "reason": "本人核对机身铭牌后修正型号",
            },
        )
        assert corrected.status_code == 200
        updated = _facts(client, case_id)["items"][0]
        assert updated["claim"]["value"] == "本人核实的 H9"
        assert updated["confirmation_basis"] == "correction"
        assert updated["confirmation"]["reason"] == "本人核对机身铭牌后修正型号"
        assert updated["usable_as_fact"] is True
        # Original source remains original; the projection does not pretend it states H9.
        assert updated["citations"][0]["quote_text"] == "Original H8"
        rejected = client.post(
            f"/api/v1/claims/{claim['id']}/reject",
            json={"expected_version": 2, "actor": "local_user", "reason": "此条不再采用"},
        )
        assert rejected.status_code == 200
        final = _facts(client, case_id)["items"][0]
        assert final["claim"]["status"] == "rejected"
        assert final["usable_as_fact"] is False
        assert final["confirmation_basis"] is None


@pytest.mark.parametrize(
    ("field", "original_value", "corrected_value", "final_value"),
    [
        ("product_name", "合成 H8", "本人核实的 H9", "本人核实的 H10"),
        ("purchase_date", "2026-08-01", "2026-08-02", "2026-08-03"),
    ],
)
def test_settled_fact_corrections_preserve_history_scope_and_restart(
    tmp_path: Path, field: str, original_value: str, corrected_value: str, final_value: str
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        other_id = _case(client, "独立的合成案件")
        claim = _candidate(client, case_id, field=field, value=original_value)
        other_claim = _candidate(client, other_id, field=field, value=original_value)
        _confirm(client, claim)
        _confirm(client, other_claim)
        assert client.get(f"/api/v1/cases/{case_id}/evidence-review").json()["items"] == []
        original_fact = _facts(client, case_id)["items"][0]
        other_facts = _facts(client, other_id)

        response = client.post(
            f"/api/v1/claims/{claim['id']}/correct",
            json={
                "expected_version": original_fact["claim"]["version"],
                "value": corrected_value,
                "actor": "synthetic_reviewer",
                "reason": "本人再次核对后修正已确认事实",
            },
        )
        assert response.status_code == 200, response.text
        corrected = _facts(client, case_id)["items"][0]
        assert corrected["claim"] == response.json()
        assert corrected["claim"]["value"] == corrected_value
        assert corrected["claim"]["status"] == "corrected"
        assert corrected["claim"]["version"] == 3
        assert corrected["confirmation_basis"] == "correction"
        assert corrected["confirmation"]["actor"] == "synthetic_reviewer"
        assert corrected["confirmation"]["reason"] == "本人再次核对后修正已确认事实"
        assert corrected["usable_as_fact"] is True
        # A personal correction does not rewrite or mislabel the imported quote.
        assert corrected["citations"] == original_fact["citations"]
        assert corrected["citations"][0]["quote_text"] == original_value
        assert client.get(f"/api/v1/cases/{case_id}/evidence-review").json()["items"] == []

        before_rejected_writes = _snapshot(config)
        stale = client.post(
            f"/api/v1/claims/{claim['id']}/correct",
            json={
                "expected_version": 2,
                "value": "陈旧页面中的错误覆盖值",
                "actor": "stale_reviewer",
                "reason": "模拟另一页面的旧版本请求",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "claim_version_conflict"
        assert stale.json()["error"]["details"] == {"current_version": 3}
        duplicate = client.post(
            f"/api/v1/claims/{claim['id']}/correct",
            json={
                "expected_version": 3,
                "value": corrected_value,
                "actor": "synthetic_reviewer",
                "reason": "模拟重复保存同一值",
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "claim_transition_not_allowed"
        assert _snapshot(config) == before_rejected_writes
        assert _facts(client, case_id)["items"][0] == corrected

        second = client.post(
            f"/api/v1/claims/{claim['id']}/correct",
            json={
                "expected_version": 3,
                "value": final_value,
                "actor": "second_synthetic_reviewer",
                "reason": "再次核实后保留第二次修正历史",
            },
        )
        assert second.status_code == 200, second.text
        final_facts = _facts(client, case_id)
        final = final_facts["items"][0]
        assert final["claim"]["value"] == final_value
        assert final["claim"]["version"] == 4
        assert final["confirmation"]["actor"] == "second_synthetic_reviewer"
        assert final["confirmation"]["reason"] == "再次核实后保留第二次修正历史"
        assert final["citations"] == original_fact["citations"]
        assert _facts(client, other_id) == other_facts
        connection = connect_database(config.database_path)
        try:
            service = ClaimService(connection)
            history = service.list_claim_events(str(claim["id"]))
            assert [event.event_type.value for event in history] == [
                "created",
                "confirmed",
                "corrected",
                "corrected",
            ]
            assert history[2].prior_value is not None
            assert history[2].prior_value.to_python() == original_value
            assert history[2].new_value.to_python() == corrected_value
            assert (history[2].prior_version, history[2].new_version) == (2, 3)
            assert history[2].actor == "synthetic_reviewer"
            assert history[2].reason == "本人再次核对后修正已确认事实"
            assert history[3].prior_value == history[2].new_value
            assert history[3].new_value.to_python() == final_value
            assert (history[3].prior_version, history[3].new_version) == (3, 4)
            confirmations = service.list_claim_confirmations(str(claim["id"]))
            assert len(confirmations) == 1
            assert confirmations[0].claim_version == 2
            assert confirmations[0].reason == "本人核对了合成材料"
        finally:
            connection.close()

    with TestClient(create_app(config)) as restarted:
        assert _facts(restarted, case_id) == final_facts
        assert _facts(restarted, other_id) == other_facts
        assert restarted.get(f"/api/v1/cases/{case_id}/evidence-review").json()["items"] == []
        connection = connect_database(config.database_path)
        try:
            assert ClaimService(connection).list_claim_events(str(claim["id"])) == history
        finally:
            connection.close()


@pytest.mark.parametrize("metadata", ["actor", "reason", "expected_version"])
def test_settled_fact_correction_requires_audit_and_version_metadata(
    tmp_path: Path, metadata: str
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        claim = _candidate(client, case_id)
        _confirm(client, claim)
        payload = {
            "expected_version": 2,
            "value": "本人核实的 H9",
            "actor": "local_user",
            "reason": "本人核对后修正",
        }
        del payload[metadata]
        before = _snapshot(config)
        response = client.post(f"/api/v1/claims/{claim['id']}/correct", json=payload)
        assert response.status_code == 422
        assert _snapshot(config) == before
        fact = _facts(client, case_id)["items"][0]
        assert fact["claim"]["value"] == claim["value"]
        assert fact["claim"]["version"] == 2
        assert fact["confirmation_basis"] == "evidence"


def test_current_facts_marks_conflict_across_settled_and_candidate_values(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        case_id = _case(client)
        first = _candidate(client, case_id, value="H8")
        _confirm(client, first)
        second = _candidate(client, case_id, value="H9")
        items = _facts(client, case_id)["items"]
        assert all(item["conflict"] is True for item in items)
        assert all(item["usable_as_fact"] is False for item in items)
        _confirm(client, second)
        assert all(item["conflict"] is True for item in _facts(client, case_id)["items"])
        rejected = client.post(
            f"/api/v1/claims/{second['id']}/reject",
            json={"expected_version": 2, "actor": "local_user", "reason": "排除错误型号"},
        )
        assert rejected.status_code == 200
        items = _facts(client, case_id)["items"]
        assert all(item["conflict"] is False for item in items)
        assert [item["claim"]["value"] for item in items if item["usable_as_fact"]] == ["H8"]


def test_current_facts_keeps_source_free_explicit_user_basis_and_inference_separate(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        connection = connect_database(config.database_path)
        try:
            service = ClaimService(connection)
            manual = service.create_claim(
                CreateClaimCommand(
                    case_id=case_id,
                    field="product_name",
                    value=ClaimValue.from_input("本人核实的型号"),
                    status=ClaimStatus.CANDIDATE,
                    confidence=None,
                    created_by=ClaimCreatedBy.USER,
                    actor="local_user",
                    reason="登记本人明确知道的合成事实",
                )
            )
            service.create_claim(
                CreateClaimCommand(
                    case_id=case_id,
                    field="order_number",
                    value=ClaimValue.from_input("INFERRED-001"),
                    status=ClaimStatus.INFERRED,
                    confidence=0.5,
                    created_by=ClaimCreatedBy.RULE,
                    actor="test_rule",
                    reason="静态测试推断；不调用模型",
                )
            )
        finally:
            connection.close()
        _confirm(client, {"id": manual.id, "version": manual.version}, "user")
        items = _facts(client, case_id)["items"]
        confirmed = next(item for item in items if item["claim"]["id"] == manual.id)
        assert confirmed["confirmation_basis"] == "user"
        assert confirmed["evidence_status"] == "none"
        assert confirmed["citations"] == []
        assert confirmed["usable_as_fact"] is True
        inferred = next(item for item in items if item["claim"]["status"] == "inferred")
        assert inferred["usable_as_fact"] is False


@pytest.mark.parametrize("damage", ["text", "cross_case"])
def test_current_facts_degrades_invalid_source_per_fact_without_exposing_foreign_text(
    tmp_path: Path, damage: str
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        other_id = _case(client, "另一合成案件")
        claim = _candidate(client, case_id)
        healthy = _candidate(client, case_id, value="2026-08-01", field="purchase_date")
        foreign = _candidate(client, other_id, value="FOREIGN-PRIVATE-TEXT")
        _confirm(client, claim)
        _confirm(client, healthy)
        connection = connect_database(config.database_path)
        try:
            if damage == "text":
                connection.execute(
                    "UPDATE source_segment SET text = 'tampered' WHERE id IN "
                    "(SELECT segment_id FROM claim_source WHERE claim_id = ?)",
                    (claim["id"],),
                )
            else:
                source = connection.execute(
                    "SELECT source_id, segment_id FROM claim_source WHERE claim_id = ?",
                    (foreign["id"],),
                ).fetchone()
                connection.execute(
                    "UPDATE claim_source SET source_id = ?, segment_id = ? WHERE claim_id = ?",
                    (source["source_id"], source["segment_id"], claim["id"]),
                )
        finally:
            connection.close()
        result = _facts(client, case_id)
        damaged = next(item for item in result["items"] if item["claim"]["id"] == claim["id"])
        assert damaged["claim"]["status"] == "confirmed"
        assert damaged["evidence_status"] == "unavailable"
        assert damaged["citations"] == []
        assert damaged["usable_as_fact"] is False
        assert (
            next(item for item in result["items"] if item["claim"]["id"] == healthy["id"])[
                "usable_as_fact"
            ]
            is True
        )
        assert "FOREIGN-PRIVATE-TEXT" not in str(result)
        corrected = client.post(
            f"/api/v1/claims/{claim['id']}/correct",
            json={
                "expected_version": 2,
                "value": "本人补充核实的 H9",
                "actor": "local_user",
                "reason": "人工修正不得恢复损坏引用的权威",
            },
        )
        assert corrected.status_code == 200
        after_correction = _facts(client, case_id)
        still_unavailable = next(
            item for item in after_correction["items"] if item["claim"]["id"] == claim["id"]
        )
        assert still_unavailable["claim"]["value"] == "本人补充核实的 H9"
        assert still_unavailable["confirmation_basis"] == "correction"
        assert still_unavailable["evidence_status"] == "unavailable"
        assert still_unavailable["citations"] == []
        assert still_unavailable["usable_as_fact"] is False
        assert "FOREIGN-PRIVATE-TEXT" not in str(after_correction)


def test_current_facts_empty_missing_and_other_case_are_explicit(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        first = _case(client)
        second = _case(client, "空案件")
        claim = _candidate(client, first)
        _confirm(client, claim)
        assert _facts(client, second) == {"case_id": second, "case_type": "warranty", "items": []}
        missing = client.get("/api/v1/cases/case_missing/current-facts")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "case_not_found"


def test_current_facts_uses_normalized_comparison_and_requires_recorded_confirmation(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        first = _candidate(client, case_id, value="H8")
        second = _candidate(client, case_id, value="h8")
        _confirm(client, first)
        _confirm(client, second)
        assert all(item["conflict"] is False for item in _facts(client, case_id)["items"])
        connection = connect_database(config.database_path)
        try:
            connection.execute("DELETE FROM claim_confirmation WHERE claim_id = ?", (first["id"],))
        finally:
            connection.close()
        damaged = next(
            item for item in _facts(client, case_id)["items"] if item["claim"]["id"] == first["id"]
        )
        assert damaged["claim"]["status"] == "confirmed"
        assert damaged["confirmation_basis"] is None
        assert damaged["usable_as_fact"] is False


def test_current_facts_maps_storage_failure_without_private_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_read(_self: CurrentFactsService, _case_id: str) -> None:
        raise CurrentFactsPersistenceError("SELECT private FROM secret_table")

    with TestClient(create_app(_config(tmp_path))) as client:
        case_id = _case(client)
        monkeypatch.setattr(CurrentFactsService, "list_case_facts", fail_read)
        response = client.get(f"/api/v1/cases/{case_id}/current-facts")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "current_facts_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert "SELECT" not in response.text
    assert "secret_table" not in response.text


def test_current_facts_does_not_rollback_callers_transaction(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)
        connection = connect_database(config.database_path)
        try:
            connection.execute("BEGIN")
            with pytest.raises(CurrentFactsPersistenceError):
                CurrentFactsService(connection).list_case_facts(case_id)
            assert connection.in_transaction is True
            connection.execute("ROLLBACK")
        finally:
            connection.close()
