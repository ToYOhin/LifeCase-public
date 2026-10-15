"""P1-04 opt-in Case-scoped live Agent product composition coverage."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from lifecase.agent import ExtractionEvidenceSegment
from lifecase.agent.demo import (
    PORTFOLIO_AGENT_DEMO_VERSION,
    _answer_checkpoint,
    _extraction_checkpoint,
)
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.providers import (
    DEEPSEEK_CHAT_ENDPOINT,
    DEEPSEEK_CHAT_MODEL_ID,
    DEEPSEEK_CHAT_PRO_MODEL_ID,
    GROQ_CHAT_ENDPOINT,
    GROQ_CHAT_MODEL_ID,
    MISTRAL_CHAT_ENDPOINT,
    MISTRAL_CHAT_MODEL_ID,
    ModelProviderConfig,
    ModelProviderKind,
    OpenAIResponsesResource,
    ProviderConfigurationError,
    canonical_json_object,
)
from lifecase.storage import connect_database


class _InMemoryResponsesResource:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Awaitable[Any]:
        self.calls.append(kwargs)

        async def execute() -> dict[str, object]:
            request_input = cast(
                str,
                kwargs["input"][1]["content"][0]["text"],
            )
            stage_input = cast(dict[str, Any], json.loads(request_input))
            schema = cast(dict[str, Any], kwargs["text"]["format"]["schema"])
            properties = cast(dict[str, Any], schema["properties"])
            stage = cast(str, properties["stage"]["const"])
            if stage == "extract":
                evidence = tuple(
                    ExtractionEvidenceSegment(**item)
                    for item in stage_input["stage_payload"]["evidence"]
                )
                checkpoint = _extraction_checkpoint(evidence)
            elif stage == "validate":
                checkpoint = _answer_checkpoint(request_input)
            else:
                checkpoint = {
                    "schema_version": "portfolio_agent_demo_stage.v1",
                    "demo_version": PORTFOLIO_AGENT_DEMO_VERSION,
                    "summary": f"{stage} completed without external action",
                    "read_only": True,
                }
            output = canonical_json_object(
                {
                    "stage": stage,
                    "stage_version": properties["stage_version"]["const"],
                    "checkpoint_schema_version": properties["checkpoint_schema_version"]["const"],
                    "checkpoint": checkpoint,
                },
                maximum_bytes=65_536,
            )
            return {
                "status": "completed",
                "model": "gpt-live-synthetic-2026-08-12",
                "private_debug": "PRIVATE-RAW-RESPONSE-MARKER",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": output}],
                    }
                ],
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 10,
                    "total_tokens": 30,
                },
            }

        return execute()


class _InMemoryOpenAIClient:
    def __init__(self) -> None:
        self.resource = _InMemoryResponsesResource()
        self.responses: OpenAIResponsesResource = self.resource


class _InMemoryGroqCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Awaitable[Any]:
        self.calls.append(kwargs)

        async def execute() -> dict[str, object]:
            input_json = cast(str, kwargs["messages"][2]["content"])
            stage_input = cast(dict[str, Any], json.loads(input_json))
            schema = cast(dict[str, Any], kwargs["response_format"]["json_schema"]["schema"])
            properties = cast(dict[str, Any], schema["properties"])
            stage = cast(str, properties["stage"]["const"])
            if stage == "extract":
                evidence = tuple(
                    ExtractionEvidenceSegment(**item)
                    for item in stage_input["stage_payload"]["evidence"]
                )
                checkpoint = _extraction_checkpoint(evidence)
            elif stage == "validate":
                checkpoint = _answer_checkpoint(input_json)
            else:
                checkpoint = {
                    "schema_version": "portfolio_agent_demo_stage.v1",
                    "demo_version": PORTFOLIO_AGENT_DEMO_VERSION,
                    "summary": f"{stage} completed without external action",
                    "read_only": True,
                }
            output = canonical_json_object(
                {
                    "stage": stage,
                    "stage_version": properties["stage_version"]["const"],
                    "checkpoint_schema_version": properties["checkpoint_schema_version"]["const"],
                    "checkpoint": checkpoint,
                },
                maximum_bytes=65_536,
            )
            return {
                "model": GROQ_CHAT_MODEL_ID,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": output},
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
                "private_debug": "PRIVATE-GROQ-RAW-RESPONSE",
            }

        return execute()


class _InMemoryGroqChat:
    def __init__(self) -> None:
        self.completions = _InMemoryGroqCompletions()


class _InMemoryGroqClient:
    def __init__(self, *, endpoint: str = GROQ_CHAT_ENDPOINT) -> None:
        self.endpoint = endpoint
        self.chat = _InMemoryGroqChat()


class _InMemoryMistralCompletions(_InMemoryGroqCompletions):
    def create(self, **kwargs: Any) -> Awaitable[Any]:
        mistral_kwargs = dict(kwargs)
        messages = cast(list[dict[str, Any]], mistral_kwargs["messages"])
        mistral_kwargs["messages"] = [
            messages[0],
            {"role": "developer", "content": ""},
            messages[1],
        ]
        outcome = super().create(**mistral_kwargs)
        self.calls[-1] = kwargs

        async def execute() -> Any:
            response = await outcome
            response["model"] = MISTRAL_CHAT_MODEL_ID
            response["private_debug"] = "PRIVATE-MISTRAL-RAW-RESPONSE"
            return response

        return execute()


class _InMemoryMistralChat:
    def __init__(self) -> None:
        self.completions = _InMemoryMistralCompletions()


class _InMemoryMistralClient:
    def __init__(self, *, endpoint: str = MISTRAL_CHAT_ENDPOINT) -> None:
        self.endpoint = endpoint
        self.chat = _InMemoryMistralChat()


class _InMemoryDeepSeekClient:
    def __init__(
        self,
        *,
        endpoint: str = DEEPSEEK_CHAT_ENDPOINT,
        model_id: str = DEEPSEEK_CHAT_MODEL_ID,
    ) -> None:
        self.endpoint = endpoint
        self.model_id = model_id
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Awaitable[Any]:
        self.calls.append(kwargs)

        async def execute() -> dict[str, object]:
            input_json = cast(str, kwargs["messages"][1]["content"])
            stage_input = cast(dict[str, Any], json.loads(input_json))
            system_message = cast(str, kwargs["messages"][0]["content"])
            schema = cast(
                dict[str, Any],
                json.loads(system_message.rsplit("JSON Schema:\n", maxsplit=1)[1]),
            )
            properties = cast(dict[str, Any], schema["properties"])
            stage = cast(str, properties["stage"]["const"])
            if stage == "extract":
                evidence = tuple(
                    ExtractionEvidenceSegment(**item)
                    for item in stage_input["stage_payload"]["evidence"]
                )
                checkpoint = _extraction_checkpoint(evidence)
            elif stage == "validate":
                checkpoint = _answer_checkpoint(input_json)
            else:
                checkpoint = {
                    "schema_version": "portfolio_agent_demo_stage.v1",
                    "demo_version": PORTFOLIO_AGENT_DEMO_VERSION,
                    "summary": f"{stage} completed without external action",
                    "read_only": True,
                }
            output = canonical_json_object(
                {
                    "stage": stage,
                    "stage_version": properties["stage_version"]["const"],
                    "checkpoint_schema_version": properties["checkpoint_schema_version"]["const"],
                    "checkpoint": checkpoint,
                },
                maximum_bytes=65_536,
            )
            return {
                "model": self.model_id,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": output},
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 20,
                },
                "private_debug": "PRIVATE-DEEPSEEK-RAW-RESPONSE",
            }

        return execute()


def test_live_agent_environment_opt_in_and_model_are_server_owned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIFECASE_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("LIFECASE_LIVE_AGENT", "true")
    monkeypatch.setenv("LIFECASE_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("LIFECASE_MODEL_ID", "gpt-live-synthetic")

    config = ApplicationConfig.from_environment()

    assert config.live_agent_enabled is True
    assert config.model_provider.model_id == "gpt-live-synthetic"
    monkeypatch.setenv("LIFECASE_LIVE_AGENT", "invalid")
    with pytest.raises(ValueError, match="LIFECASE_LIVE_AGENT must be a boolean"):
        ApplicationConfig.from_environment()
    monkeypatch.setenv("LIFECASE_LIVE_AGENT", "true")
    monkeypatch.delenv("LIFECASE_MODEL_ID")
    with pytest.raises(ProviderConfigurationError):
        ApplicationConfig.from_environment()

    monkeypatch.setenv("LIFECASE_MODEL_PROVIDER", "groq")
    monkeypatch.setenv("LIFECASE_MODEL_ID", GROQ_CHAT_MODEL_ID)
    monkeypatch.setenv("LIFECASE_GROQ_ZDR_CONFIRMED", "true")
    groq_config = ApplicationConfig.from_environment()
    assert groq_config.model_provider.provider is ModelProviderKind.GROQ
    assert groq_config.groq_zdr_confirmed is True
    monkeypatch.setenv("LIFECASE_GROQ_ZDR_CONFIRMED", "invalid")
    with pytest.raises(ValueError, match="LIFECASE_GROQ_ZDR_CONFIRMED must be a boolean"):
        ApplicationConfig.from_environment()

    monkeypatch.setenv("LIFECASE_GROQ_ZDR_CONFIRMED", "false")
    monkeypatch.setenv("LIFECASE_MODEL_PROVIDER", "mistral")
    monkeypatch.setenv("LIFECASE_MODEL_ID", MISTRAL_CHAT_MODEL_ID)
    monkeypatch.setenv("LIFECASE_MISTRAL_ZDR_CONFIRMED", "true")
    mistral_config = ApplicationConfig.from_environment()
    assert mistral_config.model_provider.provider is ModelProviderKind.MISTRAL
    assert mistral_config.mistral_zdr_confirmed is True
    monkeypatch.setenv("LIFECASE_MISTRAL_ZDR_CONFIRMED", "invalid")
    with pytest.raises(ValueError, match="LIFECASE_MISTRAL_ZDR_CONFIRMED must be a boolean"):
        ApplicationConfig.from_environment()

    monkeypatch.setenv("LIFECASE_MISTRAL_ZDR_CONFIRMED", "false")
    monkeypatch.setenv("LIFECASE_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("LIFECASE_MODEL_ID", DEEPSEEK_CHAT_MODEL_ID)
    monkeypatch.setenv("LIFECASE_DEEPSEEK_DATA_USE_CONFIRMED", "true")
    deepseek_config = ApplicationConfig.from_environment()
    assert deepseek_config.model_provider.provider is ModelProviderKind.DEEPSEEK
    assert deepseek_config.deepseek_data_use_confirmed is True
    monkeypatch.setenv("LIFECASE_DEEPSEEK_DATA_USE_CONFIRMED", "invalid")
    with pytest.raises(
        ValueError,
        match="LIFECASE_DEEPSEEK_DATA_USE_CONFIRMED must be a boolean",
    ):
        ApplicationConfig.from_environment()


def test_live_agent_openapi_accepts_only_server_bounded_confirmation(tmp_path: Path) -> None:
    schema = create_app(_config(tmp_path)).openapi()
    start = schema["paths"]["/api/v1/cases/{case_id}/live-agent"]["post"]
    request_schema = start["requestBody"]["content"]["application/json"]["schema"]

    assert request_schema == {"$ref": "#/components/schemas/LiveAgentAuthorizationRequest"}
    authorization = schema["components"]["schemas"]["LiveAgentAuthorizationRequest"]
    assert authorization["additionalProperties"] is False
    assert authorization["required"] == ["preview_sha256", "confirm_read_only"]
    assert set(authorization["properties"]) == {
        "preview_sha256",
        "confirm_read_only",
        "question",
    }
    question = schema["components"]["schemas"]["LiveAgentQuestionRequest"]
    assert question["additionalProperties"] is False
    assert question["required"] == ["question"]
    assert set(question["properties"]) == {"question"}
    assert question["properties"]["question"]["maxLength"] == 2000
    command_request = schema["components"]["schemas"]["LiveAgentCommandRequest"]
    assert command_request["additionalProperties"] is False
    assert command_request["required"] == ["expected_run_id"]
    assert set(command_request["properties"]) == {"expected_run_id"}
    capability = schema["components"]["schemas"]["LiveAgentCapabilityResponse"]
    assert capability["properties"]["provider"]["enum"] == [
        "disabled",
        "openai",
        "groq",
        "mistral",
        "deepseek",
        "gemini",
        "anthropic",
    ]
    for command in ("execute", "retry", "recover", "cancel"):
        operation = schema["paths"][f"/api/v1/cases/{{case_id}}/live-agent/{command}"]["post"]
        assert operation["requestBody"].get("required", False) is False
        assert {"$ref": "#/components/schemas/LiveAgentCommandRequest"} in (
            operation["requestBody"]["content"]["application/json"]["schema"]["anyOf"]
        )


def test_live_agent_default_disabled_never_calls_injected_client(tmp_path: Path) -> None:
    client_boundary = _InMemoryOpenAIClient()
    with TestClient(create_app(_config(tmp_path), openai_client=client_boundary)) as client:
        case = _create_case(client, template_id="warranty")
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent")
        start = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={"preview_sha256": "0" * 64, "confirm_read_only": True},
        )

    assert preview.status_code == 200
    assert preview.json()["capability"] == {
        "status": "disabled",
        "provider": "disabled",
        "model_id": None,
        "action": "Enable the live read-only Agent explicitly on the server.",
        "supported_case_types": ["warranty"],
    }
    assert start.status_code == 409
    assert start.json()["error"]["code"] == "live_agent_disabled"
    assert client_boundary.resource.calls == []


def test_live_agent_missing_client_and_unsupported_case_fail_closed(tmp_path: Path) -> None:
    config = _config(tmp_path, live=True)
    with TestClient(create_app(config)) as client:
        warranty = _create_case(client, template_id="warranty")
        unavailable = client.get(f"/api/v1/cases/{warranty['id']}/live-agent")
        start = client.post(
            f"/api/v1/cases/{warranty['id']}/live-agent",
            json={
                "preview_sha256": unavailable.json()["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )
        unsupported = _create_case(client, template_id="return_refund")
        rejected = client.get(f"/api/v1/cases/{unsupported['id']}/live-agent")

    assert unavailable.json()["capability"]["status"] == "unavailable"
    assert start.status_code == 503
    assert start.json()["error"]["code"] == "live_agent_client_unavailable"
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "live_agent_case_unsupported"


def test_live_agent_preview_and_authorization_are_server_authoritative(tmp_path: Path) -> None:
    boundary = _InMemoryOpenAIClient()
    with TestClient(create_app(_config(tmp_path, live=True), openai_client=boundary)) as client:
        case = _create_case(client, template_id="warranty")
        evidence_text = "Merchant Alpha. Product SynthPhone. Order ORD-1."
        _upload_evidence(client, case["id"], evidence_text)
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent")
        body = preview.json()
        forbidden = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": body["preview"]["preview_sha256"],
                "confirm_read_only": True,
                "api_key": "FORBIDDEN_CREDENTIAL_CANARY",
                "host": "https://private.example",
                "endpoint": "https://private.example/v1",
                "model": "browser-model",
                "provider": "groq",
                "provider_output": {"raw": True},
                "checkpoint": {},
                "tools": ["write"],
                "budget": {"max_calls": 99},
            },
        )
        unconfirmed = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": body["preview"]["preview_sha256"],
                "confirm_read_only": False,
            },
        )
        stale = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={"preview_sha256": "f" * 64, "confirm_read_only": True},
        )
        started = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": body["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )

    assert body["capability"] == {
        "status": "ready",
        "provider": "openai",
        "model_id": "gpt-live-synthetic",
        "action": "Review the server preview and explicitly authorize one read-only run.",
        "supported_case_types": ["warranty"],
    }
    assert body["preview"]["case_type"] == "warranty"
    assert body["preview"]["evidence"] == {
        "segment_count": 1,
        "utf8_bytes": len(evidence_text.encode("utf-8")),
    }
    assert body["preview"]["budget"] == {
        "max_calls": 14,
        "max_input_tokens": 28000,
        "max_output_tokens": 14000,
        "max_total_tokens": 42000,
        "max_cost_minor": 100,
        "cost_currency": "CNY",
    }
    assert "candidate" in body["preview"]["read_only_notice"]
    assert forbidden.status_code == 422
    assert unconfirmed.status_code == 409
    assert unconfirmed.json()["error"]["code"] == "live_agent_confirmation_required"
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "live_agent_preview_stale"
    assert started.status_code == 201
    assert started.json()["run"]["status"] == "queued"
    assert boundary.resource.calls == []


def test_live_agent_preview_includes_validate_context_beyond_extraction_limit(
    tmp_path: Path,
) -> None:
    boundary = _InMemoryOpenAIClient()
    config = _config(tmp_path, live=True)
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case = _create_case(client, template_id="warranty")
        _upload_evidence(client, case["id"], "initial filler segment")

    connection = connect_database(config.database_path)
    try:
        source = connection.execute(
            "SELECT id FROM source WHERE case_id = ?", (case["id"],)
        ).fetchone()
        assert source is not None
        inserted_texts = [f"filler segment {sequence}" for sequence in range(1, 32)]
        inserted_texts.append("synthetic warranty marker in validate-only segment")
        with connection:
            connection.executemany(
                """
                INSERT INTO source_segment (
                    id, source_id, sequence, page, bbox_json, text,
                    extraction_method, confidence, text_sha256
                ) VALUES (?, ?, ?, NULL, NULL, ?, 'synthetic', 1.0, ?)
                """,
                [
                    (
                        f"seg_preview_{sequence:03d}",
                        str(source["id"]),
                        sequence,
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    )
                    for sequence, text in enumerate(inserted_texts, start=1)
                ],
            )
            connection.execute(
                """
                INSERT INTO source_segment_fts (
                    rowid, source_id, segment_id, text, text_sha256
                )
                SELECT rowid, source_id, id, text, text_sha256
                FROM source_segment WHERE id LIKE 'seg_preview_%'
                ORDER BY sequence, id
                """
            )
    finally:
        connection.close()

    with TestClient(create_app(config, openai_client=boundary)) as client:
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent")

    assert preview.status_code == 200
    assert preview.json()["preview"]["evidence"] == {
        "segment_count": 33,
        "utf8_bytes": len(b"initial filler segment")
        + sum(len(text.encode("utf-8")) for text in inserted_texts),
    }
    assert boundary.resource.calls == []


def test_fake_and_live_routes_reject_each_others_runs_and_live_cancel_is_offline(
    tmp_path: Path,
) -> None:
    boundary = _InMemoryOpenAIClient()
    config = replace(_config(tmp_path, live=True), portfolio_agent_demo_enabled=True)
    with TestClient(create_app(config, openai_client=boundary)) as client:
        live_case = _create_case(client, template_id="warranty")
        live_preview = client.get(f"/api/v1/cases/{live_case['id']}/live-agent").json()
        client.post(
            f"/api/v1/cases/{live_case['id']}/live-agent",
            json={
                "preview_sha256": live_preview["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )
        fake_rejected = client.get(f"/api/v1/cases/{live_case['id']}/agent-run")
        cancelled = client.post(f"/api/v1/cases/{live_case['id']}/live-agent/cancel")

        fake_case = _create_case(client, template_id="warranty")
        fake_started = client.post(f"/api/v1/cases/{fake_case['id']}/agent-run")
        live_rejected = client.get(f"/api/v1/cases/{fake_case['id']}/live-agent")

    assert fake_rejected.status_code == 409
    assert fake_rejected.json()["error"]["code"] == "agent_run_mode_conflict"
    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["status"] == "cancelled"
    assert fake_started.status_code == 201
    assert live_rejected.status_code == 409
    assert live_rejected.json()["error"]["code"] == "agent_run_mode_conflict"
    assert boundary.resource.calls == []


def test_live_agent_in_memory_client_completes_read_only_product_run(tmp_path: Path) -> None:
    boundary = _InMemoryOpenAIClient()
    config = _config(tmp_path, live=True)
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case = _create_case(client, template_id="warranty")
        _upload_evidence(
            client,
            case["id"],
            (
                "Merchant Alpha Store. Product SynthPhone. Order ORD-001. "
                "Paid CNY 259.00. Serial prefix SYN. synthetic warranty marker."
            ),
        )
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent").json()
        queued = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": preview["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )
        completed = client.post(f"/api/v1/cases/{case['id']}/live-agent/execute")
        case_after = client.get(f"/api/v1/cases/{case['id']}")
        deadlines = client.get(f"/api/v1/cases/{case['id']}/deadlines")
        actions = client.get(f"/api/v1/cases/{case['id']}/actions")

    assert queued.status_code == 201
    assert completed.status_code == 200, completed.text
    run = completed.json()["run"]
    assert run["status"] == "completed"
    assert [stage["name"] for stage in run["stages"]] == [
        "extract",
        "validate",
    ]
    assert all(stage["provider"] == "openai" for stage in run["stages"])
    assert {claim["status"] for claim in run["claims"]} <= {"candidate", "inferred"}
    assert run["answer"]["status"] in {"answered", "unknown"}
    assert len(boundary.resource.calls) == 2
    assert all(call["store"] is False for call in boundary.resource.calls)
    assert all("tools" not in call for call in boundary.resource.calls)
    assert case_after.status_code == 200
    assert deadlines.json() == []
    assert actions.json() == []

    sqlite_bytes = config.database_path.read_bytes()
    forbidden_values = (
        b"FORBIDDEN_CREDENTIAL_CANARY",
        b"private.example",
        b"PRIVATE-RAW-RESPONSE-MARKER",
        str(tmp_path).encode(),
    )
    assert all(value not in sqlite_bytes for value in forbidden_values)


def test_live_agent_groq_requires_zdr_before_any_injected_client_call(tmp_path: Path) -> None:
    boundary = _InMemoryGroqClient()
    config = _groq_config(tmp_path, zdr=False)

    with TestClient(create_app(config, groq_client=boundary)) as client:
        case = _create_case(client, template_id="warranty")
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent")
        start = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": preview.json()["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )

    assert preview.status_code == 200
    assert preview.json()["capability"]["status"] == "unavailable"
    assert preview.json()["capability"]["provider"] == "groq"
    assert "ZDR" in preview.json()["capability"]["action"]
    assert start.status_code == 503
    assert start.json()["error"]["code"] == "live_agent_provider_unavailable"
    assert boundary.chat.completions.calls == []


def test_live_agent_groq_rejects_nonofficial_endpoint_before_authorization(
    tmp_path: Path,
) -> None:
    boundary = _InMemoryGroqClient(endpoint="https://private.example/v1/chat/completions")
    config = _groq_config(tmp_path, zdr=True)

    with TestClient(create_app(config, groq_client=boundary)) as client:
        case = _create_case(client, template_id="warranty")
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent")
        start = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": preview.json()["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )

    assert preview.status_code == 200
    assert preview.json()["capability"]["status"] == "unavailable"
    assert preview.json()["capability"]["provider"] == "groq"
    assert start.status_code == 503
    assert start.json()["error"]["code"] == "live_agent_client_unavailable"
    assert boundary.chat.completions.calls == []


def test_live_agent_in_memory_groq_client_completes_read_only_product_run(
    tmp_path: Path,
) -> None:
    boundary = _InMemoryGroqClient()
    config = _groq_config(tmp_path, zdr=True)

    with TestClient(create_app(config, groq_client=boundary)) as client:
        case = _create_case(client, template_id="warranty")
        _upload_evidence(
            client,
            case["id"],
            "Merchant Groq Synthetic. Order GROQ-001. synthetic warranty marker.",
        )
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent").json()
        queued = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": preview["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )
        completed = client.post(f"/api/v1/cases/{case['id']}/live-agent/execute")
        deadlines = client.get(f"/api/v1/cases/{case['id']}/deadlines")
        actions = client.get(f"/api/v1/cases/{case['id']}/actions")

    assert preview["capability"] == {
        "status": "ready",
        "provider": "groq",
        "model_id": GROQ_CHAT_MODEL_ID,
        "action": "Review the server preview and explicitly authorize one read-only run.",
        "supported_case_types": ["warranty"],
    }
    assert queued.status_code == 201
    assert completed.status_code == 200, completed.text
    run = completed.json()["run"]
    assert run["status"] == "completed"
    assert all(stage["provider"] == "groq" for stage in run["stages"])
    assert {claim["status"] for claim in run["claims"]} <= {"candidate", "inferred"}
    assert run["answer"]["status"] in {"answered", "unknown"}
    assert len(boundary.chat.completions.calls) == 2
    assert all(call["stream"] is False for call in boundary.chat.completions.calls)
    assert all(call["tool_choice"] == "none" for call in boundary.chat.completions.calls)
    assert all("tools" not in call for call in boundary.chat.completions.calls)
    assert all("store" not in call for call in boundary.chat.completions.calls)
    assert deadlines.json() == []
    assert actions.json() == []
    sqlite_bytes = config.database_path.read_bytes()
    assert b"PRIVATE-GROQ-RAW-RESPONSE" not in sqlite_bytes
    assert str(tmp_path).encode() not in sqlite_bytes


def test_live_agent_mistral_requires_zdr_before_any_injected_client_call(tmp_path: Path) -> None:
    boundary = _InMemoryMistralClient()
    config = _mistral_config(tmp_path, zdr=False)

    with TestClient(create_app(config, mistral_client=boundary)) as client:
        case = _create_case(client, template_id="warranty")
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent")
        start = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": preview.json()["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )

    assert preview.status_code == 200
    assert preview.json()["capability"]["status"] == "unavailable"
    assert preview.json()["capability"]["provider"] == "mistral"
    assert "ZDR" in preview.json()["capability"]["action"]
    assert start.status_code == 503
    assert boundary.chat.completions.calls == []


def test_live_agent_in_memory_mistral_client_completes_read_only_product_run(
    tmp_path: Path,
) -> None:
    boundary = _InMemoryMistralClient()
    config = _mistral_config(tmp_path, zdr=True)

    with TestClient(create_app(config, mistral_client=boundary)) as client:
        case = _create_case(client, template_id="warranty")
        _upload_evidence(client, case["id"], "Merchant Mistral Synthetic. Order MS-001.")
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent").json()
        queued = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": preview["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )
        completed = client.post(f"/api/v1/cases/{case['id']}/live-agent/execute")
        deadlines = client.get(f"/api/v1/cases/{case['id']}/deadlines")
        actions = client.get(f"/api/v1/cases/{case['id']}/actions")

    assert preview["capability"] == {
        "status": "ready",
        "provider": "mistral",
        "model_id": MISTRAL_CHAT_MODEL_ID,
        "action": "Review the server preview and explicitly authorize one read-only run.",
        "supported_case_types": ["warranty"],
    }
    assert queued.status_code == 201
    assert completed.status_code == 200, completed.text
    run = completed.json()["run"]
    assert run["status"] == "completed"
    assert all(stage["provider"] == "mistral" for stage in run["stages"])
    assert {claim["status"] for claim in run["claims"]} <= {"candidate", "inferred"}
    assert run["answer"]["status"] in {"answered", "unknown"}
    assert len(boundary.chat.completions.calls) == 2
    assert all(call["stream"] is False for call in boundary.chat.completions.calls)
    assert all(call["tool_choice"] == "none" for call in boundary.chat.completions.calls)
    assert all("tools" not in call for call in boundary.chat.completions.calls)
    assert all("store" not in call for call in boundary.chat.completions.calls)
    assert deadlines.json() == []
    assert actions.json() == []
    sqlite_bytes = config.database_path.read_bytes()
    assert b"PRIVATE-MISTRAL-RAW-RESPONSE" not in sqlite_bytes
    assert str(tmp_path).encode() not in sqlite_bytes


def test_live_agent_deepseek_requires_server_data_use_confirmation(
    tmp_path: Path,
) -> None:
    boundary = _InMemoryDeepSeekClient()
    config = _deepseek_config(tmp_path, confirmed=False)

    with TestClient(create_app(config, deepseek_client=boundary)) as client:
        case = _create_case(client, template_id="warranty")
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent")
        start = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": preview.json()["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )

    assert preview.status_code == 200
    assert preview.json()["capability"]["status"] == "unavailable"
    assert preview.json()["capability"]["provider"] == "deepseek"
    assert "data use" in preview.json()["capability"]["action"]
    assert start.status_code == 503
    assert start.json()["error"]["code"] == "live_agent_provider_unavailable"
    assert boundary.calls == []


def test_live_agent_in_memory_deepseek_client_completes_read_only_product_run(
    tmp_path: Path,
) -> None:
    boundary = _InMemoryDeepSeekClient()
    config = _deepseek_config(tmp_path, confirmed=True)

    with TestClient(create_app(config, deepseek_client=boundary)) as client:
        case = _create_case(client, template_id="warranty")
        _upload_evidence(client, case["id"], "Merchant DeepSeek Synthetic. Order DS-001.")
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent").json()
        queued = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": preview["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )
        completed = client.post(f"/api/v1/cases/{case['id']}/live-agent/execute")
        deadlines = client.get(f"/api/v1/cases/{case['id']}/deadlines")
        actions = client.get(f"/api/v1/cases/{case['id']}/actions")

    assert preview["capability"] == {
        "status": "ready",
        "provider": "deepseek",
        "model_id": DEEPSEEK_CHAT_MODEL_ID,
        "action": "Review the server preview and explicitly authorize one read-only run.",
        "supported_case_types": ["warranty"],
    }
    assert preview["preview"]["budget"]["max_input_tokens"] == 56_000
    assert preview["preview"]["budget"]["max_output_tokens"] == 56_000
    assert preview["preview"]["budget"]["max_total_tokens"] == 112_000
    assert queued.status_code == 201
    assert completed.status_code == 200, completed.text
    run = completed.json()["run"]
    assert run["status"] == "completed"
    assert all(stage["provider"] == "deepseek" for stage in run["stages"])
    assert run["provider"] == "deepseek"
    assert run["model_id"] == DEEPSEEK_CHAT_MODEL_ID
    assert all(stage["model_id"] == DEEPSEEK_CHAT_MODEL_ID for stage in run["stages"])
    assert {claim["status"] for claim in run["claims"]} <= {"candidate", "inferred"}
    assert run["answer"]["status"] in {"answered", "unknown"}
    assert len(boundary.calls) == 2
    assert all(call["stream"] is False for call in boundary.calls)
    assert all(call["tool_choice"] == "none" for call in boundary.calls)
    assert all(call["thinking"] == {"type": "disabled"} for call in boundary.calls)
    assert all(call["max_tokens"] == 4_000 for call in boundary.calls)
    assert all("tools" not in call for call in boundary.calls)
    assert all("store" not in call for call in boundary.calls)
    assert deadlines.json() == []
    assert actions.json() == []
    sqlite_bytes = config.database_path.read_bytes()
    assert b"PRIVATE-DEEPSEEK-RAW-RESPONSE" not in sqlite_bytes
    assert str(tmp_path).encode() not in sqlite_bytes


def test_live_agent_long_goal_retrieves_terms_instead_of_one_exact_phrase(
    tmp_path: Path,
) -> None:
    boundary = _InMemoryDeepSeekClient(model_id=DEEPSEEK_CHAT_PRO_MODEL_ID)
    config = _deepseek_config(
        tmp_path,
        confirmed=True,
        model_id=DEEPSEEK_CHAT_PRO_MODEL_ID,
    )
    with TestClient(create_app(config, deepseek_client=boundary)) as client:
        case = _create_case(
            client,
            template_id="warranty",
            goal="核验滚筒洗衣机无法启动是否属于保修范围并整理送修材料",
        )
        _upload_evidence(
            client,
            case["id"],
            "海岚滚筒洗衣机购买后无法启动，现申请保修并安排送修。",
        )
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent").json()
        client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": preview["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )
        completed = client.post(f"/api/v1/cases/{case['id']}/live-agent/execute")

    assert completed.status_code == 200, completed.text
    run = completed.json()["run"]
    assert run["answer"]["status"] == "answered"
    assert run["answer"]["citations"]
    assert run["model_id"] == DEEPSEEK_CHAT_PRO_MODEL_ID


def test_live_agent_english_appliance_goal_retrieves_model_fault_and_warranty(
    tmp_path: Path,
) -> None:
    boundary = _InMemoryDeepSeekClient(model_id=DEEPSEEK_CHAT_PRO_MODEL_ID)
    config = _deepseek_config(
        tmp_path,
        confirmed=True,
        model_id=DEEPSEEK_CHAT_PRO_MODEL_ID,
    )
    with TestClient(create_app(config, deepseek_client=boundary)) as client:
        case = _create_case(
            client,
            template_id="warranty",
            goal=(
                "Verify HAIXING-H8 E21 warranty coverage and prepare facts for on-site inspection."
            ),
        )
        _upload_evidence(
            client,
            case["id"],
            "HAIXING-H8 front-loading washing machine has a 36-month warranty.",
        )
        _upload_evidence(
            client,
            case["id"],
            "Purchase record for HAIXING-H8 dated 2026-07-15.",
        )
        _upload_evidence(
            client,
            case["id"],
            "HAIXING-H8 stopped during drainage with fault code E21; request repair service.",
        )
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent").json()
        client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": preview["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )
        completed = client.post(f"/api/v1/cases/{case['id']}/live-agent/execute")

    assert completed.status_code == 200, completed.text
    run = completed.json()["run"]
    assert run["answer"]["status"] == "answered"
    assert run["answer"]["citations"]
    assert run["model_id"] == DEEPSEEK_CHAT_PRO_MODEL_ID


def test_live_agent_retrieval_fallback_does_not_admit_unrelated_case_evidence(
    tmp_path: Path,
) -> None:
    boundary = _InMemoryDeepSeekClient(model_id=DEEPSEEK_CHAT_PRO_MODEL_ID)
    config = _deepseek_config(
        tmp_path,
        confirmed=True,
        model_id=DEEPSEEK_CHAT_PRO_MODEL_ID,
    )
    with TestClient(create_app(config, deepseek_client=boundary)) as client:
        case = _create_case(
            client,
            template_id="warranty",
            goal="zeta-no-match-0000",
        )
        _upload_evidence(client, case["id"], "synthetic warranty marker")
        preview = client.get(f"/api/v1/cases/{case['id']}/live-agent").json()
        client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": preview["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )
        completed = client.post(f"/api/v1/cases/{case['id']}/live-agent/execute")

    assert completed.status_code == 200, completed.text
    assert completed.json()["run"]["answer"]["status"] == "unknown"


def test_live_agent_reanalysis_preserves_prior_run_and_creates_new_version(
    tmp_path: Path,
) -> None:
    boundary = _InMemoryDeepSeekClient(model_id=DEEPSEEK_CHAT_PRO_MODEL_ID)
    config = _deepseek_config(
        tmp_path,
        confirmed=True,
        model_id=DEEPSEEK_CHAT_PRO_MODEL_ID,
    )
    with TestClient(create_app(config, deepseek_client=boundary)) as client:
        case = _create_case(client, template_id="warranty")
        _upload_evidence(client, case["id"], "synthetic warranty marker")
        first_preview = client.get(f"/api/v1/cases/{case['id']}/live-agent").json()
        client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": first_preview["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )
        first = client.post(f"/api/v1/cases/{case['id']}/live-agent/execute").json()
        second = client.post(
            f"/api/v1/cases/{case['id']}/live-agent",
            json={
                "preview_sha256": first["preview"]["preview_sha256"],
                "confirm_read_only": True,
            },
        )

    assert second.status_code == 201, second.text
    body = second.json()
    assert body["run"]["status"] == "queued"
    assert body["run"]["id"] != first["run"]["id"]
    assert [item["version"] for item in body["run_history"]] == [2, 1]
    assert body["run_history"][1]["id"] == first["run"]["id"]
    assert body["run_history"][1]["status"] == "completed"
    assert body["run_history"][1]["model_id"] == DEEPSEEK_CHAT_PRO_MODEL_ID


def _config(tmp_path: Path, *, live: bool = False) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "lifecase.sqlite3",
        data_root=tmp_path / "data",
        upload_root=tmp_path / "uploads",
        model_provider=(
            ModelProviderConfig(
                provider=ModelProviderKind.OPENAI,
                model_id="gpt-live-synthetic",
            )
            if live
            else ModelProviderConfig()
        ),
        live_agent_enabled=live,
    )


def _groq_config(tmp_path: Path, *, zdr: bool) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "lifecase.sqlite3",
        data_root=tmp_path / "data",
        upload_root=tmp_path / "uploads",
        model_provider=ModelProviderConfig(
            provider=ModelProviderKind.GROQ,
            model_id=GROQ_CHAT_MODEL_ID,
        ),
        live_agent_enabled=True,
        groq_zdr_confirmed=zdr,
    )


def _mistral_config(tmp_path: Path, *, zdr: bool) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "lifecase.sqlite3",
        data_root=tmp_path / "data",
        upload_root=tmp_path / "uploads",
        model_provider=ModelProviderConfig(
            provider=ModelProviderKind.MISTRAL,
            model_id=MISTRAL_CHAT_MODEL_ID,
        ),
        live_agent_enabled=True,
        mistral_zdr_confirmed=zdr,
    )


def _deepseek_config(
    tmp_path: Path,
    *,
    confirmed: bool,
    model_id: str = DEEPSEEK_CHAT_MODEL_ID,
) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "lifecase.sqlite3",
        data_root=tmp_path / "data",
        upload_root=tmp_path / "uploads",
        model_provider=ModelProviderConfig(
            provider=ModelProviderKind.DEEPSEEK,
            model_id=model_id,
        ),
        live_agent_enabled=True,
        deepseek_data_use_confirmed=confirmed,
    )


def _create_case(
    client: TestClient,
    *,
    template_id: str,
    goal: str = "synthetic warranty marker",
) -> dict[str, object]:
    response = client.post(
        "/api/v1/cases",
        json={
            "title": "Synthetic Live Agent Case",
            "goal": goal,
            "template_id": template_id,
        },
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def _upload_evidence(client: TestClient, case_id: object, text: str) -> None:
    response = client.post(
        f"/api/v1/cases/{case_id}/sources",
        files={"file": ("synthetic-live.txt", text, "text/plain")},
    )
    assert response.status_code == 201
