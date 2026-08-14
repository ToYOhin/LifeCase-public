"""Gemini GenerateContent and Anthropic Messages structured-output adapters."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from lifecase.providers.config import ModelProviderConfig
from lifecase.providers.errors import (
    ModelProviderError,
    ProviderCancelledError,
    ProviderErrorCode,
    ProviderFailureMetadata,
    ProviderMalformedResponseError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderRemoteError,
    ProviderRequestSizeError,
    ProviderResponseSizeError,
    ProviderTimeoutError,
)
from lifecase.providers.models import (
    ANTHROPIC_MESSAGES_MODEL_ID,
    GEMINI_GENERATE_MODEL_ID,
    ModelProviderKind,
    ProviderCallMetadata,
    ProviderModelError,
    ProviderUsage,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    canonical_json_object,
)

GEMINI_GENERATE_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_GENERATE_MODEL_ID}:generateContent"
)
ANTHROPIC_MESSAGES_ENDPOINT = "https://api.anthropic.com/v1/messages"


class _OutputTooLargeError(ValueError):
    pass


@runtime_checkable
class DirectJsonClient(Protocol):
    endpoint: str

    def create(self, **kwargs: Any) -> Awaitable[Any]: ...


class GeminiGenerateProvider:
    def __init__(
        self,
        config: ModelProviderConfig,
        client: DirectJsonClient,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if (
            config.provider is not ModelProviderKind.GEMINI
            or config.model_id != GEMINI_GENERATE_MODEL_ID
            or client.endpoint != GEMINI_GENERATE_ENDPOINT
        ):
            raise ValueError("Gemini provider requires the fixed Gemini configuration")
        self._config = config
        self._client = client
        self._clock = clock

    async def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResult:
        return await _generate(self, request)

    def payload(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        return {
            "system_instruction": {
                "parts": [
                    {
                        "text": (
                            f"{request.system_instruction}\n\n"
                            f"Stage instruction:\n{request.stage_instruction}"
                        )
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": request.input_json}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": request.output_schema(),
                "maxOutputTokens": request.max_output_tokens,
                "temperature": 0,
            },
        }

    def map_response(
        self, request: StructuredGenerationRequest, raw: object, latency_ms: int
    ) -> StructuredGenerationResult:
        candidates = _field(raw, "candidates")
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ProviderModelError("Provider response is invalid")
        candidate = candidates[0]
        if _field(candidate, "finishReason") != "STOP":
            raise ProviderModelError("Provider response is invalid")
        parts = _field(_field(candidate, "content"), "parts")
        if not isinstance(parts, list) or len(parts) != 1:
            raise ProviderModelError("Provider response is invalid")
        output = _required_text(parts[0], "text")
        usage = _field(raw, "usageMetadata")
        return _result(
            request,
            output,
            provider=ModelProviderKind.GEMINI,
            model_id=GEMINI_GENERATE_MODEL_ID,
            input_tokens=_tokens(_field(usage, "promptTokenCount")),
            output_tokens=_tokens(_field(usage, "candidatesTokenCount")),
            total_tokens=_tokens(_field(usage, "totalTokenCount")),
            latency_ms=latency_ms,
            max_output_bytes=self._config.max_output_bytes,
        )


class AnthropicMessagesProvider:
    def __init__(
        self,
        config: ModelProviderConfig,
        client: DirectJsonClient,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if (
            config.provider is not ModelProviderKind.ANTHROPIC
            or config.model_id != ANTHROPIC_MESSAGES_MODEL_ID
            or client.endpoint != ANTHROPIC_MESSAGES_ENDPOINT
        ):
            raise ValueError("Anthropic provider requires the fixed Anthropic configuration")
        self._config = config
        self._client = client
        self._clock = clock

    async def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResult:
        return await _generate(self, request)

    def payload(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        return {
            "model": ANTHROPIC_MESSAGES_MODEL_ID,
            "max_tokens": request.max_output_tokens,
            "system": (
                f"{request.system_instruction}\n\nStage instruction:\n{request.stage_instruction}"
            ),
            "messages": [{"role": "user", "content": request.input_json}],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": request.output_schema(),
                }
            },
            "temperature": 0,
        }

    def map_response(
        self, request: StructuredGenerationRequest, raw: object, latency_ms: int
    ) -> StructuredGenerationResult:
        if _field(raw, "stop_reason") != "end_turn":
            raise ProviderModelError("Provider response is invalid")
        content = _field(raw, "content")
        if not isinstance(content, list) or len(content) != 1:
            raise ProviderModelError("Provider response is invalid")
        if _field(content[0], "type") != "text":
            raise ProviderModelError("Provider response is invalid")
        output = _required_text(content[0], "text")
        model_id = _required_text(raw, "model")
        usage = _field(raw, "usage")
        input_tokens = _tokens(_field(usage, "input_tokens"))
        output_tokens = _tokens(_field(usage, "output_tokens"))
        return _result(
            request,
            output,
            provider=ModelProviderKind.ANTHROPIC,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            latency_ms=latency_ms,
            max_output_bytes=self._config.max_output_bytes,
        )


async def _generate(
    provider: GeminiGenerateProvider | AnthropicMessagesProvider,
    request: StructuredGenerationRequest,
) -> StructuredGenerationResult:
    if request.input_bytes > provider._config.max_input_bytes:
        raise _failure(
            provider, request, ProviderRequestSizeError, ProviderErrorCode.REQUEST_TOO_LARGE
        )
    started = provider._clock()
    try:
        raw = await asyncio.wait_for(
            provider._client.create(**provider.payload(request)),
            timeout=provider._config.timeout_seconds,
        )
    except asyncio.CancelledError:
        raise _failure(
            provider, request, ProviderCancelledError, ProviderErrorCode.CANCELLED
        ) from None
    except TimeoutError:
        raise _failure(provider, request, ProviderTimeoutError, ProviderErrorCode.TIMEOUT) from None
    except Exception as error:
        status_code = getattr(error, "status_code", None)
        if status_code == 429:
            error_type: type[ModelProviderError] = ProviderRateLimitError
            code = ProviderErrorCode.RATE_LIMITED
        elif isinstance(error, (ConnectionError, OSError)):
            error_type = ProviderNetworkError
            code = ProviderErrorCode.NETWORK
        else:
            error_type = ProviderRemoteError
            code = ProviderErrorCode.REMOTE
        raise _failure(provider, request, error_type, code) from None
    latency_ms = max(0, int((provider._clock() - started) * 1000))
    try:
        return provider.map_response(request, raw, latency_ms)
    except _OutputTooLargeError:
        raise _failure(
            provider,
            request,
            ProviderResponseSizeError,
            ProviderErrorCode.RESPONSE_TOO_LARGE,
            latency_ms=latency_ms,
        ) from None
    except (ProviderModelError, TypeError, ValueError, json.JSONDecodeError):
        raise _failure(
            provider,
            request,
            ProviderMalformedResponseError,
            ProviderErrorCode.MALFORMED_RESPONSE,
            latency_ms=latency_ms,
        ) from None


def _result(
    request: StructuredGenerationRequest,
    output: str,
    *,
    provider: ModelProviderKind,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    latency_ms: int,
    max_output_bytes: int,
) -> StructuredGenerationResult:
    if len(output.encode()) > max_output_bytes:
        raise _OutputTooLargeError("Provider response is too large")
    output_json = canonical_json_object(json.loads(output), maximum_bytes=max_output_bytes)
    return StructuredGenerationResult(
        request_id=request.request_id,
        output_json=output_json,
        metadata=ProviderCallMetadata(
            provider=provider,
            model_id=model_id,
            request_scope_sha256=request.request_scope_sha256,
            latency_ms=latency_ms,
            usage=ProviderUsage(input_tokens, output_tokens, total_tokens),
        ),
    )


def _failure(
    provider: GeminiGenerateProvider | AnthropicMessagesProvider,
    request: StructuredGenerationRequest,
    error_type: type[ModelProviderError],
    code: ProviderErrorCode,
    *,
    latency_ms: int = 0,
) -> ModelProviderError:
    retryable = code in {
        ProviderErrorCode.NETWORK,
        ProviderErrorCode.RATE_LIMITED,
        ProviderErrorCode.TIMEOUT,
    }
    return error_type(
        "Model provider request failed",
        code=code,
        retryable=retryable,
        metadata=ProviderFailureMetadata(
            provider=provider._config.provider,
            model_id=provider._config.model_id,
            request_scope_sha256=request.request_scope_sha256,
            latency_ms=latency_ms,
            error_code=code,
        ),
    )


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _required_text(value: object, name: str) -> str:
    field = _field(value, name)
    if not isinstance(field, str) or not field:
        raise ProviderModelError("Provider response is invalid")
    return field


def _tokens(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProviderModelError("Provider usage is invalid")
    return value
