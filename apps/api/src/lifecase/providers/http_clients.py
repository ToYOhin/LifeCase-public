"""Small fixed-endpoint async JSON clients for UI-configured API credentials."""

from __future__ import annotations

import asyncio
import http.client
import json
import ssl
from types import SimpleNamespace
from urllib.parse import urlsplit

from lifecase.providers.groq_chat import GROQ_CHAT_ENDPOINT
from lifecase.providers.mistral_chat import MISTRAL_CHAT_ENDPOINT

OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"


class SafeHttpStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("Provider request failed")


class FixedJsonHttpResource:
    """POST JSON to one constructor-owned HTTPS endpoint with bounded output."""

    def __init__(
        self,
        endpoint: str,
        headers: dict[str, str],
        *,
        timeout_seconds: float = 120.0,
        max_response_bytes: int = 256_000,
    ) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.path:
            raise ValueError("Provider endpoint is invalid")
        self.endpoint = endpoint
        self._host = parsed.hostname
        self._port = parsed.port or 443
        self._path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        self._headers = {**headers, "Content-Type": "application/json"}
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def create(self, **kwargs: object) -> object:
        return await asyncio.to_thread(self._create_sync, kwargs)

    def _create_sync(self, payload: dict[str, object]) -> object:
        connection = http.client.HTTPSConnection(
            self._host,
            self._port,
            timeout=self._timeout_seconds,
            context=ssl.create_default_context(),
        )
        try:
            connection.request(
                "POST",
                self._path,
                body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
                headers=self._headers,
            )
            response = connection.getresponse()
            raw = response.read(self._max_response_bytes + 1)
            if response.status >= 400:
                raise SafeHttpStatusError(response.status)
            if len(raw) > self._max_response_bytes:
                raise ValueError("Provider response exceeded the client limit")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("Provider response is invalid")
            return value
        finally:
            connection.close()


class OpenAIHttpClient:
    def __init__(self, api_key: str) -> None:
        self.responses = FixedJsonHttpResource(
            OPENAI_RESPONSES_ENDPOINT,
            {"Authorization": f"Bearer {api_key}"},
        )


class OpenAICompatibleChatHttpClient:
    def __init__(self, endpoint: str, api_key: str) -> None:
        if endpoint not in {GROQ_CHAT_ENDPOINT, MISTRAL_CHAT_ENDPOINT}:
            raise ValueError("Chat endpoint is unsupported")
        self.endpoint = endpoint
        self.chat = SimpleNamespace(
            completions=FixedJsonHttpResource(
                endpoint,
                {"Authorization": f"Bearer {api_key}"},
            )
        )
