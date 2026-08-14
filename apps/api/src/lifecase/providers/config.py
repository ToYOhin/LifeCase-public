"""Explicit, secret-free model-provider runtime configuration."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from lifecase.providers.errors import (
    ProviderConfigurationError,
    ProviderErrorCode,
)
from lifecase.providers.models import (
    ANTHROPIC_MESSAGES_MODEL_ID,
    DEEPSEEK_CHAT_MODEL_IDS,
    GEMINI_GENERATE_MODEL_ID,
    GROQ_CHAT_MODEL_ID,
    MISTRAL_CHAT_MODEL_ID,
    ModelProviderKind,
    is_finite_number,
)

_MIN_TIMEOUT_SECONDS = 1.0
_MAX_TIMEOUT_SECONDS = 120.0
_MIN_INPUT_BYTES = 1_024
_MAX_INPUT_BYTES = 1_000_000
_MIN_OUTPUT_BYTES = 2
_MAX_OUTPUT_BYTES = 256_000
_MODEL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}\Z")


@dataclass(frozen=True, slots=True)
class ModelProviderConfig:
    """Bounded provider selection; credentials are deliberately excluded."""

    provider: ModelProviderKind = ModelProviderKind.DISABLED
    model_id: str | None = None
    timeout_seconds: float = 30.0
    max_input_bytes: int = 100_000
    max_output_bytes: int = 64_000

    def __post_init__(self) -> None:
        if not isinstance(self.provider, ModelProviderKind):
            raise _configuration_error()
        model_id = self.model_id.strip() if isinstance(self.model_id, str) else self.model_id
        fixed_model_id = {
            ModelProviderKind.ANTHROPIC: ANTHROPIC_MESSAGES_MODEL_ID,
            ModelProviderKind.GEMINI: GEMINI_GENERATE_MODEL_ID,
            ModelProviderKind.GROQ: GROQ_CHAT_MODEL_ID,
            ModelProviderKind.MISTRAL: MISTRAL_CHAT_MODEL_ID,
        }.get(self.provider)
        if self.provider is ModelProviderKind.DEEPSEEK:
            if model_id not in DEEPSEEK_CHAT_MODEL_IDS:
                raise _configuration_error()
        elif fixed_model_id is not None:
            if model_id != fixed_model_id:
                raise _configuration_error()
        elif self.provider in {ModelProviderKind.OLLAMA, ModelProviderKind.OPENAI}:
            if not isinstance(model_id, str) or _MODEL_ID_PATTERN.fullmatch(model_id) is None:
                raise _configuration_error()
        elif model_id is not None:
            raise _configuration_error()
        if (
            not is_finite_number(self.timeout_seconds)
            or not _MIN_TIMEOUT_SECONDS <= float(self.timeout_seconds) <= _MAX_TIMEOUT_SECONDS
        ):
            raise _configuration_error()
        if not _bounded_int(self.max_input_bytes, _MIN_INPUT_BYTES, _MAX_INPUT_BYTES):
            raise _configuration_error()
        if not _bounded_int(self.max_output_bytes, _MIN_OUTPUT_BYTES, _MAX_OUTPUT_BYTES):
            raise _configuration_error()
        if self.max_output_bytes > self.max_input_bytes:
            raise _configuration_error()
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> ModelProviderConfig:
        """Parse only non-secret provider settings from an explicit mapping."""

        parsed: tuple[ModelProviderKind, str | None, float, int, int] | None = None
        try:
            provider = ModelProviderKind(
                environment.get("LIFECASE_MODEL_PROVIDER", ModelProviderKind.DISABLED).strip()
            )
            model_id = environment.get("LIFECASE_MODEL_ID") or None
            timeout = _float_setting(environment, "LIFECASE_MODEL_TIMEOUT_SECONDS", 30.0)
            max_input = _int_setting(environment, "LIFECASE_MODEL_MAX_INPUT_BYTES", 100_000)
            max_output = _int_setting(environment, "LIFECASE_MODEL_MAX_OUTPUT_BYTES", 64_000)
            parsed = (provider, model_id, timeout, max_input, max_output)
        except (TypeError, ValueError):
            pass
        if parsed is None:
            raise _configuration_error()
        provider, model_id, timeout, max_input, max_output = parsed
        return cls(
            provider=provider,
            model_id=model_id,
            timeout_seconds=timeout,
            max_input_bytes=max_input,
            max_output_bytes=max_output,
        )


def _float_setting(environment: Mapping[str, str], name: str, default: float) -> float:
    raw = environment.get(name)
    return default if raw is None else float(raw)


def _int_setting(environment: Mapping[str, str], name: str, default: int) -> int:
    raw = environment.get(name)
    return default if raw is None else int(raw)


def _bounded_int(value: object, minimum: int, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _configuration_error() -> ProviderConfigurationError:
    return ProviderConfigurationError(
        "Model provider configuration is invalid",
        code=ProviderErrorCode.CONFIGURATION,
        retryable=False,
    )
