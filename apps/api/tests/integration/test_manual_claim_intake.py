"""Product-path tests for deterministic browser Claim intake."""

from pathlib import Path

from fastapi.testclient import TestClient

from lifecase.config import ApplicationConfig
from lifecase.main import create_app


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _create_case(client: TestClient, title: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/cases",
        json={
            "title": title,
            "goal": "Complete one deterministic browser workflow",
            "template_id": "warranty",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_user_can_create_evidence_backed_candidate_and_confirm_after_restart(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    text = "Synthetic receipt from Store A for headphones."
    with TestClient(create_app(config)) as client:
        case = _create_case(client, "Synthetic manual Claim Case")
        case_id = str(case["id"])
        imported = client.post(
            f"/api/v1/cases/{case_id}/sources",
            files={"file": ("receipt.txt", text.encode(), "text/plain")},
        )
        assert imported.status_code == 201

        listed = client.get(f"/api/v1/cases/{case_id}/source-segments")
        assert listed.status_code == 200
        segment = listed.json()[0]
        quote = "Store A"
        start = segment["text"].index(quote)
        created = client.post(
            f"/api/v1/cases/{case_id}/claims",
            json={
                "field": "merchant_or_manufacturer",
                "value": quote,
                "segment_id": segment["id"],
                "quote_start": start,
                "quote_end": start + len(quote),
                "actor": "local_user",
                "reason": "Selected the merchant from the imported receipt",
            },
        )

        assert created.status_code == 201
        assert created.json()["created_by"] == "user"
        assert created.json()["status"] == "candidate"
        review = client.get(f"/api/v1/cases/{case_id}/evidence-review")
        assert review.status_code == 200
        assert review.json()["items"][0]["citations"][0]["quote_text"] == quote
        assert "object_path" not in listed.text + created.text + review.text

    with TestClient(create_app(config)) as restarted:
        restored = restarted.get(f"/api/v1/cases/{case_id}/evidence-review")
        claim = restored.json()["items"][0]["claim"]
        confirmed = restarted.post(
            f"/api/v1/claims/{claim['id']}/confirm",
            json={
                "expected_version": claim["version"],
                "basis": "evidence",
                "actor": "local_user",
                "reason": "Confirmed against the selected receipt text",
            },
        )
        refreshed = restarted.get(f"/api/v1/cases/{case_id}/evidence-review")
        workbench = restarted.get("/api/v1/cases")

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert refreshed.json()["items"] == []
    item = next(item for item in workbench.json() if item["id"] == case_id)
    assert "merchant_or_manufacturer" not in item["missing_evidence"]["required_claims"]


def test_manual_claim_intake_rejects_cross_case_unknown_field_and_bad_quote(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    text = "Synthetic receipt from Store A."
    with TestClient(create_app(config)) as client:
        first = _create_case(client, "First synthetic Case")
        second = _create_case(client, "Second synthetic Case")
        imported = client.post(
            f"/api/v1/cases/{first['id']}/sources",
            files={"file": ("receipt.txt", text.encode(), "text/plain")},
        )
        assert imported.status_code == 201
        segment = client.get(f"/api/v1/cases/{first['id']}/source-segments").json()[0]
        base = {
            "field": "merchant_or_manufacturer",
            "value": "Store A",
            "segment_id": segment["id"],
            "quote_start": text.index("Store A"),
            "quote_end": text.index("Store A") + len("Store A"),
            "actor": "local_user",
            "reason": "Synthetic boundary check",
        }
        cross_case = client.post(
            f"/api/v1/cases/{second['id']}/claims",
            json=base,
        )
        unknown_field = client.post(
            f"/api/v1/cases/{first['id']}/claims",
            json={**base, "field": "unreviewed_private_field"},
        )
        bad_quote = client.post(
            f"/api/v1/cases/{first['id']}/claims",
            json={**base, "quote_end": len(text) + 1},
        )
        first_review = client.get(f"/api/v1/cases/{first['id']}/evidence-review")
        second_review = client.get(f"/api/v1/cases/{second['id']}/evidence-review")

    assert (cross_case.status_code, cross_case.json()["error"]["code"]) == (
        409,
        "claim_source_invalid",
    )
    assert (unknown_field.status_code, unknown_field.json()["error"]["code"]) == (
        422,
        "claim_invalid",
    )
    assert (bad_quote.status_code, bad_quote.json()["error"]["code"]) == (
        409,
        "claim_source_invalid",
    )
    assert first_review.json()["items"] == second_review.json()["items"] == []
    assert str(tmp_path) not in cross_case.text + unknown_field.text + bad_quote.text


def test_deterministic_product_loop_closes_and_recovers_after_restart(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    text = "Synthetic warranty follow-up date 2026-09-01 with Store A."
    with TestClient(create_app(config)) as client:
        case = _create_case(client, "Synthetic complete browser loop")
        case_id = str(case["id"])
        imported = client.post(
            f"/api/v1/cases/{case_id}/sources",
            files={"file": ("receipt.txt", text.encode(), "text/plain")},
        ).json()
        segment = client.get(f"/api/v1/cases/{case_id}/source-segments").json()[0]
        quote = "2026-09-01"
        start = text.index(quote)
        claim = client.post(
            f"/api/v1/cases/{case_id}/claims",
            json={
                "field": "purchase_date",
                "value": quote,
                "segment_id": segment["id"],
                "quote_start": start,
                "quote_end": start + len(quote),
                "actor": "local_user",
                "reason": "Selected the displayed synthetic date",
            },
        ).json()
        confirmed = client.post(
            f"/api/v1/claims/{claim['id']}/confirm",
            json={
                "expected_version": claim["version"],
                "basis": "evidence",
                "actor": "local_user",
                "reason": "Confirmed against the exact imported quote",
            },
        )
        deadline = client.post(
            f"/api/v1/cases/{case_id}/deadlines/suggested",
            json={
                "date_kind": "local_date",
                "due_value": "2026-09-08",
                "timezone": "Asia/Shanghai",
                "actor": "local_user",
                "reason": "Personal synthetic follow-up preference",
            },
        ).json()
        confirmed_deadline = client.post(
            f"/api/v1/deadlines/{deadline['id']}/confirm",
            json={
                "expected_version": deadline["version"],
                "actor": "local_user",
                "reason": "Confirmed the displayed personal reminder",
            },
        )
        action = client.post(
            f"/api/v1/cases/{case_id}/actions",
            json={
                "kind": "collect_evidence",
                "risk_level": "B",
                "payload": {
                    "requirement_key": "purchase_proof",
                    "requirement_type": "evidence",
                },
                "requirements": [],
                "dependencies": [],
                "deadline": None,
                "actor": "local_user",
                "reason": "Create an approval-gated local follow-up",
            },
        ).json()
        action = client.post(
            f"/api/v1/actions/{action['id']}/revalidate",
            json={
                "expected_version": action["version"],
                "actor": "local_user",
                "reason": "Revalidate the local follow-up before approval",
            },
        ).json()
        approval_response = client.post(
            f"/api/v1/actions/{action['id']}/approval-requests",
            json={
                "expected_action_version": action["version"],
                "payload_sha256": action["payload_sha256"],
                "summary": "Approve the displayed local synthetic follow-up",
                "actor": "local_user",
                "reason": "Request exact-version approval",
            },
        )
        assert approval_response.status_code == 201, approval_response.text
        approval = approval_response.json()
        approved = client.post(
            f"/api/v1/actions/{action['id']}/approve",
            json={
                "approval_id": approval["id"],
                "expected_approval_version": approval["version"],
                "expected_action_version": action["version"],
                "actor": "local_user",
                "reason": "Approve only the displayed version",
            },
        ).json()["action"]
        running = client.post(
            f"/api/v1/actions/{action['id']}/start",
            json={
                "expected_version": approved["version"],
                "actor": "local_user",
                "reason": "Start the approved local follow-up",
            },
        ).json()
        completed = client.post(
            f"/api/v1/actions/{action['id']}/complete",
            json={
                "expected_version": running["version"],
                "actor": "local_user",
                "reason": "Complete with the imported synthetic source",
                "evidence": [
                    {
                        "authority_type": "source",
                        "authority_id": imported["id"],
                        "expected_version": None,
                        "authority_sha256": imported["sha256"],
                    }
                ],
            },
        )

        version = int(case["version"])
        for target in ("ready", "in_progress", "resolved", "archived"):
            transition = client.post(
                f"/api/v1/cases/{case_id}/transitions",
                json={
                    "expected_version": version,
                    "target_status": target,
                    "actor": "local_user",
                    "reason": f"Synthetic browser lifecycle to {target}",
                },
            )
            assert transition.status_code == 200
            version = int(transition.json()["new_version"])

        pack = client.post(f"/api/v1/cases/{case_id}/evidence-pack")
        backup = client.post("/api/v1/backups")
        timeline = client.get(f"/api/v1/cases/{case_id}/timeline")

    assert confirmed.status_code == 200
    assert confirmed_deadline.status_code == 200
    assert completed.status_code == 200
    assert completed.json()["status"] == "done"
    assert pack.status_code == backup.status_code == 201
    assert len(timeline.json()["events"]) >= 10

    with TestClient(create_app(config)) as restarted:
        restored_case = restarted.get(f"/api/v1/cases/{case_id}")
        restored_deadlines = restarted.get(f"/api/v1/cases/{case_id}/deadlines")
        restored_actions = restarted.get(f"/api/v1/cases/{case_id}/actions")
        restored_pack = restarted.get(f"/api/v1/cases/{case_id}/evidence-pack")
        restored_backup = restarted.get("/api/v1/backups")

    assert restored_case.json()["status"] == "archived"
    assert restored_deadlines.json()[0]["status"] == "confirmed"
    assert restored_actions.json()[0]["status"] == "done"
    assert restored_pack.json()["sha256"] == pack.json()["sha256"]
    assert restored_backup.json()["sha256"] == backup.json()["sha256"]
