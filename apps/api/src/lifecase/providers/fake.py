"""Deterministic explicit-script provider for tests and local evaluation."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from lifecase.providers.config import ModelProviderConfig
from lifecase.providers.errors import (
    ProviderErrorCode,
    ProviderFailureMetadata,
    ProviderRequestSizeError,
    ProviderResponseSizeError,
    ProviderScriptError,
)
from lifecase.providers.models import (
    ModelProviderKind,
    ProviderCallMetadata,
    ProviderModelError,
    ProviderUsage,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)

_LOGGER = logging.getLogger(__name__)
_FAKE_MODEL_ID = "lifecase-fake-v1"


@dataclass(frozen=True, slots=True)
class FakeProviderScript:
    """One explicit synthetic result selected by request scope hash."""

    request_scope_sha256: str
    output_json: str
    usage: ProviderUsage

    def __post_init__(self) -> None:
        validated = StructuredGenerationResult(
            request_id="fixture-validation",
            output_json=self.output_json,
            metadata=ProviderCallMetadata(
                provider=ModelProviderKind.FAKE,
                model_id=_FAKE_MODEL_ID,
                request_scope_sha256=self.request_scope_sha256,
                latency_ms=0,
                usage=self.usage,
            ),
        )
        object.__setattr__(self, "output_json", validated.output_json)


class FakeModelProvider:
    """Return only explicitly supplied, scope-keyed synthetic responses."""

    def __init__(
        self,
        config: ModelProviderConfig,
        scripts: tuple[FakeProviderScript, ...],
    ) -> None:
        if config.provider is not ModelProviderKind.FAKE:
            raise ValueError("Fake provider requires fake configuration")
        if not isinstance(scripts, tuple) or not all(
            isinstance(script, FakeProviderScript) for script in scripts
        ):
            raise ValueError("Fake provider scripts are invalid")
        indexed = {script.request_scope_sha256: script for script in scripts}
        if len(indexed) != len(scripts):
            raise ValueError("Fake provider scripts must have unique scopes")
        self._config = config
        self._scripts = indexed

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        if request.input_bytes > self._config.max_input_bytes:
            request_error = ProviderRequestSizeError(
                "Model provider request exceeds the configured limit",
                code=ProviderErrorCode.REQUEST_TOO_LARGE,
                retryable=False,
                metadata=_failure_metadata(request, ProviderErrorCode.REQUEST_TOO_LARGE),
            )
            _log_failure(request_error)
            raise request_error
        script = self._scripts.get(request.request_scope_sha256)
        if script is None:
            script_error = ProviderScriptError(
                "No deterministic provider script matches the request scope",
                code=ProviderErrorCode.SCRIPT_MISSING,
                retryable=False,
                metadata=_failure_metadata(request, ProviderErrorCode.SCRIPT_MISSING),
            )
            _log_failure(script_error)
            raise script_error
        if len(script.output_json.encode("utf-8")) > self._config.max_output_bytes:
            response_error = ProviderResponseSizeError(
                "Model provider response exceeds the configured limit",
                code=ProviderErrorCode.RESPONSE_TOO_LARGE,
                retryable=False,
                metadata=_failure_metadata(request, ProviderErrorCode.RESPONSE_TOO_LARGE),
            )
            _log_failure(response_error)
            raise response_error
        metadata = ProviderCallMetadata(
            provider=ModelProviderKind.FAKE,
            model_id=_FAKE_MODEL_ID,
            request_scope_sha256=request.request_scope_sha256,
            latency_ms=0,
            usage=script.usage,
        )
        result = StructuredGenerationResult(
            request_id=request.request_id,
            output_json=script.output_json,
            metadata=metadata,
        )
        _log_success(metadata)
        return result


def _failure_metadata(
    request: StructuredGenerationRequest,
    code: ProviderErrorCode,
) -> ProviderFailureMetadata:
    return ProviderFailureMetadata(
        provider=ModelProviderKind.FAKE,
        model_id=_FAKE_MODEL_ID,
        request_scope_sha256=request.request_scope_sha256,
        latency_ms=0,
        error_code=code,
    )


def _log_success(metadata: ProviderCallMetadata) -> None:
    try:
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
    except (AttributeError, ProviderModelError):
        return


def _log_failure(
    error: ProviderRequestSizeError | ProviderResponseSizeError | ProviderScriptError,
) -> None:
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
