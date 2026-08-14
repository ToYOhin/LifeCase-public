"""Server-owned Provider profiles and fail-closed live composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from lifecase.providers.config import ModelProviderConfig
from lifecase.providers.deepseek_chat import DEEPSEEK_CHAT_ENDPOINT
from lifecase.providers.errors import ProviderConfigurationError, ProviderErrorCode
from lifecase.providers.groq_chat import GROQ_CHAT_ENDPOINT
from lifecase.providers.mistral_chat import MISTRAL_CHAT_ENDPOINT
from lifecase.providers.models import (
    ANTHROPIC_MESSAGES_MODEL_ID,
    DEEPSEEK_CHAT_MODEL_IDS,
    GEMINI_GENERATE_MODEL_ID,
    GROQ_CHAT_MODEL_ID,
    MISTRAL_CHAT_MODEL_ID,
    ModelProviderKind,
)
from lifecase.providers.openai_responses import OpenAIResponsesClient, OpenAIResponsesProvider
from lifecase.providers.protocol import ModelProvider

if TYPE_CHECKING:
    from lifecase.providers.deepseek_chat import DeepSeekChatClient
    from lifecase.providers.google_anthropic import DirectJsonClient
    from lifecase.providers.groq_chat import GroqChatClient
    from lifecase.providers.mistral_chat import MistralChatClient


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Security-relevant properties required before live product composition."""

    structured_output: Literal["strict_json_schema", "json_object_local_schema"]
    usage_metadata: Literal["required"]
    cost_metadata: Literal["unavailable", "optional"]
    storage_control: Literal[
        "request_store_false", "organization_zdr", "provider_policy_synthetic_only"
    ]
    storage_control_confirmed: bool
    streaming: Literal["disabled"]
    tools: Literal["disabled"]
    credential_source: Literal["process_injected_client"]


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """Resolved immutable profile; no credential or arbitrary endpoint is admitted."""

    profile_id: str
    provider: ModelProviderKind
    endpoint_preset: str
    endpoint: str
    allowed_model_ids: tuple[str, ...]
    model_id: str
    capabilities: ProviderCapabilities
    timeout_seconds: float
    max_input_bytes: int
    max_output_bytes: int

    @property
    def live_safe(self) -> bool:
        """Return whether every required live read-only control is represented."""

        capabilities = self.capabilities
        return (
            capabilities.structured_output in {"strict_json_schema", "json_object_local_schema"}
            and capabilities.usage_metadata == "required"
            and capabilities.storage_control_confirmed
            and capabilities.streaming == "disabled"
            and capabilities.tools == "disabled"
            and capabilities.credential_source == "process_injected_client"
        )


@dataclass(frozen=True, slots=True)
class ProviderRuntimeClients:
    """Process-local injected clients; credentials never enter configuration."""

    openai: OpenAIResponsesClient | None = None
    groq: GroqChatClient | None = None
    mistral: MistralChatClient | None = None
    deepseek: DeepSeekChatClient | None = None
    gemini: DirectJsonClient | None = None
    anthropic: DirectJsonClient | None = None


class ProviderRegistry:
    """Resolve fixed profiles and compose only live-safe injected adapters."""

    def profile(
        self,
        config: ModelProviderConfig,
        *,
        groq_zdr_confirmed: bool = False,
        mistral_zdr_confirmed: bool = False,
        deepseek_data_use_confirmed: bool = False,
        gemini_data_use_confirmed: bool = False,
        anthropic_data_use_confirmed: bool = False,
    ) -> ProviderProfile:
        if not isinstance(config, ModelProviderConfig) or config.model_id is None:
            raise _configuration_error()
        if config.provider is ModelProviderKind.OPENAI:
            return ProviderProfile(
                profile_id="openai-responses-official.v1",
                provider=config.provider,
                endpoint_preset="openai-responses-official",
                endpoint="https://api.openai.com/v1/responses",
                allowed_model_ids=(config.model_id,),
                model_id=config.model_id,
                capabilities=ProviderCapabilities(
                    structured_output="strict_json_schema",
                    usage_metadata="required",
                    cost_metadata="unavailable",
                    storage_control="request_store_false",
                    storage_control_confirmed=True,
                    streaming="disabled",
                    tools="disabled",
                    credential_source="process_injected_client",
                ),
                timeout_seconds=config.timeout_seconds,
                max_input_bytes=config.max_input_bytes,
                max_output_bytes=config.max_output_bytes,
            )
        if config.provider is ModelProviderKind.GROQ and config.model_id == GROQ_CHAT_MODEL_ID:
            return ProviderProfile(
                profile_id="groq-chat-official-zdr.v1",
                provider=config.provider,
                endpoint_preset="groq-chat-official",
                endpoint=GROQ_CHAT_ENDPOINT,
                allowed_model_ids=(GROQ_CHAT_MODEL_ID,),
                model_id=config.model_id,
                capabilities=ProviderCapabilities(
                    structured_output="strict_json_schema",
                    usage_metadata="required",
                    cost_metadata="unavailable",
                    storage_control="organization_zdr",
                    storage_control_confirmed=groq_zdr_confirmed,
                    streaming="disabled",
                    tools="disabled",
                    credential_source="process_injected_client",
                ),
                timeout_seconds=config.timeout_seconds,
                max_input_bytes=config.max_input_bytes,
                max_output_bytes=config.max_output_bytes,
            )
        if (
            config.provider is ModelProviderKind.MISTRAL
            and config.model_id == MISTRAL_CHAT_MODEL_ID
        ):
            return ProviderProfile(
                profile_id="mistral-chat-official-zdr.v1",
                provider=config.provider,
                endpoint_preset="mistral-chat-official",
                endpoint=MISTRAL_CHAT_ENDPOINT,
                allowed_model_ids=(MISTRAL_CHAT_MODEL_ID,),
                model_id=config.model_id,
                capabilities=ProviderCapabilities(
                    structured_output="strict_json_schema",
                    usage_metadata="required",
                    cost_metadata="unavailable",
                    storage_control="organization_zdr",
                    storage_control_confirmed=mistral_zdr_confirmed,
                    streaming="disabled",
                    tools="disabled",
                    credential_source="process_injected_client",
                ),
                timeout_seconds=config.timeout_seconds,
                max_input_bytes=config.max_input_bytes,
                max_output_bytes=config.max_output_bytes,
            )
        if (
            config.provider is ModelProviderKind.DEEPSEEK
            and config.model_id in DEEPSEEK_CHAT_MODEL_IDS
        ):
            return ProviderProfile(
                profile_id="deepseek-chat-official-synthetic.v1",
                provider=config.provider,
                endpoint_preset="deepseek-chat-official",
                endpoint=DEEPSEEK_CHAT_ENDPOINT,
                allowed_model_ids=tuple(sorted(DEEPSEEK_CHAT_MODEL_IDS)),
                model_id=config.model_id,
                capabilities=ProviderCapabilities(
                    structured_output="json_object_local_schema",
                    usage_metadata="required",
                    cost_metadata="optional",
                    storage_control="provider_policy_synthetic_only",
                    storage_control_confirmed=deepseek_data_use_confirmed,
                    streaming="disabled",
                    tools="disabled",
                    credential_source="process_injected_client",
                ),
                timeout_seconds=config.timeout_seconds,
                max_input_bytes=config.max_input_bytes,
                max_output_bytes=config.max_output_bytes,
            )
        if (
            config.provider is ModelProviderKind.GEMINI
            and config.model_id == GEMINI_GENERATE_MODEL_ID
        ):
            from lifecase.providers.google_anthropic import GEMINI_GENERATE_ENDPOINT

            return ProviderProfile(
                profile_id="gemini-generate-content-official.v1",
                provider=config.provider,
                endpoint_preset="gemini-generate-content-official",
                endpoint=GEMINI_GENERATE_ENDPOINT,
                allowed_model_ids=(GEMINI_GENERATE_MODEL_ID,),
                model_id=config.model_id,
                capabilities=ProviderCapabilities(
                    structured_output="strict_json_schema",
                    usage_metadata="required",
                    cost_metadata="unavailable",
                    storage_control="provider_policy_synthetic_only",
                    storage_control_confirmed=gemini_data_use_confirmed,
                    streaming="disabled",
                    tools="disabled",
                    credential_source="process_injected_client",
                ),
                timeout_seconds=config.timeout_seconds,
                max_input_bytes=config.max_input_bytes,
                max_output_bytes=config.max_output_bytes,
            )
        if (
            config.provider is ModelProviderKind.ANTHROPIC
            and config.model_id == ANTHROPIC_MESSAGES_MODEL_ID
        ):
            from lifecase.providers.google_anthropic import ANTHROPIC_MESSAGES_ENDPOINT

            return ProviderProfile(
                profile_id="anthropic-messages-official.v1",
                provider=config.provider,
                endpoint_preset="anthropic-messages-official",
                endpoint=ANTHROPIC_MESSAGES_ENDPOINT,
                allowed_model_ids=(ANTHROPIC_MESSAGES_MODEL_ID,),
                model_id=config.model_id,
                capabilities=ProviderCapabilities(
                    structured_output="strict_json_schema",
                    usage_metadata="required",
                    cost_metadata="unavailable",
                    storage_control="provider_policy_synthetic_only",
                    storage_control_confirmed=anthropic_data_use_confirmed,
                    streaming="disabled",
                    tools="disabled",
                    credential_source="process_injected_client",
                ),
                timeout_seconds=config.timeout_seconds,
                max_input_bytes=config.max_input_bytes,
                max_output_bytes=config.max_output_bytes,
            )
        raise _configuration_error()

    def compose(
        self,
        config: ModelProviderConfig,
        *,
        clients: ProviderRuntimeClients,
        groq_zdr_confirmed: bool = False,
        mistral_zdr_confirmed: bool = False,
        deepseek_data_use_confirmed: bool = False,
        gemini_data_use_confirmed: bool = False,
        anthropic_data_use_confirmed: bool = False,
    ) -> ModelProvider:
        profile = self.profile(
            config,
            groq_zdr_confirmed=groq_zdr_confirmed,
            mistral_zdr_confirmed=mistral_zdr_confirmed,
            deepseek_data_use_confirmed=deepseek_data_use_confirmed,
            gemini_data_use_confirmed=gemini_data_use_confirmed,
            anthropic_data_use_confirmed=anthropic_data_use_confirmed,
        )
        if not profile.live_safe:
            raise _configuration_error()
        if profile.provider is ModelProviderKind.OPENAI and clients.openai is not None:
            return OpenAIResponsesProvider(config, clients.openai)
        if profile.provider is ModelProviderKind.GROQ and clients.groq is not None:
            from lifecase.providers.groq_chat import GroqChatClient, GroqChatProvider

            if (
                not isinstance(clients.groq, GroqChatClient)
                or clients.groq.endpoint != GROQ_CHAT_ENDPOINT
            ):
                raise _configuration_error()
            return GroqChatProvider(config, clients.groq)
        if profile.provider is ModelProviderKind.MISTRAL and clients.mistral is not None:
            from lifecase.providers.mistral_chat import MistralChatClient, MistralChatProvider

            if (
                not isinstance(clients.mistral, MistralChatClient)
                or clients.mistral.endpoint != MISTRAL_CHAT_ENDPOINT
            ):
                raise _configuration_error()
            return MistralChatProvider(config, clients.mistral)
        if profile.provider is ModelProviderKind.DEEPSEEK and clients.deepseek is not None:
            from lifecase.providers.deepseek_chat import DeepSeekChatClient, DeepSeekJsonProvider

            if (
                not isinstance(clients.deepseek, DeepSeekChatClient)
                or clients.deepseek.endpoint != DEEPSEEK_CHAT_ENDPOINT
            ):
                raise _configuration_error()
            return DeepSeekJsonProvider(
                clients.deepseek,
                model_id=profile.model_id,
                timeout_seconds=config.timeout_seconds,
                max_input_bytes=config.max_input_bytes,
                max_output_bytes=config.max_output_bytes,
            )
        if profile.provider is ModelProviderKind.GEMINI and clients.gemini is not None:
            from lifecase.providers.google_anthropic import (
                GEMINI_GENERATE_ENDPOINT,
                DirectJsonClient,
                GeminiGenerateProvider,
            )

            if (
                not isinstance(clients.gemini, DirectJsonClient)
                or clients.gemini.endpoint != GEMINI_GENERATE_ENDPOINT
            ):
                raise _configuration_error()
            return GeminiGenerateProvider(config, clients.gemini)
        if profile.provider is ModelProviderKind.ANTHROPIC and clients.anthropic is not None:
            from lifecase.providers.google_anthropic import (
                ANTHROPIC_MESSAGES_ENDPOINT,
                AnthropicMessagesProvider,
                DirectJsonClient,
            )

            if (
                not isinstance(clients.anthropic, DirectJsonClient)
                or clients.anthropic.endpoint != ANTHROPIC_MESSAGES_ENDPOINT
            ):
                raise _configuration_error()
            return AnthropicMessagesProvider(config, clients.anthropic)
        raise _configuration_error()


def _configuration_error() -> ProviderConfigurationError:
    return ProviderConfigurationError(
        "Model provider runtime is not configured",
        code=ProviderErrorCode.CONFIGURATION,
        retryable=False,
    )
