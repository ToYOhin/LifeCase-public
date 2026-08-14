"""Safe system-level AI Provider catalog coverage."""

from pathlib import Path

from fastapi.testclient import TestClient

from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.providers import (
    DEEPSEEK_CHAT_MODEL_ID,
    ModelProviderConfig,
    ModelProviderKind,
)


def test_ai_capabilities_lists_every_product_boundary_without_secrets(tmp_path: Path) -> None:
    config = ApplicationConfig(
        database_path=tmp_path / "lifecase.sqlite3",
        data_root=tmp_path / "data",
        upload_root=tmp_path / "uploads",
    )

    with TestClient(create_app(config)) as client:
        response = client.get("/api/v1/system/ai-capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["catalog_version"] == "ai_provider_catalog.v1"
    assert body["current_provider"] == "disabled"
    assert body["runtime_status"] == "disabled"
    assert body["browser_configuration"] == "persistent_local_settings"
    assert [item["provider"] for item in body["providers"]] == [
        "openai",
        "deepseek",
        "groq",
        "mistral",
        "gemini",
        "anthropic",
        "ollama",
    ]
    assert [item["evidence_status"] for item in body["providers"]] == [
        "offline_injection_verified",
        "synthetic_acceptance_verified",
        "offline_injection_verified",
        "offline_injection_verified",
        "offline_injection_verified",
        "offline_injection_verified",
        "quality_gate_failed",
    ]
    assert body["providers"][-1]["product_status"] == "evaluation_only"
    encoded = response.content.lower()
    assert b"api_key" not in encoded
    assert b"credential" not in encoded
    assert b"endpoint" not in encoded
    assert b"sk-" not in encoded


def test_ai_capabilities_reports_selected_provider_without_claiming_runtime_ready(
    tmp_path: Path,
) -> None:
    config = ApplicationConfig(
        database_path=tmp_path / "lifecase.sqlite3",
        data_root=tmp_path / "data",
        upload_root=tmp_path / "uploads",
        model_provider=ModelProviderConfig(
            provider=ModelProviderKind.DEEPSEEK,
            model_id=DEEPSEEK_CHAT_MODEL_ID,
        ),
        live_agent_enabled=True,
        deepseek_data_use_confirmed=True,
    )

    with TestClient(create_app(config)) as client:
        response = client.get("/api/v1/system/ai-capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["current_provider"] == "deepseek"
    assert body["current_model_id"] == DEEPSEEK_CHAT_MODEL_ID
    assert body["runtime_status"] == "unavailable"
    selected = next(item for item in body["providers"] if item["provider"] == "deepseek")
    assert selected["selected"] is True
    assert selected["runtime_status"] == "unavailable"
    assert all(
        item["runtime_status"] == "disabled"
        for item in body["providers"]
        if item["provider"] != "deepseek"
    )


def test_ai_capabilities_openapi_is_read_only_and_has_no_browser_configuration_input(
    tmp_path: Path,
) -> None:
    config = ApplicationConfig(
        database_path=tmp_path / "lifecase.sqlite3",
        data_root=tmp_path / "data",
        upload_root=tmp_path / "uploads",
    )

    operation = create_app(config).openapi()["paths"]["/api/v1/system/ai-capabilities"]["get"]

    assert "requestBody" not in operation
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AiCapabilitiesResponse"
    }
