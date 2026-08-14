"""OpenAI Responses API adapter behind an injected, credentialed client."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

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
    ModelProviderKind,
    ProviderCallMetadata,
    ProviderModelError,
    ProviderUsage,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    canonical_json_object,
)

_LOGGER = logging.getLogger(__name__)


class OpenAIResponsesResource(Protocol):
    """Narrow async shape implemented by ``AsyncOpenAI.responses``."""

    def create(self, **kwargs: Any) -> Awaitable[Any]: ...


class OpenAIResponsesClient(Protocol):
    """Narrow client boundary; SDK objects remain inside this module."""

    responses: OpenAIResponsesResource


class OpenAIResponsesProvider:
    """Map the current Responses API contract into provider-neutral values."""

    def __init__(
        self,
        config: ModelProviderConfig,
        client: OpenAIResponsesClient,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if config.provider is not ModelProviderKind.OPENAI or config.model_id is None:
            raise ValueError("OpenAI provider requires OpenAI configuration")
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
            request_error = ProviderRequestSizeError(
                "Model provider request exceeds the configured limit",
                code=ProviderErrorCode.REQUEST_TOO_LARGE,
                retryable=False,
                metadata=self._failure_metadata(
                    request,
                    latency_ms=0,
                    code=ProviderErrorCode.REQUEST_TOO_LARGE,
                ),
            )
            _log_failure(request_error)
            raise request_error

        payload = self._payload(request)
        started = self._clock()
        call_error: ModelProviderError | None = None
        raw_response: object | None = None
        try:
            raw_response = await asyncio.wait_for(
                self._client.responses.create(**payload),
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
            malformed_error = self._failure(
                request,
                started,
                ProviderMalformedResponseError,
                ProviderErrorCode.MALFORMED_RESPONSE,
                retryable=False,
                message="Model provider response is invalid",
            )
            _log_failure(malformed_error)
            raise malformed_error

        latency_ms = _latency_ms(started, self._clock())
        mapping_error: ModelProviderError | None = None
        result: StructuredGenerationResult | None = None
        try:
            result = self._map_response(request, raw_response, latency_ms)
        except ModelProviderError as error:
            mapping_error = error
        if mapping_error is not None:
            _log_failure(mapping_error)
            raise mapping_error
        if result is None:
            raise AssertionError("Provider response mapping returned no result")
        _log_success(result.metadata)
        return result

    def _payload(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        return {
            "model": self._config.model_id,
            "instructions": request.system_instruction,
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {"type": "input_text", "text": request.stage_instruction},
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": request.input_json}],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.output_schema_name,
                    "strict": True,
                    "schema": request.output_schema(),
                }
            },
            "max_output_tokens": request.max_output_tokens,
            "store": False,
        }

    def _map_response(
        self,
        request: StructuredGenerationRequest,
        raw_response: object,
        latency_ms: int,
    ) -> StructuredGenerationResult:
        status = _field(raw_response, "status")
        if status is None:
            raise self._failure(
                request,
                None,
                ProviderMalformedResponseError,
                ProviderErrorCode.MALFORMED_RESPONSE,
                retryable=False,
                message="Model provider response is invalid",
                latency_ms=latency_ms,
            )
        if status != "completed":
            raise self._failure(
                request,
                None,
                ProviderRemoteError,
                ProviderErrorCode.REMOTE,
                retryable=False,
                message="Model provider did not complete the response",
                latency_ms=latency_ms,
            )
        output_text = _response_output_text(raw_response, request, self, latency_ms)
        output_bytes = len(output_text.encode("utf-8"))
        if output_bytes > self._config.max_output_bytes:
            raise self._failure(
                request,
                None,
                ProviderResponseSizeError,
                ProviderErrorCode.RESPONSE_TOO_LARGE,
                retryable=False,
                message="Model provider response exceeds the configured limit",
                latency_ms=latency_ms,
            )
        mapped_result: StructuredGenerationResult | None = None
        try:
            decoded = json.loads(output_text)
            output_json = canonical_json_object(
                decoded,
                maximum_bytes=self._config.max_output_bytes,
            )
            usage = _response_usage(raw_response)
            model_id = _required_text(raw_response, "model")
            metadata = ProviderCallMetadata(
                provider=ModelProviderKind.OPENAI,
                model_id=model_id,
                request_scope_sha256=request.request_scope_sha256,
                latency_ms=latency_ms,
                usage=usage,
            )
            mapped_result = StructuredGenerationResult(
                request_id=request.request_id,
                output_json=output_json,
                metadata=metadata,
            )
        except (ProviderModelError, TypeError, ValueError, json.JSONDecodeError):
            pass
        if mapped_result is None:
            raise self._failure(
                request,
                None,
                ProviderMalformedResponseError,
                ProviderErrorCode.MALFORMED_RESPONSE,
                retryable=False,
                message="Model provider response is invalid",
                latency_ms=latency_ms,
            )
        return mapped_result

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
            provider=ModelProviderKind.OPENAI,
            model_id=self._config.model_id,
            request_scope_sha256=request.request_scope_sha256,
            latency_ms=latency_ms,
            error_code=code,
        )


def _response_output_text(
    raw_response: object,
    request: StructuredGenerationRequest,
    provider: OpenAIResponsesProvider,
    latency_ms: int,
) -> str:
    output = _field(raw_response, "output")
    if not isinstance(output, (list, tuple)):
        raise provider._failure(
            request,
            None,
            ProviderMalformedResponseError,
            ProviderErrorCode.MALFORMED_RESPONSE,
            retryable=False,
            message="Model provider response is invalid",
            latency_ms=latency_ms,
        )
    texts: list[str] = []
    for item in output:
        if _field(item, "type") != "message":
            continue
        content = _field(item, "content")
        if not isinstance(content, (list, tuple)):
            continue
        for part in content:
            part_type = _field(part, "type")
            if part_type == "refusal":
                raise provider._failure(
                    request,
                    None,
                    ProviderRefusalError,
                    ProviderErrorCode.REFUSED,
                    retryable=False,
                    message="Model provider refused the request",
                    latency_ms=latency_ms,
                )
            if part_type == "output_text":
                text = _field(part, "text")
                if isinstance(text, str) and text:
                    texts.append(text)
    if len(texts) != 1:
        raise provider._failure(
            request,
            None,
            ProviderMalformedResponseError,
            ProviderErrorCode.MALFORMED_RESPONSE,
            retryable=False,
            message="Model provider response is invalid",
            latency_ms=latency_ms,
        )
    return texts[0]


def _response_usage(raw_response: object) -> ProviderUsage:
    usage = _field(raw_response, "usage")
    input_tokens = _token_count(_field(usage, "input_tokens"))
    output_tokens = _token_count(_field(usage, "output_tokens"))
    total_tokens = _token_count(_field(usage, "total_tokens"))
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
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
    elapsed = finished - started
    if elapsed < 0:
        return 0
    return int(elapsed * 1000)


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
