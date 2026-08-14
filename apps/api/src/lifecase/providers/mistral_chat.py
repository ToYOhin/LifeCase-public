"""Mistral Chat Completions adapter behind an injected process-local client."""

from __future__ import annotations

import asyncio
import json
import logging
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
    ProviderRefusalError,
    ProviderRemoteError,
    ProviderRequestSizeError,
    ProviderResponseSizeError,
    ProviderTimeoutError,
)
from lifecase.providers.models import (
    MISTRAL_CHAT_MODEL_ID,
    ModelProviderKind,
    ProviderCallMetadata,
    ProviderModelError,
    ProviderUsage,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    canonical_json_object,
)

_LOGGER = logging.getLogger(__name__)
MISTRAL_CHAT_ENDPOINT = "https://api.mistral.ai/v1/chat/completions"


class MistralChatCompletionsResource(Protocol):
    """Narrow async shape implemented by a credentialed Mistral chat resource."""

    def create(self, **kwargs: Any) -> Awaitable[Any]: ...


class MistralChatResource(Protocol):
    completions: MistralChatCompletionsResource


@runtime_checkable
class MistralChatClient(Protocol):
    """Injected client boundary; credential handling remains outside LifeCase."""

    endpoint: str
    chat: MistralChatResource


class MistralChatProvider:
    """Map fixed Mistral strict Chat output into provider-neutral values."""

    def __init__(
        self,
        config: ModelProviderConfig,
        client: MistralChatClient,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if (
            config.provider is not ModelProviderKind.MISTRAL
            or config.model_id != MISTRAL_CHAT_MODEL_ID
            or client.endpoint != MISTRAL_CHAT_ENDPOINT
        ):
            raise ValueError("Mistral provider requires the fixed Mistral configuration")
        self._config = config
        self._client = client
        self._clock = clock

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        if not isinstance(request, StructuredGenerationRequest):
            raise ProviderMalformedResponseError(
                "Model provider request is invalid",
                code=ProviderErrorCode.MALFORMED_RESPONSE,
                retryable=False,
            )
        if request.input_bytes > self._config.max_input_bytes:
            error = self._failure(
                request,
                None,
                ProviderRequestSizeError,
                ProviderErrorCode.REQUEST_TOO_LARGE,
                retryable=False,
                message="Model provider request exceeds the configured limit",
                latency_ms=0,
            )
            _log_failure(error)
            raise error

        started = self._clock()
        raw_response: object | None = None
        call_error: ModelProviderError | None = None
        try:
            raw_response = await asyncio.wait_for(
                self._client.chat.completions.create(**self._payload(request)),
                timeout=self._config.timeout_seconds,
            )
        except asyncio.CancelledError:
            call_error = self._failure(
                request,
                started,
                ProviderCancelledError,
                ProviderErrorCode.CANCELLED,
                retryable=False,
                message="Model provider call was cancelled",
            )
        except TimeoutError:
            call_error = self._failure(
                request,
                started,
                ProviderTimeoutError,
                ProviderErrorCode.TIMEOUT,
                retryable=True,
                message="Model provider call timed out",
            )
        except Exception as remote_error:
            call_error = self._remote_failure(request, started, remote_error)
        if call_error is not None:
            _log_failure(call_error)
            raise call_error
        if raw_response is None:
            error = self._failure(
                request,
                started,
                ProviderMalformedResponseError,
                ProviderErrorCode.MALFORMED_RESPONSE,
                retryable=False,
                message="Model provider response is invalid",
            )
            _log_failure(error)
            raise error

        latency_ms = _latency_ms(started, self._clock())
        try:
            result = self._map_response(request, raw_response, latency_ms)
        except ModelProviderError as error:
            _log_failure(error)
            raise error
        _log_success(result.metadata)
        return result

    def _payload(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        return {
            "model": MISTRAL_CHAT_MODEL_ID,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{request.system_instruction}\n\n"
                        f"Stage instruction:\n{request.stage_instruction}"
                    ),
                },
                {"role": "user", "content": request.input_json},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.output_schema_name,
                    "strict": True,
                    "schema": request.output_schema(),
                },
            },
            "max_tokens": request.max_output_tokens,
            "temperature": 0,
            "stream": False,
            "tool_choice": "none",
        }

    def _map_response(
        self,
        request: StructuredGenerationRequest,
        raw_response: object,
        latency_ms: int,
    ) -> StructuredGenerationResult:
        choices = _field(raw_response, "choices")
        if not isinstance(choices, (list, tuple)) or len(choices) != 1:
            raise self._malformed(request, latency_ms)
        choice = choices[0]
        if _field(choice, "finish_reason") != "stop":
            raise self._malformed(request, latency_ms)
        message = _field(choice, "message")
        refusal = _field(message, "refusal")
        if isinstance(refusal, str) and refusal:
            raise self._failure(
                request,
                None,
                ProviderRefusalError,
                ProviderErrorCode.REFUSED,
                retryable=False,
                message="Model provider refused the request",
                latency_ms=latency_ms,
            )
        output_text = _field(message, "content")
        if not isinstance(output_text, str) or not output_text:
            raise self._malformed(request, latency_ms)
        if len(output_text.encode("utf-8")) > self._config.max_output_bytes:
            raise self._failure(
                request,
                None,
                ProviderResponseSizeError,
                ProviderErrorCode.RESPONSE_TOO_LARGE,
                retryable=False,
                message="Model provider response exceeds the configured limit",
                latency_ms=latency_ms,
            )
        result: StructuredGenerationResult | None = None
        try:
            output_json = canonical_json_object(
                json.loads(output_text),
                maximum_bytes=self._config.max_output_bytes,
            )
            usage = _response_usage(raw_response)
            model_id = _required_text(raw_response, "model")
            if model_id != MISTRAL_CHAT_MODEL_ID:
                raise ProviderModelError("Provider response model is invalid")
            metadata = ProviderCallMetadata(
                provider=ModelProviderKind.MISTRAL,
                model_id=model_id,
                request_scope_sha256=request.request_scope_sha256,
                latency_ms=latency_ms,
                usage=usage,
            )
            result = StructuredGenerationResult(
                request_id=request.request_id,
                output_json=output_json,
                metadata=metadata,
            )
        except (ProviderModelError, TypeError, ValueError, json.JSONDecodeError):
            pass
        if result is None:
            raise self._malformed(request, latency_ms)
        return result

    def _malformed(
        self,
        request: StructuredGenerationRequest,
        latency_ms: int,
    ) -> ProviderMalformedResponseError:
        return ProviderMalformedResponseError(
            "Model provider response is invalid",
            code=ProviderErrorCode.MALFORMED_RESPONSE,
            retryable=False,
            metadata=self._failure_metadata(
                request,
                latency_ms=latency_ms,
                code=ProviderErrorCode.MALFORMED_RESPONSE,
            ),
        )

    def _remote_failure(
        self,
        request: StructuredGenerationRequest,
        started: float,
        remote_error: Exception,
    ) -> ModelProviderError:
        class_name = type(remote_error).__name__.lower()
        status_code = getattr(remote_error, "status_code", None)
        if class_name in {"apitimeouterror", "timeouterror"}:
            error_type: type[ModelProviderError] = ProviderTimeoutError
            code = ProviderErrorCode.TIMEOUT
            retryable = True
            message = "Model provider call timed out"
        elif status_code == 429 or "ratelimit" in class_name:
            error_type = ProviderRateLimitError
            code = ProviderErrorCode.RATE_LIMITED
            retryable = True
            message = "Model provider rate limit was reached"
        elif isinstance(remote_error, (ConnectionError, OSError)) or "connection" in class_name:
            error_type = ProviderNetworkError
            code = ProviderErrorCode.NETWORK
            retryable = True
            message = "Model provider network request failed"
        else:
            error_type = ProviderRemoteError
            code = ProviderErrorCode.REMOTE
            retryable = status_code in {408, 409} or (
                isinstance(status_code, int) and status_code >= 500
            )
            message = "Model provider request failed"
        return self._failure(
            request,
            started,
            error_type,
            code,
            retryable=retryable,
            message=message,
        )

    def _failure(
        self,
        request: StructuredGenerationRequest,
        started: float | None,
        error_type: type[ModelProviderError],
        code: ProviderErrorCode,
        *,
        retryable: bool,
        message: str,
        latency_ms: int | None = None,
    ) -> ModelProviderError:
        if latency_ms is None:
            latency_ms = 0 if started is None else _latency_ms(started, self._clock())
        return error_type(
            message,
            code=code,
            retryable=retryable,
            metadata=self._failure_metadata(request, latency_ms=latency_ms, code=code),
        )

    def _failure_metadata(
        self,
        request: StructuredGenerationRequest,
        *,
        latency_ms: int,
        code: ProviderErrorCode,
    ) -> ProviderFailureMetadata:
        return ProviderFailureMetadata(
            provider=ModelProviderKind.MISTRAL,
            model_id=MISTRAL_CHAT_MODEL_ID,
            request_scope_sha256=request.request_scope_sha256,
            latency_ms=latency_ms,
            error_code=code,
        )


def _response_usage(raw_response: object) -> ProviderUsage:
    usage = _field(raw_response, "usage")
    return ProviderUsage(
        input_tokens=_token_count(_field(usage, "prompt_tokens")),
        output_tokens=_token_count(_field(usage, "completion_tokens")),
        total_tokens=_token_count(_field(usage, "total_tokens")),
    )


def _token_count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProviderModelError("Provider usage is invalid")
    return value


def _required_text(value: object, field_name: str) -> str:
    field = _field(value, field_name)
    if not isinstance(field, str) or not field.strip():
        raise ProviderModelError("Provider response field is invalid")
    return field.strip()


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _latency_ms(started: float, finished: float) -> int:
    return max(0, int((finished - started) * 1000))


def _log_success(metadata: ProviderCallMetadata) -> None:
    _LOGGER.info(
        "model_provider_call provider=%s model_id=%s scope_sha256=%s latency_ms=%d "
        "input_tokens=%d output_tokens=%d total_tokens=%d cost_minor_units=%s "
        "cost_currency=%s",
        metadata.provider,
        metadata.model_id,
        metadata.request_scope_sha256,
        metadata.latency_ms,
        metadata.usage.input_tokens,
        metadata.usage.output_tokens,
        metadata.usage.total_tokens,
        metadata.usage.cost_minor_units,
        metadata.usage.cost_currency,
    )


def _log_failure(error: ModelProviderError) -> None:
    metadata = error.metadata
    if metadata is None:
        return
    _LOGGER.warning(
        "model_provider_call provider=%s model_id=%s scope_sha256=%s latency_ms=%d "
        "error_code=%s retryable=%s",
        metadata.provider,
        metadata.model_id,
        metadata.request_scope_sha256,
        metadata.latency_ms,
        metadata.error_code,
        error.retryable,
    )
