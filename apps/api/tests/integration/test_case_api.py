"""Integration tests for persisted M1 Case endpoints."""

from pathlib import Path

from fastapi.testclient import TestClient

import lifecase.api.cases as cases_api
from lifecase.cases import CasePersistenceError
from lifecase.config import ApplicationConfig
from lifecase.main import create_app


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def test_case_create_and_read_survive_new_client(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v1/cases",
            json={
                "title": "Synthetic warranty case",
                "goal": "Prepare a synthetic warranty request",
                "template_id": "warranty",
            },
        )

    assert created.status_code == 201
    body = created.json()
    assert body["type"] == "warranty"
    assert (body["template_id"], body["template_version"]) == ("warranty_claim", 1)
    assert body["status"] == "intake"
    assert body["version"] == 1
    assert body["created_at"].endswith("Z")
    assert body["closed_at"] is None

    with TestClient(create_app(config)) as restarted_client:
        restored = restarted_client.get(f"/api/v1/cases/{body['id']}")

    assert restored.status_code == 200
    assert restored.json() == body


def test_case_errors_use_safe_stable_contract(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        invalid = client.post(
            "/api/v1/cases",
            json={"title": "", "goal": "synthetic", "unexpected": "not-accepted"},
        )
        missing = client.get("/api/v1/cases/case_missing")

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert "not-accepted" not in str(invalid.json()["error"]["details"])
    assert invalid.json()["error"]["request_id"].startswith("req_")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "case_not_found"


def test_transition_and_history_api_persist_optimistic_versions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v1/cases",
            json={
                "title": "Synthetic lifecycle",
                "goal": "Test transition API",
                "template_id": "warranty",
            },
        ).json()
        first = client.post(
            f"/api/v1/cases/{created['id']}/transitions",
            json={
                "expected_version": 1,
                "target_status": "ready",
                "actor": "local_user",
                "reason": "Synthetic evidence is ready",
            },
        )
        second = client.post(
            f"/api/v1/cases/{created['id']}/transitions",
            json={
                "expected_version": 2,
                "target_status": "in_progress",
                "actor": "local_user",
                "reason": "Synthetic work started",
            },
        )

    assert first.status_code == second.status_code == 200
    assert (first.json()["prior_status"], first.json()["new_status"]) == (
        "intake",
        "ready",
    )
    assert (first.json()["prior_version"], first.json()["new_version"]) == (1, 2)
    assert (second.json()["prior_version"], second.json()["new_version"]) == (2, 3)

    with TestClient(create_app(config)) as restarted:
        case = restarted.get(f"/api/v1/cases/{created['id']}")
        history = restarted.get(f"/api/v1/cases/{created['id']}/transitions")

    assert case.status_code == history.status_code == 200
    assert (case.json()["status"], case.json()["version"], case.json()["closed_at"]) == (
        "in_progress",
        3,
        None,
    )
    assert [item["new_version"] for item in history.json()] == [2, 3]
    assert history.json() == [first.json(), second.json()]


def test_transition_api_rejects_stale_illegal_and_invalid_requests(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v1/cases",
            json={
                "title": "Synthetic boundaries",
                "goal": "Test safe failures",
                "template_id": "warranty",
            },
        ).json()
        accepted = client.post(
            f"/api/v1/cases/{created['id']}/transitions",
            json={
                "expected_version": 1,
                "target_status": "ready",
                "actor": "local_user",
                "reason": "Synthetic ready state",
            },
        )
        stale = client.post(
            f"/api/v1/cases/{created['id']}/transitions",
            json={
                "expected_version": 1,
                "target_status": "in_progress",
                "actor": "local_user",
                "reason": "Synthetic stale command",
            },
        )
        illegal = client.post(
            f"/api/v1/cases/{created['id']}/transitions",
            json={
                "expected_version": 2,
                "target_status": "resolved",
                "actor": "local_user",
                "reason": "Synthetic illegal edge",
            },
        )
        whitespace = client.post(
            f"/api/v1/cases/{created['id']}/transitions",
            json={
                "expected_version": 2,
                "target_status": "in_progress",
                "actor": "   ",
                "reason": "Synthetic invalid actor",
            },
        )
        malformed = client.post(
            f"/api/v1/cases/{created['id']}/transitions",
            json={
                "expected_version": 0,
                "target_status": "unknown",
                "actor": "local_user",
                "reason": "x" * 1001,
            },
        )
        boolean_version = client.post(
            f"/api/v1/cases/{created['id']}/transitions",
            json={
                "expected_version": True,
                "target_status": "in_progress",
                "actor": "local_user",
                "reason": "Synthetic invalid version type",
            },
        )
        missing = client.post(
            "/api/v1/cases/case_missing/transitions",
            json={
                "expected_version": 1,
                "target_status": "ready",
                "actor": "local_user",
                "reason": "Synthetic missing Case",
            },
        )
        missing_history = client.get("/api/v1/cases/case_missing/transitions")
        current = client.get(f"/api/v1/cases/{created['id']}")
        history = client.get(f"/api/v1/cases/{created['id']}/transitions")

    assert accepted.status_code == 200
    assert (stale.status_code, stale.json()["error"]["code"]) == (
        409,
        "case_version_conflict",
    )
    assert stale.json()["error"]["details"] == {"current_version": 2}
    assert (illegal.status_code, illegal.json()["error"]["code"]) == (
        409,
        "case_transition_not_allowed",
    )
    assert (whitespace.status_code, whitespace.json()["error"]["code"]) == (
        422,
        "case_transition_invalid",
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "validation_error"
    assert boolean_version.status_code == 422
    assert boolean_version.json()["error"]["code"] == "validation_error"
    assert (missing.status_code, missing.json()["error"]["code"]) == (
        404,
        "case_not_found",
    )
    assert (missing_history.status_code, missing_history.json()["error"]["code"]) == (
        404,
        "case_not_found",
    )
    assert (current.json()["status"], current.json()["version"]) == ("ready", 2)
    assert len(history.json()) == 1
    assert "Synthetic stale command" not in stale.text
    assert str(config.database_path) not in stale.text


def test_transition_storage_failure_maps_without_private_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path)

    def fail_transition(*_args: object, **_kwargs: object) -> None:
        raise CasePersistenceError("synthetic private SQL detail")

    monkeypatch.setattr(cases_api.CaseService, "transition_case", fail_transition)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v1/cases",
            json={
                "title": "Synthetic failure",
                "goal": "Test safe error",
                "template_id": "warranty",
            },
        ).json()
        response = client.post(
            f"/api/v1/cases/{created['id']}/transitions",
            json={
                "expected_version": 1,
                "target_status": "ready",
                "actor": "local_user",
                "reason": "Synthetic persistence failure",
            },
        )

    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "case_storage_failed",
    )
    assert response.json()["error"]["retryable"] is True
    assert "synthetic private SQL detail" not in response.text
    assert str(config.database_path) not in response.text
