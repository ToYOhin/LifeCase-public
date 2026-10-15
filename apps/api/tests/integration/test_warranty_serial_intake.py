"""Serial-number intake uses existing evidence, decision, and intent authorities."""

from pathlib import Path

from fastapi.testclient import TestClient

from lifecase.claims import ClaimService
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.storage import connect_database


def test_serial_intake_requires_review_and_preserves_intent_history_and_restart(
    tmp_path: Path,
) -> None:
    config = ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )
    with TestClient(create_app(config)) as client:
        cases = []
        for title in ("合成洗衣机序列号补齐", "独立合成案件"):
            created = client.post(
                "/api/v1/cases",
                json={"title": title, "goal": "核对铭牌", "template_id": "warranty"},
            )
            assert created.status_code == 201
            cases.append(created.json())
        case_id, other_id = (case["id"] for case in cases)
        url = f"/api/v1/cases/{case_id}"
        text = "🧾 合成铭牌记录：序列号 SYN-WASH-009，故障为排水异常。"
        imported = client.post(
            f"{url}/sources",
            files={"file": ("synthetic-nameplate.txt", text.encode(), "text/plain")},
        )
        assert imported.status_code == 201
        segment = client.get(f"{url}/source-segments").json()[0]
        original = "SYN-WASH-009"
        start = segment["text"].index(original)
        intent = client.put(
            f"{url}/preparation-intent",
            json={"intent": "请先告知检测流程。\n未经确认不接受付费维修。", "expected_version": 0},
        )
        assert intent.status_code == 200
        before_case = client.get(url).json()
        command = {
            "field": "serial_number",
            "value": original,
            "segment_id": segment["id"],
            "quote_start": start,
            "quote_end": start + len(original),
            "actor": "synthetic_operator",
            "reason": "从当前案件合成铭牌中登记序列号，尚待复核",
        }
        foreign = client.post(f"/api/v1/cases/{other_id}/claims", json=command)
        assert foreign.status_code == 409
        assert foreign.json()["error"]["code"] == "claim_source_invalid"
        candidate = client.post(f"{url}/claims", json=command)
        assert candidate.status_code == 201
        claim = candidate.json()
        assert (claim["field"], claim["status"], claim["created_by"], claim["version"]) == (
            "serial_number",
            "candidate",
            "user",
            1,
        )
        pending = client.get(f"{url}/current-facts").json()["items"][0]
        assert pending["usable_as_fact"] is False
        assert pending["confirmation"] is None
        assert pending["citations"][0]["quote_text"] == original
        review = client.get(f"{url}/evidence-review").json()["items"]
        assert [item["claim"]["id"] for item in review] == [claim["id"]]

        confirmed = client.post(
            f"/api/v1/claims/{claim['id']}/confirm",
            json={
                "expected_version": 1,
                "basis": "evidence",
                "actor": "synthetic_reviewer",
                "reason": "本人逐字核对当前案件的合成铭牌原文",
            },
        )
        assert confirmed.status_code == 200
        settled = client.get(f"{url}/current-facts").json()["items"][0]
        assert settled["usable_as_fact"] is True
        assert settled["confirmation_basis"] == "evidence"
        assert settled["claim"]["version"] == 2
        assert client.get(f"{url}/evidence-review").json()["items"] == []
        correction = {
            "expected_version": 1,
            "value": "SYN-WASH-010",
            "actor": "synthetic_reviewer",
            "reason": "本人再次检查合成铭牌记录，明确修正末位编号",
        }
        stale = client.post(f"/api/v1/claims/{claim['id']}/correct", json=correction)
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "claim_version_conflict"
        assert client.get(f"{url}/current-facts").json()["items"][0] == settled
        corrected = client.post(
            f"/api/v1/claims/{claim['id']}/correct",
            json={**correction, "expected_version": 2},
        )
        assert corrected.status_code == 200
        current = client.get(f"{url}/current-facts").json()
        fact = current["items"][0]
        assert (fact["claim"]["value"], fact["claim"]["version"]) == ("SYN-WASH-010", 3)
        assert fact["confirmation_basis"] == "correction"
        assert fact["confirmation"]["actor"] == correction["actor"]
        assert fact["confirmation"]["reason"] == correction["reason"]
        assert fact["usable_as_fact"] is True
        assert fact["citations"] == settled["citations"]
        assert client.get(f"{url}/preparation-intent").json() == intent.json()
        assert client.get(url).json() == before_case
        assert client.get(f"{url}/actions").json() == []
        assert client.get(f"/api/v1/cases/{other_id}/current-facts").json()["items"] == []

    with TestClient(create_app(config)) as restarted:
        assert restarted.get(f"{url}/current-facts").json() == current
        assert restarted.get(f"{url}/preparation-intent").json() == intent.json()
        assert restarted.get(url).json() == before_case
    connection = connect_database(config.database_path)
    try:
        events = ClaimService(connection).list_claim_events(claim["id"])
        assert [event.new_version for event in events] == [1, 2, 3]
        assert events[-1].prior_value.to_python() == original
        assert events[-1].new_value.to_python() == correction["value"]
        assert events[-1].reason == correction["reason"]
    finally:
        connection.close()
