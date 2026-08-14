"""Safe typed failures at the model-provider boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from lifecase.providers.models import (
    ANTHROPIC_MESSAGES_MODEL_ID,
    DEEPSEEK_CHAT_MODEL_IDS,
    GEMINI_GENERATE_MODEL_ID,
    GROQ_CHAT_MODEL_ID,
    MISTRAL_CHAT_MODEL_ID,
)


class ProviderErrorCode(StrEnum):
    """Stable failure codes that never contain remote response text."""

    CONFIGURATION = "provider_configuration"
    DISABLED = "provider_disabled"
    SCRIPT_MISSING = "provider_script_missing"
    REQUEST_TOO_LARGE = "provider_request_too_large"
    RESPONSE_TOO_LARGE = "provider_response_too_large"
    TIMEOUT = "provider_timeout"
    CANCELLED = "provider_cancelled"
    NETWORK = "provider_network"
    RATE_LIMITED = "provider_rate_limited"
    REFUSED = "provider_refused"
    REMOTE = "provider_remote"
    MALFORMED_RESPONSE = "provider_malformed_response"


@dataclass(frozen=True, slots=True)
class ProviderFailureMetadata:
    """Safe call metadata attached to a provider failure."""

    provider: str
    model_id: str | None
    request_scope_sha256: str
    latency_ms: int
    error_code: ProviderErrorCode

    def __post_init__(self) -> None:
        if self.provider not in {
            "deepseek",
            "disabled",
            "fake",
            "ollama",
            "openai",
            "groq",
            "mistral",
            "gemini",
            "anthropic",
        }:
            raise ValueError("Provider failure metadata is invalid")
        fixed_model_id = {
            "anthropic": ANTHROPIC_MESSAGES_MODEL_ID,
            "gemini": GEMINI_GENERATE_MODEL_ID,
            "groq": GROQ_CHAT_MODEL_ID,
            "mistral": MISTRAL_CHAT_MODEL_ID,
        }.get(self.provider)
        if self.provider == "deepseek":
            if self.model_id not in DEEPSEEK_CHAT_MODEL_IDS:
                raise ValueError("Provider failure metadata is invalid")
        elif fixed_model_id is not None:
            if self.model_id != fixed_model_id:
                raise ValueError("Provider failure metadata is invalid")
        elif self.model_id is not None and (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}", self.model_id) is None
        ):
            raise ValueError("Provider failure metadata is invalid")
        if re.fullmatch(r"[0-9a-f]{64}", self.request_scope_sha256) is None:
            raise ValueError("Provider failure metadata is invalid")
        if (
            not isinstance(self.latency_ms, int)
            or isinstance(self.latency_ms, bool)
            or self.latency_ms < 0
            or not isinstance(self.error_code, ProviderErrorCode)
        ):
            raise ValueError("Provider failure metadata is invalid")


class ModelProviderError(RuntimeError):
    """Base class for failures that cross into provider-neutral code."""

    code: ProviderErrorCode
    retryable: bool

    def __init__(
        self,
        message: str,
        *,
        code: ProviderErrorCode,
        retryable: bool,
        metadata: ProviderFailureMetadata | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.metadata = metadata


class ProviderConfigurationError(ModelProviderError):
    """Raised when a provider is disabled, missing, or misconfigured."""


class ProviderScriptError(ModelProviderError):
    """Raised when the fake provider has no explicit scripted result."""


class ProviderRequestSizeError(ModelProviderError):
    """Raised before a provider call when bounded input is too large."""


class ProviderResponseSizeError(ModelProviderError):
    """Raised when provider output exceeds the configured byte ceiling."""


class ProviderTimeoutError(ModelProviderError):
    """Raised when one bounded provider call times out."""


class ProviderCancelledError(ModelProviderError):
    """Raised when the caller cancels one provider call."""


class ProviderNetworkError(ModelProviderError):
    """Raised for a retryable network transport failure."""


class ProviderRateLimitError(ModelProviderError):
    """Raised for a retryable provider rate limit."""


class ProviderRefusalError(ModelProviderError):
    """Raised when the provider returns a structured refusal."""


class ProviderRemoteError(ModelProviderError):
    """Raised for a safe, classified remote provider failure."""


class ProviderMalformedResponseError(ModelProviderError):
    """Raised when required provider response fields are absent or invalid."""
