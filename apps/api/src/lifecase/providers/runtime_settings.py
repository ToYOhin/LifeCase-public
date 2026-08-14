"""Persistent local AI settings without placing credentials in project data."""

from __future__ import annotations

import ctypes
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import RLock
from typing import Any, Literal, Protocol, cast

from lifecase.config import ApplicationConfig
from lifecase.providers.config import ModelProviderConfig
from lifecase.providers.models import (
    ANTHROPIC_MESSAGES_MODEL_ID,
    DEEPSEEK_CHAT_MODEL_IDS,
    GEMINI_GENERATE_MODEL_ID,
    GROQ_CHAT_MODEL_ID,
    MISTRAL_CHAT_MODEL_ID,
    ModelProviderKind,
)
from lifecase.providers.registry import ProviderRuntimeClients

_CREDENTIAL_TARGET = "LifeCase/AIProvider/v1"
_CREDENTIAL_TYPE_GENERIC = 1
_CREDENTIAL_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168
_MAX_CREDENTIAL_BYTES = 2_000

SUPPORTED_SETTINGS_MODELS: dict[ModelProviderKind, frozenset[str]] = {
    ModelProviderKind.OPENAI: frozenset({"gpt-5.6-terra"}),
    ModelProviderKind.DEEPSEEK: DEEPSEEK_CHAT_MODEL_IDS,
    ModelProviderKind.GROQ: frozenset({GROQ_CHAT_MODEL_ID}),
    ModelProviderKind.MISTRAL: frozenset({MISTRAL_CHAT_MODEL_ID}),
    ModelProviderKind.GEMINI: frozenset({GEMINI_GENERATE_MODEL_ID}),
    ModelProviderKind.ANTHROPIC: frozenset({ANTHROPIC_MESSAGES_MODEL_ID}),
}


class CredentialStoreError(RuntimeError):
    """Raised when the operating-system credential store cannot complete an operation."""


@dataclass(frozen=True, slots=True)
class StoredAiSettings:
    provider: ModelProviderKind
    model_id: str
    api_key: str
    data_use_confirmed: bool

    def __post_init__(self) -> None:
        if self.model_id not in SUPPORTED_SETTINGS_MODELS.get(self.provider, frozenset()):
            raise ValueError("AI settings model is unsupported")
        if (
            not isinstance(self.api_key, str)
            or not 1 <= len(self.api_key) <= 1_500
            or self.api_key != self.api_key.strip()
            or any(character.isspace() for character in self.api_key)
        ):
            raise ValueError("AI credential is invalid")
        if self.data_use_confirmed is not True:
            raise ValueError("External processing confirmation is required")


class CredentialStore(Protocol):
    persistence: Literal["windows_credential_manager", "unavailable"]

    def load(self) -> StoredAiSettings | None: ...

    def save(self, settings: StoredAiSettings) -> None: ...

    def clear(self) -> None: ...


class MemoryCredentialStore:
    """Deterministic test double that models one persistent OS credential slot."""

    persistence: Literal["windows_credential_manager", "unavailable"] = "windows_credential_manager"

    def __init__(self) -> None:
        self._settings: StoredAiSettings | None = None

    def load(self) -> StoredAiSettings | None:
        return self._settings

    def save(self, settings: StoredAiSettings) -> None:
        self._settings = settings

    def clear(self) -> None:
        self._settings = None


class UnavailableCredentialStore:
    """Fail-closed store for explicit test configs and unsupported operating systems."""

    persistence: Literal["windows_credential_manager", "unavailable"] = "unavailable"

    def load(self) -> StoredAiSettings | None:
        return None

    def save(self, _settings: StoredAiSettings) -> None:
        raise CredentialStoreError("Persistent credential storage is unavailable")

    def clear(self) -> None:
        return


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _Credential(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("target_name", ctypes.c_wchar_p),
        ("comment", ctypes.c_wchar_p),
        ("last_written", _FileTime),
        ("credential_blob_size", ctypes.c_uint32),
        ("credential_blob", ctypes.POINTER(ctypes.c_ubyte)),
        ("persist", ctypes.c_uint32),
        ("attribute_count", ctypes.c_uint32),
        ("attributes", ctypes.c_void_p),
        ("target_alias", ctypes.c_wchar_p),
        ("user_name", ctypes.c_wchar_p),
    ]


class WindowsCredentialStore:
    """Current-user Generic Credential backed by Windows Credential Manager."""

    persistence: Literal["windows_credential_manager", "unavailable"] = "windows_credential_manager"

    def __init__(self) -> None:
        if os.name != "nt":
            raise CredentialStoreError("Windows Credential Manager is unavailable")
        win_dll = cast(Callable[..., Any], ctypes.__dict__["WinDLL"])
        self._api = win_dll("advapi32", use_last_error=True)
        self._api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), ctypes.c_uint32]
        self._api.CredWriteW.restype = ctypes.c_int
        self._api.CredReadW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.POINTER(_Credential)),
        ]
        self._api.CredReadW.restype = ctypes.c_int
        self._api.CredFree.argtypes = [ctypes.c_void_p]
        self._api.CredFree.restype = None
        self._api.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        self._api.CredDeleteW.restype = ctypes.c_int

    def load(self) -> StoredAiSettings | None:
        pointer = ctypes.POINTER(_Credential)()
        if not self._api.CredReadW(
            _CREDENTIAL_TARGET,
            _CREDENTIAL_TYPE_GENERIC,
            0,
            ctypes.byref(pointer),
        ):
            if _windows_last_error() == _ERROR_NOT_FOUND:
                return None
            raise CredentialStoreError("Could not read the saved AI credential")
        try:
            credential = pointer.contents
            raw = ctypes.string_at(credential.credential_blob, credential.credential_blob_size)
            return _decode_settings(raw)
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise CredentialStoreError("The saved AI credential is invalid") from error
        finally:
            self._api.CredFree(pointer)

    def save(self, settings: StoredAiSettings) -> None:
        encoded = _encode_settings(settings)
        if len(encoded) > _MAX_CREDENTIAL_BYTES:
            raise CredentialStoreError("The AI credential is too large")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = _Credential(
            flags=0,
            type=_CREDENTIAL_TYPE_GENERIC,
            target_name=_CREDENTIAL_TARGET,
            comment="LifeCase local AI Provider settings",
            credential_blob_size=len(encoded),
            credential_blob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
            persist=_CREDENTIAL_PERSIST_LOCAL_MACHINE,
            attribute_count=0,
            attributes=None,
            target_alias=None,
            user_name="LifeCase",
        )
        if not self._api.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialStoreError("Could not save the AI credential")

    def clear(self) -> None:
        if (
            not self._api.CredDeleteW(_CREDENTIAL_TARGET, _CREDENTIAL_TYPE_GENERIC, 0)
            and _windows_last_error() != _ERROR_NOT_FOUND
        ):
            raise CredentialStoreError("Could not clear the AI credential")


def _windows_last_error() -> int:
    get_last_error = cast(Callable[[], int], ctypes.__dict__["get_last_error"])
    return get_last_error()


RuntimeClientFactory = Callable[[ModelProviderKind, str], ProviderRuntimeClients]


@dataclass(frozen=True, slots=True)
class AiSettingsSnapshot:
    persistence: Literal["windows_credential_manager", "unavailable"]
    configured: bool
    provider: ModelProviderKind | None
    model_id: str | None
    runtime_status: Literal["disabled", "ready"]


class AiSettingsManager:
    """Atomically save, restore, and expose the effective local Provider runtime."""

    def __init__(
        self,
        base_config: ApplicationConfig,
        base_clients: ProviderRuntimeClients,
        store: CredentialStore,
        client_factory: RuntimeClientFactory,
    ) -> None:
        self._base_config = base_config
        self._base_clients = base_clients
        self._store = store
        self._client_factory = client_factory
        self._lock = RLock()
        self._settings: StoredAiSettings | None = None
        self._clients: ProviderRuntimeClients | None = None
        try:
            self._restore()
        except (CredentialStoreError, ValueError):
            # A stale OS credential must not prevent the local Case desk from starting.
            self._settings = None
            self._clients = None

    def snapshot(self) -> AiSettingsSnapshot:
        with self._lock:
            settings = self._settings
            return AiSettingsSnapshot(
                persistence=self._store.persistence,
                configured=settings is not None,
                provider=None if settings is None else settings.provider,
                model_id=None if settings is None else settings.model_id,
                runtime_status="disabled" if settings is None else "ready",
            )

    def save(
        self,
        *,
        provider: ModelProviderKind,
        model_id: str,
        api_key: str | None,
        data_use_confirmed: bool,
    ) -> AiSettingsSnapshot:
        with self._lock:
            if api_key is None:
                if self._settings is None or self._settings.provider is not provider:
                    raise ValueError("AI credential is required")
                api_key = self._settings.api_key
            settings = StoredAiSettings(provider, model_id, api_key, data_use_confirmed)
            clients = self._client_factory(provider, api_key)
            self._store.save(settings)
            self._settings = settings
            self._clients = clients
        return self.snapshot()

    def clear(self) -> AiSettingsSnapshot:
        with self._lock:
            self._store.clear()
            self._settings = None
            self._clients = None
        return self.snapshot()

    def effective_config(self) -> ApplicationConfig:
        with self._lock:
            settings = self._settings
            if settings is None:
                return self._base_config
            return replace(
                self._base_config,
                model_provider=ModelProviderConfig(
                    provider=settings.provider,
                    model_id=settings.model_id,
                ),
                live_agent_enabled=True,
                groq_zdr_confirmed=settings.provider is ModelProviderKind.GROQ,
                mistral_zdr_confirmed=settings.provider is ModelProviderKind.MISTRAL,
                deepseek_data_use_confirmed=settings.provider is ModelProviderKind.DEEPSEEK,
                gemini_data_use_confirmed=settings.provider is ModelProviderKind.GEMINI,
                anthropic_data_use_confirmed=settings.provider is ModelProviderKind.ANTHROPIC,
            )

    def effective_clients(self) -> ProviderRuntimeClients:
        with self._lock:
            return self._base_clients if self._clients is None else self._clients

    def _restore(self) -> None:
        settings = self._store.load()
        if settings is None:
            return
        clients = self._client_factory(settings.provider, settings.api_key)
        self._settings = settings
        self._clients = clients


def default_credential_store() -> CredentialStore:
    if os.name != "nt":
        return UnavailableCredentialStore()
    try:
        return WindowsCredentialStore()
    except CredentialStoreError:
        return UnavailableCredentialStore()


def default_runtime_client_factory(
    provider: ModelProviderKind,
    api_key: str,
) -> ProviderRuntimeClients:
    from lifecase.providers.http_clients import (
        FixedJsonHttpResource,
        OpenAICompatibleChatHttpClient,
        OpenAIHttpClient,
    )

    if provider is ModelProviderKind.OPENAI:
        from lifecase.providers.openai_responses import OpenAIResponsesClient

        return ProviderRuntimeClients(openai=cast(OpenAIResponsesClient, OpenAIHttpClient(api_key)))
    if provider is ModelProviderKind.DEEPSEEK:
        from lifecase.providers.deepseek_chat import DeepSeekHttpClient

        return ProviderRuntimeClients(deepseek=DeepSeekHttpClient(api_key))
    if provider is ModelProviderKind.GROQ:
        from lifecase.providers.groq_chat import GROQ_CHAT_ENDPOINT, GroqChatClient

        return ProviderRuntimeClients(
            groq=cast(
                GroqChatClient,
                OpenAICompatibleChatHttpClient(GROQ_CHAT_ENDPOINT, api_key),
            )
        )
    if provider is ModelProviderKind.MISTRAL:
        from lifecase.providers.mistral_chat import MISTRAL_CHAT_ENDPOINT, MistralChatClient

        return ProviderRuntimeClients(
            mistral=cast(
                MistralChatClient,
                OpenAICompatibleChatHttpClient(MISTRAL_CHAT_ENDPOINT, api_key),
            )
        )
    if provider is ModelProviderKind.GEMINI:
        from lifecase.providers.google_anthropic import GEMINI_GENERATE_ENDPOINT

        return ProviderRuntimeClients(
            gemini=FixedJsonHttpResource(
                GEMINI_GENERATE_ENDPOINT,
                {"x-goog-api-key": api_key},
            )
        )
    if provider is ModelProviderKind.ANTHROPIC:
        from lifecase.providers.google_anthropic import ANTHROPIC_MESSAGES_ENDPOINT

        return ProviderRuntimeClients(
            anthropic=FixedJsonHttpResource(
                ANTHROPIC_MESSAGES_ENDPOINT,
                {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
        )
    raise ValueError("AI Provider client is not implemented")


def _encode_settings(settings: StoredAiSettings) -> bytes:
    return json.dumps(
        {
            "version": "ai_settings.v1",
            "provider": settings.provider.value,
            "model_id": settings.model_id,
            "api_key": settings.api_key,
            "data_use_confirmed": settings.data_use_confirmed,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_settings(raw: bytes) -> StoredAiSettings:
    value: Any = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or value.get("version") != "ai_settings.v1":
        raise ValueError("AI settings version is unsupported")
    provider = value.get("provider")
    model_id = value.get("model_id")
    api_key = value.get("api_key")
    data_use_confirmed = value.get("data_use_confirmed")
    if (
        not isinstance(provider, str)
        or not isinstance(model_id, str)
        or not isinstance(api_key, str)
        or not isinstance(data_use_confirmed, bool)
    ):
        raise ValueError("AI settings are invalid")
    return StoredAiSettings(
        provider=ModelProviderKind(provider),
        model_id=model_id,
        api_key=api_key,
        data_use_confirmed=data_use_confirmed,
    )
