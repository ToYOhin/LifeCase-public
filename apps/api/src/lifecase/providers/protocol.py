"""Provider-neutral protocol used by later bounded Agent stages."""

from typing import Protocol, runtime_checkable

from lifecase.providers.models import StructuredGenerationRequest, StructuredGenerationResult


@runtime_checkable
class ModelProvider(Protocol):
    """Generate one structured candidate without exposing SDK response types."""

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult: ...
