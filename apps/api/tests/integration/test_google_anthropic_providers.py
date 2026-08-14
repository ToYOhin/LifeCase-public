"""Offline Gemini and Anthropic structured Provider contract coverage."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

import pytest

from lifecase.providers import (
    ANTHROPIC_MESSAGES_ENDPOINT,
    ANTHROPIC_MESSAGES_MODEL_ID,
    GEMINI_GENERATE_ENDPOINT,
    GEMINI_GENERATE_MODEL_ID,
    AnthropicMessagesProvider,
    GeminiGenerateProvider,
    ModelProviderConfig,
    ModelProviderKind,
    ProviderRegistry,
    ProviderResponseSizeError,
    ProviderRuntimeClients,
    ProviderUsage,
    StructuredGenerationRequest,
    request_scope_sha256,
)


class _Client:
    def __init__(self, endpoint: str, outcome: object) -> None:
        self.endpoint = endpoint
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Awaitable[Any]:
        self.calls.append(kwargs)

        async def execute() -> Any:
            return self.outcome

        return execute()


def _request() -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        request_id="request-p1-11",
        request_scope_sha256=request_scope_sha256("synthetic"),
        system_instruction="Use only the supplied synthetic evidence.",
        stage_instruction="Return one candidate fact.",
        input_json='{"evidence":"synthetic warranty record"}',
        output_schema_name="candidate_fact",
        output_schema_json=(
            '{"additionalProperties":false,"properties":{"status":{"type":"string"}},'
            '"required":["status"],"type":"object"}'
        ),
        max_output_tokens=200,
    )


def test_gemini_adapter_uses_generate_content_structured_json_without_tools() -> None:
    client = _Client(
        GEMINI_GENERATE_ENDPOINT,
        {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": '{"status":"candidate"}'}]},
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 12,
                "candidatesTokenCount": 4,
                "totalTokenCount": 16,
            },
        },
    )
    config = ModelProviderConfig(
        provider=ModelProviderKind.GEMINI,
        model_id=GEMINI_GENERATE_MODEL_ID,
    )
    provider = GeminiGenerateProvider(config, client)

    result = asyncio.run(provider.generate_structured(_request()))

    assert result.output_json == '{"status":"candidate"}'
    assert result.metadata.provider is ModelProviderKind.GEMINI
    assert result.metadata.usage == ProviderUsage(12, 4, 16)
    payload = client.calls[0]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["responseJsonSchema"]["type"] == "object"
    assert "tools" not in payload
    assert "stream" not in payload


def test_anthropic_adapter_uses_messages_structured_output_without_tools() -> None:
    client = _Client(
        ANTHROPIC_MESSAGES_ENDPOINT,
        {
            "model": ANTHROPIC_MESSAGES_MODEL_ID,
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": '{"status":"candidate"}'}],
            "usage": {"input_tokens": 15, "output_tokens": 5},
        },
    )
    config = ModelProviderConfig(
        provider=ModelProviderKind.ANTHROPIC,
        model_id=ANTHROPIC_MESSAGES_MODEL_ID,
    )
    provider = AnthropicMessagesProvider(config, client)

    result = asyncio.run(provider.generate_structured(_request()))

    assert result.output_json == '{"status":"candidate"}'
    assert result.metadata.provider is ModelProviderKind.ANTHROPIC
    assert result.metadata.usage == ProviderUsage(15, 5, 20)
    payload = client.calls[0]
    assert payload["model"] == ANTHROPIC_MESSAGES_MODEL_ID
    assert payload["output_config"]["format"]["type"] == "json_schema"
    assert "tools" not in payload
    assert "stream" not in payload


def test_registry_composes_gemini_and_anthropic_only_with_confirmation() -> None:
    registry = ProviderRegistry()
    gemini_client = _Client(GEMINI_GENERATE_ENDPOINT, {})
    anthropic_client = _Client(ANTHROPIC_MESSAGES_ENDPOINT, {})
    gemini_config = ModelProviderConfig(
        provider=ModelProviderKind.GEMINI,
        model_id=GEMINI_GENERATE_MODEL_ID,
    )
    anthropic_config = ModelProviderConfig(
        provider=ModelProviderKind.ANTHROPIC,
        model_id=ANTHROPIC_MESSAGES_MODEL_ID,
    )

    gemini = registry.compose(
        gemini_config,
        clients=ProviderRuntimeClients(gemini=gemini_client),
        gemini_data_use_confirmed=True,
    )
    anthropic = registry.compose(
        anthropic_config,
        clients=ProviderRuntimeClients(anthropic=anthropic_client),
        anthropic_data_use_confirmed=True,
    )

    assert isinstance(gemini, GeminiGenerateProvider)
    assert isinstance(anthropic, AnthropicMessagesProvider)


def test_gemini_adapter_reports_oversized_output_without_exposing_content() -> None:
    client = _Client(
        GEMINI_GENERATE_ENDPOINT,
        {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": '{"status":"too-long"}'}]},
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            },
        },
    )
    provider = GeminiGenerateProvider(
        ModelProviderConfig(
            provider=ModelProviderKind.GEMINI,
            model_id=GEMINI_GENERATE_MODEL_ID,
            max_input_bytes=1_024,
            max_output_bytes=2,
        ),
        client,
    )

    with pytest.raises(ProviderResponseSizeError) as raised:
        asyncio.run(provider.generate_structured(_request()))

    assert raised.value.code.value == "provider_response_too_large"
    assert "too-long" not in str(raised.value)
