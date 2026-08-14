"""Loopback-only Ollama structured-generation adapter."""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

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
    ModelProviderKind,
    ProviderCallMetadata,
    ProviderModelError,
    ProviderUsage,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    canonical_json_object,
)

_LOGGER = logging.getLogger(__name__)
_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434
_OLLAMA_CHAT_PATH = "/api/chat"
OLLAMA_CHAT_ENDPOINT = f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}{_OLLAMA_CHAT_PATH}"
_RESPONSE_ENVELOPE_BYTES = 16_384


@dataclass(frozen=True, slots=True)
class OllamaChatResponse:
    """Bounded transport result without raw error text."""

    status_code: int
    payload: object | None
    response_too_large: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.status_code, int)
            or isinstance(self.status_code, bool)
            or not 100 <= self.status_code <= 599
            or not isinstance(self.response_too_large, bool)
        ):
            raise ValueError("Ollama response metadata is invalid")


class OllamaChatClient(Protocol):
    """Narrow async client boundary for the fixed loopback chat endpoint."""

    async def chat(
        self,
        payload: Mapping[str, object],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> OllamaChatResponse: ...


class LoopbackOllamaChatClient:
    """Call only the native Ollama loopback endpoint using the standard library."""

    async def chat(
        self,
        payload: Mapping[str, object],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> OllamaChatResponse:
        return await asyncio.to_thread(
            _post_loopback,
            payload,
            timeout_seconds,
            max_response_bytes,
        )


class OllamaChatProvider:
    """Map provider-neutral structured requests to Ollama ``/api/chat``."""

    def __init__(
        self,
        config: ModelProviderConfig,
        client: OllamaChatClient | None = None,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if config.provider is not ModelProviderKind.OLLAMA or config.model_id is None:
            raise ValueError("Ollama provider requires Ollama configuration")
        self._config = config
        self._client = client or LoopbackOllamaChatClient()
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
            )
            _log_failure(error)
            raise error

        started = self._clock()
        call_error: ModelProviderError | None = None
        response: OllamaChatResponse | None = None
        try:
            response = await asyncio.wait_for(
                self._client.chat(
                    self._payload(request),
                    timeout_seconds=self._config.timeout_seconds,
                    max_response_bytes=(
                        self._config.max_output_bytes * 4 + _RESPONSE_ENVELOPE_BYTES
                    ),
                ),
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
        except (ConnectionError, OSError, http.client.HTTPException):
            call_error = self._failure(
                request,
                started,
                ProviderNetworkError,
                ProviderErrorCode.NETWORK,
                retryable=True,
                message="Model provider loopback request failed",
            )
        if call_error is not None:
            _log_failure(call_error)
            raise call_error
        if response is None:
            raise AssertionError("Ollama client returned no response")

        latency_ms = _latency_ms(started, self._clock())
        result = self._map_response(request, response, latency_ms)
        _log_success(result.metadata)
        return result

    def _payload(self, request: StructuredGenerationRequest) -> dict[str, object]:
        # Native contract: https://docs.ollama.com/api/chat
        # Structured output/thinking controls:
        # https://docs.ollama.com/capabilities/structured-outputs
        # https://docs.ollama.com/capabilities/thinking
        return {
            "model": self._config.model_id,
            "messages": [
                {"role": "system", "content": request.system_instruction},
                {"role": "user", "content": request.stage_instruction},
                {"role": "user", "content": request.input_json},
            ],
            "stream": False,
            "think": False,
            "format": request.output_schema(),
            "options": {
                "temperature": 0,
                "num_predict": request.max_output_tokens,
            },
        }

    def _map_response(
        self,
        request: StructuredGenerationRequest,
        response: OllamaChatResponse,
        latency_ms: int,
    ) -> StructuredGenerationResult:
        if response.response_too_large:
            raise self._mapped_failure(
                request,
                ProviderResponseSizeError,
                ProviderErrorCode.RESPONSE_TOO_LARGE,
                latency_ms,
                retryable=False,
                message="Model provider response exceeds the configured limit",
            )
        if response.status_code == 429:
            raise self._mapped_failure(
                request,
                ProviderRateLimitError,
                ProviderErrorCode.RATE_LIMITED,
                latency_ms,
                retryable=True,
                message="Model provider rate limit was reached",
            )
        if response.status_code != 200:
            raise self._mapped_failure(
                request,
                ProviderRemoteError,
                ProviderErrorCode.REMOTE,
                latency_ms,
                retryable=response.status_code in {408, 409} or response.status_code >= 500,
                message="Model provider request failed",
            )

        mapped: StructuredGenerationResult | None = None
        try:
            payload = _mapping(response.payload)
            if payload.get("done") is not True:
                raise ProviderModelError("Provider response is incomplete")
            message = _mapping(payload.get("message"))
            output_text = _required_text(message.get("content"))
            if len(output_text.encode("utf-8")) > self._config.max_output_bytes:
                raise self._mapped_failure(
                    request,
                    ProviderResponseSizeError,
                    ProviderErrorCode.RESPONSE_TOO_LARGE,
                    latency_ms,
                    retryable=False,
                    message="Model provider response exceeds the configured limit",
                )
            output_json = canonical_json_object(
                json.loads(output_text),
                maximum_bytes=self._config.max_output_bytes,
            )
            input_tokens = _token_count(payload.get("prompt_eval_count"))
            output_tokens = _token_count(payload.get("eval_count"))
            mapped = StructuredGenerationResult(
                request_id=request.request_id,
                output_json=output_json,
                metadata=ProviderCallMetadata(
                    provider=ModelProviderKind.OLLAMA,
                    model_id=_required_text(payload.get("model")),
                    request_scope_sha256=request.request_scope_sha256,
                    latency_ms=latency_ms,
                    usage=ProviderUsage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens,
                    ),
                ),
            )
        except ProviderResponseSizeError:
            raise
        except ModelProviderError:
            pass
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        if mapped is None:
            raise self._mapped_failure(
                request,
                ProviderMalformedResponseError,
                ProviderErrorCode.MALFORMED_RESPONSE,
                latency_ms,
                retryable=False,
                message="Model provider response is invalid",
            )
        return mapped

    def _mapped_failure(
        self,
        request: StructuredGenerationRequest,
        error_type: type[ModelProviderError],
        code: ProviderErrorCode,
        latency_ms: int,
        *,
        retryable: bool,
        message: str,
    ) -> ModelProviderError:
        error = self._failure(
            request,
            None,
            error_type,
            code,
            retryable=retryable,
            message=message,
            latency_ms=latency_ms,
        )
        _log_failure(error)
        return error

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
            metadata=ProviderFailureMetadata(
                provider=ModelProviderKind.OLLAMA,
                model_id=self._config.model_id,
                request_scope_sha256=request.request_scope_sha256,
                latency_ms=latency_ms,
                error_code=code,
            ),
        )


def _post_loopback(
    payload: Mapping[str, object],
    timeout_seconds: float,
    max_response_bytes: int,
) -> OllamaChatResponse:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    connection = http.client.HTTPConnection(
        _OLLAMA_HOST,
        _OLLAMA_PORT,
        timeout=timeout_seconds,
    )
    try:
        connection.request(
            "POST",
            _OLLAMA_CHAT_PATH,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        raw_body = response.read(max_response_bytes + 1)
        if len(raw_body) > max_response_bytes:
            return OllamaChatResponse(response.status, None, response_too_large=True)
        if response.status != 200:
            return OllamaChatResponse(response.status, None)
        try:
            decoded: object = json.loads(raw_body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            decoded = None
        return OllamaChatResponse(response.status, decoded)
    finally:
        connection.close()


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProviderModelError("Provider response is invalid")
    return value


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderModelError("Provider response field is invalid")
    return value.strip()


def _token_count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProviderModelError("Provider usage is invalid")
    return value


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
