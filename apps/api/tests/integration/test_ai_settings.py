"""P1-11 persistent local AI settings HTTP coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.providers import DEEPSEEK_CHAT_ENDPOINT, ProviderRuntimeClients
from lifecase.providers.runtime_settings import MemoryCredentialStore


class _DeepSeekClient:
    endpoint = DEEPSEEK_CHAT_ENDPOINT

    async def create(self, **_kwargs: object) -> object:
        raise AssertionError("Saving settings must not call a paid model")


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "lifecase.sqlite3",
        data_root=tmp_path / "data",
        upload_root=tmp_path / "uploads",
    )


def _client_factory(provider: object, api_key: str) -> ProviderRuntimeClients:
    assert str(provider) == "deepseek"
    assert api_key == "synthetic-secret"
    return ProviderRuntimeClients(deepseek=_DeepSeekClient())


def test_ai_settings_save_survives_app_restart_without_returning_secret(tmp_path: Path) -> None:
    store = MemoryCredentialStore()
    app = create_app(
        _config(tmp_path),
        credential_store=store,
        runtime_client_factory=_client_factory,
    )
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/system/ai-settings",
            headers={"X-LifeCase-Local-Intent": "ai-settings.v1"},
            json={
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
                "api_key": "synthetic-secret",
                "data_use_confirmed": True,
            },
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "settings_version": "ai_settings.v1",
        "persistence": "windows_credential_manager",
        "configured": True,
        "provider": "deepseek",
        "model_id": "deepseek-v4-flash",
        "runtime_status": "ready",
    }
    assert "synthetic-secret" not in response.text
    assert "api_key" not in response.text

    with TestClient(
        create_app(
            _config(tmp_path),
            credential_store=store,
            runtime_client_factory=_client_factory,
        )
    ) as restarted:
        restored = restarted.get("/api/v1/system/ai-settings")
        capability = restarted.get("/api/v1/system/ai-capabilities")

    assert restored.json()["configured"] is True
    assert restored.json()["provider"] == "deepseek"
    assert capability.json()["current_provider"] == "deepseek"
    assert capability.json()["runtime_status"] == "ready"


def test_ai_settings_require_local_intent_and_clear_persisted_credential(
    tmp_path: Path,
) -> None:
    store = MemoryCredentialStore()
    with TestClient(
        create_app(
            _config(tmp_path),
            credential_store=store,
            runtime_client_factory=_client_factory,
        )
    ) as client:
        rejected = client.put(
            "/api/v1/system/ai-settings",
            json={
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
                "api_key": "synthetic-secret",
                "data_use_confirmed": True,
            },
        )
        saved = client.put(
            "/api/v1/system/ai-settings",
            headers={"X-LifeCase-Local-Intent": "ai-settings.v1"},
            json={
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
                "api_key": "synthetic-secret",
                "data_use_confirmed": True,
            },
        )
        cleared = client.delete(
            "/api/v1/system/ai-settings",
            headers={"X-LifeCase-Local-Intent": "ai-settings.v1"},
        )

    assert rejected.status_code == 403
    assert saved.status_code == 200
    assert cleared.status_code == 200
    assert cleared.json()["configured"] is False
    assert cleared.json()["runtime_status"] == "disabled"
    assert store.load() is None


def test_ai_settings_rejects_unconfirmed_external_processing_without_echoing_key(
    tmp_path: Path,
) -> None:
    store = MemoryCredentialStore()
    with TestClient(
        create_app(
            _config(tmp_path),
            credential_store=store,
            runtime_client_factory=_client_factory,
        )
    ) as client:
        response = client.put(
            "/api/v1/system/ai-settings",
            headers={"X-LifeCase-Local-Intent": "ai-settings.v1"},
            json={
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
                "api_key": "private-key-must-not-echo",
                "data_use_confirmed": False,
            },
        )

    assert response.status_code == 422
    assert "private-key-must-not-echo" not in response.text
    assert store.load() is None


@pytest.mark.parametrize(
    ("provider", "model_id"),
    [
        ("openai", "gpt-5.6-terra"),
        ("deepseek", "deepseek-v4-flash"),
        ("deepseek", "deepseek-v4-pro"),
        ("groq", "openai/gpt-oss-20b"),
        ("mistral", "mistral-small-2603"),
        ("gemini", "gemini-2.5-flash"),
        ("anthropic", "claude-sonnet-4-5-20250929"),
    ],
)
def test_every_ui_provider_builds_a_ready_fixed_client_without_calling_it(
    tmp_path: Path,
    provider: str,
    model_id: str,
) -> None:
    with TestClient(
        create_app(_config(tmp_path), credential_store=MemoryCredentialStore())
    ) as client:
        saved = client.put(
            "/api/v1/system/ai-settings",
            headers={"X-LifeCase-Local-Intent": "ai-settings.v1"},
            json={
                "provider": provider,
                "model_id": model_id,
                "api_key": "synthetic-never-sent",
                "data_use_confirmed": True,
            },
        )
        catalog = client.get("/api/v1/system/ai-capabilities")

    assert saved.status_code == 200
    assert saved.json()["runtime_status"] == "ready"
    assert catalog.json()["current_provider"] == provider
    assert catalog.json()["runtime_status"] == "ready"


def test_deepseek_model_can_change_without_reentering_the_saved_key(tmp_path: Path) -> None:
    store = MemoryCredentialStore()
    with TestClient(
        create_app(
            _config(tmp_path),
            credential_store=store,
            runtime_client_factory=_client_factory,
        )
    ) as client:
        saved = client.put(
            "/api/v1/system/ai-settings",
            headers={"X-LifeCase-Local-Intent": "ai-settings.v1"},
            json={
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
                "api_key": "synthetic-secret",
                "data_use_confirmed": True,
            },
        )
        changed = client.put(
            "/api/v1/system/ai-settings",
            headers={"X-LifeCase-Local-Intent": "ai-settings.v1"},
            json={
                "provider": "deepseek",
                "model_id": "deepseek-v4-pro",
                "api_key": None,
                "data_use_confirmed": True,
            },
        )

    assert saved.status_code == 200
    assert changed.status_code == 200
    assert changed.json()["model_id"] == "deepseek-v4-pro"
    assert store.load() is not None
    assert store.load().api_key == "synthetic-secret"
