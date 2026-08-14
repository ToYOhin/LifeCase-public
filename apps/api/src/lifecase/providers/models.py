"""Immutable provider-neutral structured-generation contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

PROVIDER_REQUEST_SCHEMA_VERSION = "provider_request.v1"
PROVIDER_RESULT_SCHEMA_VERSION = "provider_result.v1"
GROQ_CHAT_MODEL_ID = "openai/gpt-oss-20b"
MISTRAL_CHAT_MODEL_ID = "mistral-small-2603"
DEEPSEEK_CHAT_MODEL_ID = "deepseek-v4-flash"
DEEPSEEK_CHAT_PRO_MODEL_ID = "deepseek-v4-pro"
DEEPSEEK_CHAT_MODEL_IDS = frozenset({DEEPSEEK_CHAT_MODEL_ID, DEEPSEEK_CHAT_PRO_MODEL_ID})
GEMINI_GENERATE_MODEL_ID = "gemini-2.5-flash"
ANTHROPIC_MESSAGES_MODEL_ID = "claude-sonnet-4-5-20250929"

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}\Z")
_SCHEMA_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_MAX_INSTRUCTION_CHARS = 20_000
_MAX_INPUT_JSON_BYTES = 1_000_000
_MAX_SCHEMA_JSON_BYTES = 100_000
_MAX_OUTPUT_JSON_BYTES = 256_000
_MAX_OUTPUT_TOKENS = 100_000


class ProviderModelError(ValueError):
    """Raised when a provider-neutral value violates its stable boundary."""


class ModelProviderKind(StrEnum):
    """Explicit model-provider choices supported by local configuration."""

    DISABLED = "disabled"
    FAKE = "fake"
    OLLAMA = "ollama"
    OPENAI = "openai"
    GROQ = "groq"
    MISTRAL = "mistral"
    DEEPSEEK = "deepseek"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Provider-neutral token and optional cost accounting."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_minor_units: int | None = None
    cost_currency: str | None = None

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens, self.total_tokens):
            if not _is_nonnegative_int(value):
                raise ProviderModelError("Provider usage is invalid")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ProviderModelError("Provider usage is invalid")
        if (self.cost_minor_units is None) != (self.cost_currency is None):
            raise ProviderModelError("Provider usage is invalid")
        if self.cost_minor_units is not None and not _is_nonnegative_int(self.cost_minor_units):
            raise ProviderModelError("Provider usage is invalid")
        if self.cost_currency is not None:
            currency = self.cost_currency.strip().upper()
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise ProviderModelError("Provider usage is invalid")
            object.__setattr__(self, "cost_currency", currency)


@dataclass(frozen=True, slots=True)
class ProviderCallMetadata:
    """Safe metadata suitable for normal operational logs."""

    provider: ModelProviderKind
    model_id: str
    request_scope_sha256: str
    latency_ms: int
    usage: ProviderUsage

    def __post_init__(self) -> None:
        if not isinstance(self.provider, ModelProviderKind):
            raise ProviderModelError("Provider metadata is invalid")
        allowed_model_ids = {
            ModelProviderKind.ANTHROPIC: ANTHROPIC_MESSAGES_MODEL_ID,
            ModelProviderKind.GEMINI: GEMINI_GENERATE_MODEL_ID,
            ModelProviderKind.GROQ: GROQ_CHAT_MODEL_ID,
            ModelProviderKind.MISTRAL: MISTRAL_CHAT_MODEL_ID,
        }.get(self.provider)
        if self.provider is ModelProviderKind.DEEPSEEK:
            if self.model_id not in DEEPSEEK_CHAT_MODEL_IDS:
                raise ProviderModelError("Provider metadata is invalid")
        elif allowed_model_ids is not None:
            if self.model_id != allowed_model_ids:
                raise ProviderModelError("Provider metadata is invalid")
        else:
            object.__setattr__(self, "model_id", _identifier(self.model_id))
        if not _is_sha256(self.request_scope_sha256):
            raise ProviderModelError("Provider metadata is invalid")
        if not _is_nonnegative_int(self.latency_ms):
            raise ProviderModelError("Provider metadata is invalid")
        if not isinstance(self.usage, ProviderUsage):
            raise ProviderModelError("Provider metadata is invalid")


@dataclass(frozen=True, slots=True)
class StructuredGenerationRequest:
    """Versioned bounded input for one structured provider call."""

    request_id: str
    request_scope_sha256: str
    system_instruction: str
    stage_instruction: str
    input_json: str
    output_schema_name: str
    output_schema_json: str
    max_output_tokens: int
    schema_version: str = PROVIDER_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROVIDER_REQUEST_SCHEMA_VERSION:
            raise ProviderModelError("Provider request version is unsupported")
        object.__setattr__(self, "request_id", _identifier(self.request_id))
        if not _is_sha256(self.request_scope_sha256):
            raise ProviderModelError("Provider request is invalid")
        object.__setattr__(
            self,
            "system_instruction",
            _bounded_text(self.system_instruction, maximum=_MAX_INSTRUCTION_CHARS),
        )
        object.__setattr__(
            self,
            "stage_instruction",
            _bounded_text(self.stage_instruction, maximum=_MAX_INSTRUCTION_CHARS),
        )
        input_json = _canonical_json_object(
            self.input_json,
            maximum_bytes=_MAX_INPUT_JSON_BYTES,
            label="Provider request",
        )
        schema_json = _canonical_json_object(
            self.output_schema_json,
            maximum_bytes=_MAX_SCHEMA_JSON_BYTES,
            label="Provider schema",
        )
        object.__setattr__(self, "input_json", input_json)
        object.__setattr__(self, "output_schema_json", schema_json)
        if not isinstance(self.output_schema_name, str) or (
            _SCHEMA_NAME_PATTERN.fullmatch(self.output_schema_name) is None
        ):
            raise ProviderModelError("Provider request is invalid")
        if (
            not _is_nonnegative_int(self.max_output_tokens)
            or not 1 <= self.max_output_tokens <= _MAX_OUTPUT_TOKENS
        ):
            raise ProviderModelError("Provider request is invalid")

    @property
    def input_bytes(self) -> int:
        """Return exactly the UTF-8 bytes sent as prompt material."""

        values = (
            self.system_instruction,
            self.stage_instruction,
            self.input_json,
            self.output_schema_json,
        )
        return sum(len(value.encode("utf-8")) for value in values)

    def output_schema(self) -> dict[str, Any]:
        """Return a fresh JSON schema object for an adapter call."""

        value = json.loads(self.output_schema_json)
        if not isinstance(value, dict):
            raise ProviderModelError("Provider schema is invalid")
        return value


@dataclass(frozen=True, slots=True)
class StructuredGenerationResult:
    """Provider-neutral candidate JSON plus safe call metadata."""

    request_id: str
    output_json: str
    metadata: ProviderCallMetadata
    schema_version: str = PROVIDER_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROVIDER_RESULT_SCHEMA_VERSION:
            raise ProviderModelError("Provider result version is unsupported")
        object.__setattr__(self, "request_id", _identifier(self.request_id))
        object.__setattr__(
            self,
            "output_json",
            _canonical_json_object(
                self.output_json,
                maximum_bytes=_MAX_OUTPUT_JSON_BYTES,
                label="Provider result",
            ),
        )
        if not isinstance(self.metadata, ProviderCallMetadata):
            raise ProviderModelError("Provider result is invalid")


def canonical_json_object(value: object, *, maximum_bytes: int) -> str:
    """Create stable JSON for provider fixtures and adapter output."""

    if not isinstance(value, dict):
        raise ProviderModelError("Provider JSON value must be an object")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise ProviderModelError("Provider JSON value is too large")
    return encoded


def request_scope_sha256(*parts: str) -> str:
    """Build a stable opaque scope identifier without retaining input text."""

    digest = hashlib.sha256()
    for part in parts:
        if not isinstance(part, str):
            raise ProviderModelError("Provider scope input is invalid")
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _canonical_json_object(value: object, *, maximum_bytes: int, label: str) -> str:
    if not isinstance(value, str):
        raise ProviderModelError(f"{label} JSON is invalid")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ProviderModelError(f"{label} JSON is too large")
    parsed: object = None
    parse_failed = False
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, UnicodeError):
        parse_failed = True
    if parse_failed:
        raise ProviderModelError(f"{label} JSON is invalid")
    if not isinstance(parsed, dict):
        raise ProviderModelError(f"{label} JSON is invalid")
    return canonical_json_object(parsed, maximum_bytes=maximum_bytes)


def _identifier(value: object) -> str:
    if not isinstance(value, str):
        raise ProviderModelError("Provider identifier is invalid")
    clean = value.strip()
    if _IDENTIFIER_PATTERN.fullmatch(clean) is None:
        raise ProviderModelError("Provider identifier is invalid")
    return clean


def _bounded_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ProviderModelError("Provider text is invalid")
    clean = value.strip()
    if not 1 <= len(clean) <= maximum:
        raise ProviderModelError("Provider text is invalid")
    return clean


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_finite_number(value: object) -> bool:
    """Return whether a non-boolean value is finite numeric input."""

    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
