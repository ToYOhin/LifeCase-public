"""Pure-Python composition for explicitly selected model providers."""

from __future__ import annotations

from lifecase.providers.config import ModelProviderConfig
from lifecase.providers.disabled import DisabledModelProvider
from lifecase.providers.errors import ProviderConfigurationError, ProviderErrorCode
from lifecase.providers.fake import FakeModelProvider, FakeProviderScript
from lifecase.providers.models import ModelProviderKind
from lifecase.providers.ollama_chat import OllamaChatClient, OllamaChatProvider
from lifecase.providers.openai_responses import OpenAIResponsesClient, OpenAIResponsesProvider
from lifecase.providers.protocol import ModelProvider


def build_model_provider(
    config: ModelProviderConfig,
    *,
    fake_scripts: tuple[FakeProviderScript, ...] = (),
    ollama_client: OllamaChatClient | None = None,
    openai_client: OpenAIResponsesClient | None = None,
) -> ModelProvider:
    """Build only the selected provider; never discover credentials implicitly."""

    if not isinstance(config, ModelProviderConfig):
        raise _configuration_error()
    if config.provider is ModelProviderKind.DISABLED:
        return DisabledModelProvider()
    if config.provider is ModelProviderKind.FAKE:
        return FakeModelProvider(config, fake_scripts)
    if config.provider is ModelProviderKind.OLLAMA:
        return OllamaChatProvider(config, client=ollama_client)
    if config.provider is ModelProviderKind.OPENAI and openai_client is not None:
        return OpenAIResponsesProvider(config, openai_client)
    raise _configuration_error()


def _configuration_error() -> ProviderConfigurationError:
    return ProviderConfigurationError(
        "Model provider runtime is not configured",
        code=ProviderErrorCode.CONFIGURATION,
        retryable=False,
    )
