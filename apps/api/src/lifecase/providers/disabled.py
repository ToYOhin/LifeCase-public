"""Disabled-by-default model provider."""

from lifecase.providers.errors import (
    ProviderConfigurationError,
    ProviderErrorCode,
    ProviderFailureMetadata,
)
from lifecase.providers.models import (
    ModelProviderKind,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)


class DisabledModelProvider:
    """Fail safely while leaving every deterministic subsystem available."""

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        if not isinstance(request, StructuredGenerationRequest):
            raise ProviderConfigurationError(
                "Model provider request is invalid",
                code=ProviderErrorCode.CONFIGURATION,
                retryable=False,
            )
        raise ProviderConfigurationError(
            "Cloud model provider is disabled",
            code=ProviderErrorCode.DISABLED,
            retryable=False,
            metadata=ProviderFailureMetadata(
                provider=ModelProviderKind.DISABLED,
                model_id=None,
                request_scope_sha256=request.request_scope_sha256,
                latency_ms=0,
                error_code=ProviderErrorCode.DISABLED,
            ),
        )
