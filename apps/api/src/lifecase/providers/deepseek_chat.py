"""Fixed DeepSeek JSON Object adapter behind an injected process-local client."""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

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
    DEEPSEEK_CHAT_MODEL_ID,
    DEEPSEEK_CHAT_MODEL_IDS,
    DEEPSEEK_CHAT_PRO_MODEL_ID,
    ModelProviderKind,
    ProviderCallMetadata,
    ProviderModelError,
    ProviderUsage,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    canonical_json_object,
)

DEEPSEEK_CHAT_ENDPOINT = "https://api.deepseek.com/chat/completions"
_LOGGER = logging.getLogger(__name__)
_MODEL_PRICES = {
    DEEPSEEK_CHAT_MODEL_ID: (Decimal("0.0028"), Decimal("0.14"), Decimal("0.28")),
    DEEPSEEK_CHAT_PRO_MODEL_ID: (Decimal("0.003625"), Decimal("0.435"), Decimal("0.87")),
}
_MILLION = Decimal(1_000_000)
_MIN_INPUT_TOKEN_RESERVATION = 4_000


class _SafeDeepSeekStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("DeepSeek request failed")


@runtime_checkable
class DeepSeekChatClient(Protocol):
    """Narrow injected client fixed to the official DeepSeek Chat endpoint."""

    endpoint: str

    def create(self, **kwargs: Any) -> Awaitable[Any]: ...


class DeepSeekHttpClient:
    """Fixed-host standard-library client whose credential remains process-local."""

    endpoint = DEEPSEEK_CHAT_ENDPOINT

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 120.0,
        max_response_bytes: int = 64_000,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("DeepSeek credential is missing")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("DeepSeek client bounds are invalid")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def create(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._create_sync, kwargs)

    def _create_sync(self, payload: dict[str, Any]) -> object:
        connection = http.client.HTTPSConnection(
            "api.deepseek.com",
            443,
            timeout=self._timeout_seconds,
            context=ssl.create_default_context(),
        )
        try:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            connection.request(
                "POST",
                "/chat/completions",
                body=body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            raw = response.read(self._max_response_bytes + 1)
            if response.status >= 400:
                raise _SafeDeepSeekStatusError(response.status)
            if len(raw) > self._max_response_bytes:
                raise ValueError("DeepSeek response exceeded the client limit")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("DeepSeek response is invalid")
            return value
        finally:
            connection.close()


class DeepSeekJsonProvider:
    """Map fixed DeepSeek JSON Object output into the canonical local validator."""

    def __init__(
        self,
        client: DeepSeekChatClient,
        *,
        model_id: str = DEEPSEEK_CHAT_MODEL_ID,
        timeout_seconds: float = 120.0,
        max_input_bytes: int = 100_000,
        max_output_bytes: int = 64_000,
        max_cost_usd: Decimal = Decimal("0.02"),
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if client.endpoint != DEEPSEEK_CHAT_ENDPOINT:
            raise ValueError("DeepSeek provider requires the fixed official endpoint")
        if model_id not in DEEPSEEK_CHAT_MODEL_IDS:
            raise ValueError("DeepSeek provider model is unsupported")
        if timeout_seconds <= 0 or max_input_bytes <= 0 or max_output_bytes <= 0:
            raise ValueError("DeepSeek provider bounds are invalid")
        if max_cost_usd <= 0:
            raise ValueError("DeepSeek provider cost cap is invalid")
        self._client = client
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._max_input_bytes = max_input_bytes
        self._max_output_bytes = max_output_bytes
        self._max_cost_usd = max_cost_usd
        self._nominal_cost_usd = Decimal("0")
        self._clock = clock

    @property
    def nominal_cost_usd(self) -> Decimal:
        return self._nominal_cost_usd

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        if request.input_bytes > self._max_input_bytes:
            raise self._failure(
                request,
                ProviderRequestSizeError,
                ProviderErrorCode.REQUEST_TOO_LARGE,
                "Model provider request exceeds the configured limit",
                retryable=False,
                latency_ms=0,
            )
        reserved_cost = nominal_deepseek_cost(
            input_tokens=max(_MIN_INPUT_TOKEN_RESERVATION, request.input_bytes),
            output_tokens=request.max_output_tokens,
            cache_hit_tokens=0,
            model_id=self._model_id,
        )
        if self._nominal_cost_usd + reserved_cost > self._max_cost_usd:
            raise self._failure(
                request,
                ProviderRemoteError,
                ProviderErrorCode.REMOTE,
                "Provider cost cap would be exceeded",
                retryable=False,
                latency_ms=0,
            )

        started = self._clock()
        call_error: ModelProviderError | None = None
        raw_response: object | None = None
        try:
            raw_response = await asyncio.wait_for(
                self._client.create(**self._payload(request)),
                timeout=self._timeout_seconds,
            )
        except asyncio.CancelledError:
            call_error = self._failure(
                request,
                ProviderCancelledError,
                ProviderErrorCode.CANCELLED,
                "Model provider call was cancelled",
                retryable=False,
                started=started,
            )
        except TimeoutError:
            call_error = self._failure(
                request,
                ProviderTimeoutError,
                ProviderErrorCode.TIMEOUT,
                "Model provider call timed out",
                retryable=True,
                started=started,
            )
        except Exception as remote_error:
            call_error = self._remote_failure(request, started, remote_error)
        if call_error is not None:
            _log_failure(call_error)
            raise call_error
        if raw_response is None:
            raise self._failure(
                request,
                ProviderMalformedResponseError,
                ProviderErrorCode.MALFORMED_RESPONSE,
                "Model provider response is invalid",
                retryable=False,
                started=started,
            )

        latency_ms = _latency_ms(started, self._clock())
        try:
            result, call_cost = self._map_response(request, raw_response, latency_ms)
        except ModelProviderError as error:
            _log_failure(error)
            raise
        self._nominal_cost_usd += call_cost
        _LOGGER.info(
            "model_provider_call provider=deepseek model_id=%s scope_sha256=%s "
            "latency_ms=%d input_tokens=%d output_tokens=%d total_tokens=%d",
            self._model_id,
            request.request_scope_sha256,
            latency_ms,
            result.metadata.usage.input_tokens,
            result.metadata.usage.output_tokens,
            result.metadata.usage.total_tokens,
        )
        return result

    def _payload(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        return {
            "model": self._model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{request.system_instruction}\n\n"
                        f"Stage instruction:\n{request.stage_instruction}\n\n"
                        "Return one JSON object matching this JSON Schema. Do not return "
                        "markdown or commentary. JSON Schema:\n"
                        f"{request.output_schema_json}"
                    ),
                },
                {"role": "user", "content": request.input_json},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
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
    ) -> tuple[StructuredGenerationResult, Decimal]:
        failure_class = "choices"
        try:
            choices = _field(raw_response, "choices")
            if not isinstance(choices, (list, tuple)) or len(choices) != 1:
                raise ProviderModelError("Provider response is invalid")
            failure_class = "finish_reason"
            choice = choices[0]
            finish_reason = _field(choice, "finish_reason")
            if finish_reason != "stop":
                if finish_reason in {
                    "length",
                    "content_filter",
                    "tool_calls",
                    "insufficient_system_resource",
                }:
                    failure_class = f"finish_reason_{finish_reason}"
                raise ProviderModelError("Provider response is invalid")
            failure_class = "content"
            output_text = _field(_field(choice, "message"), "content")
            if not isinstance(output_text, str) or not output_text:
                raise ProviderModelError("Provider response is invalid")
            if len(output_text.encode("utf-8")) > self._max_output_bytes:
                raise self._failure(
                    request,
                    ProviderResponseSizeError,
                    ProviderErrorCode.RESPONSE_TOO_LARGE,
                    "Model provider response exceeds the configured limit",
                    retryable=False,
                    latency_ms=latency_ms,
                )
            failure_class = "json_object"
            output_json = canonical_json_object(
                json.loads(output_text), maximum_bytes=self._max_output_bytes
            )
            failure_class = "model_identity"
            model_id = _required_text(raw_response, "model")
            if model_id != self._model_id:
                raise ProviderModelError("Provider response model is invalid")
            failure_class = "usage"
            usage, cache_hit_tokens = deepseek_response_usage(raw_response)
            result = StructuredGenerationResult(
                request_id=request.request_id,
                output_json=output_json,
                metadata=ProviderCallMetadata(
                    provider=ModelProviderKind.DEEPSEEK,
                    model_id=model_id,
                    request_scope_sha256=request.request_scope_sha256,
                    latency_ms=latency_ms,
                    usage=usage,
                ),
            )
            return result, nominal_deepseek_cost(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_hit_tokens=cache_hit_tokens,
                model_id=self._model_id,
            )
        except ProviderModelError:
            pass
        except ModelProviderError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
        _log_contract_failure(request, latency_ms, failure_class, self._model_id)
        raise self._failure(
            request,
            ProviderMalformedResponseError,
            ProviderErrorCode.MALFORMED_RESPONSE,
            "Model provider response is invalid",
            retryable=False,
            latency_ms=latency_ms,
        )

    def _remote_failure(
        self,
        request: StructuredGenerationRequest,
        started: float,
        remote_error: Exception,
    ) -> ModelProviderError:
        status_code = getattr(remote_error, "status_code", None)
        name = type(remote_error).__name__.lower()
        if status_code == 429:
            error_type: type[ModelProviderError] = ProviderRateLimitError
            code = ProviderErrorCode.RATE_LIMITED
            retryable = True
            message = "Model provider rate limit was reached"
        elif isinstance(remote_error, (ConnectionError, OSError)) or "connection" in name:
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
            error_type,
            code,
            message,
            retryable=retryable,
            started=started,
        )

    def _failure(
        self,
        request: StructuredGenerationRequest,
        error_type: type[ModelProviderError],
        code: ProviderErrorCode,
        message: str,
        *,
        retryable: bool,
        started: float | None = None,
        latency_ms: int | None = None,
    ) -> ModelProviderError:
        if latency_ms is None:
            latency_ms = 0 if started is None else _latency_ms(started, self._clock())
        return error_type(
            message,
            code=code,
            retryable=retryable,
            metadata=ProviderFailureMetadata(
                provider="deepseek",
                model_id=self._model_id,
                request_scope_sha256=request.request_scope_sha256,
                latency_ms=latency_ms,
                error_code=code,
            ),
        )


def deepseek_response_usage(raw_response: object) -> tuple[ProviderUsage, int]:
    usage = _field(raw_response, "usage")
    input_tokens = _token_count(_field(usage, "prompt_tokens"))
    output_tokens = _token_count(_field(usage, "completion_tokens"))
    total_tokens = _token_count(_field(usage, "total_tokens"))
    cache_hit_tokens = _token_count(_field(usage, "prompt_cache_hit_tokens"))
    cache_miss_tokens = _token_count(_field(usage, "prompt_cache_miss_tokens"))
    if cache_hit_tokens + cache_miss_tokens != input_tokens:
        raise ProviderModelError("Provider usage is invalid")
    return (
        ProviderUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        ),
        cache_hit_tokens,
    )


def nominal_deepseek_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_hit_tokens: int,
    model_id: str = DEEPSEEK_CHAT_MODEL_ID,
) -> Decimal:
    try:
        cache_hit_price, cache_miss_price, output_price = _MODEL_PRICES[model_id]
    except KeyError as error:
        raise ValueError("DeepSeek model is unsupported") from error
    cache_miss_tokens = input_tokens - cache_hit_tokens
    return (
        Decimal(cache_hit_tokens) * cache_hit_price
        + Decimal(cache_miss_tokens) * cache_miss_price
        + Decimal(output_tokens) * output_price
    ) / _MILLION


def _log_failure(error: ModelProviderError) -> None:
    metadata = error.metadata
    if metadata is None:
        return
    _LOGGER.warning(
        "model_provider_failure provider=deepseek model_id=%s scope_sha256=%s "
        "latency_ms=%d error_code=%s retryable=%s",
        metadata.model_id,
        metadata.request_scope_sha256,
        metadata.latency_ms,
        metadata.error_code.value,
        error.retryable,
    )


def _log_contract_failure(
    request: StructuredGenerationRequest,
    latency_ms: int,
    failure_class: str,
    model_id: str,
) -> None:
    _LOGGER.warning(
        "model_provider_contract_failure provider=deepseek model_id=%s scope_sha256=%s "
        "latency_ms=%d failure_class=%s",
        model_id,
        request.request_scope_sha256,
        latency_ms,
        failure_class,
    )


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _required_text(value: object, name: str) -> str:
    field = _field(value, name)
    if not isinstance(field, str) or not field.strip():
        raise ProviderModelError("Provider response field is invalid")
    return field.strip()


def _token_count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProviderModelError("Provider usage is invalid")
    return value


def _latency_ms(started: float, finished: float) -> int:
    return max(0, int((finished - started) * 1000))
