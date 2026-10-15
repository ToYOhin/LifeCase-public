"""Thin read-only HTTP contract for reviewed built-in Case templates."""

from fastapi import APIRouter
from pydantic import BaseModel

from lifecase.api.errors import ApiError
from lifecase.templates import BUILT_IN_TEMPLATE_REGISTRY, TemplateRegistryError

router = APIRouter()


class TemplateEvidenceRequirementResponse(BaseModel):
    """One bounded user-visible evidence category."""

    key: str
    description: str


class TemplateConditionalClaimResponse(BaseModel):
    """One available field that is required only under its existing condition."""

    when: str
    field: str


class TemplateCatalogResponse(BaseModel):
    """One reviewed selectable Case type and its immutable template identity."""

    case_type: str
    template_id: str
    template_version: int
    title: str
    goal_options: list[str]
    required_claims: list[str]
    conditional_claims: list[TemplateConditionalClaimResponse]
    evidence_requirements: list[TemplateEvidenceRequirementResponse]
    content_sha256: str


@router.get("", response_model=list[TemplateCatalogResponse])
def list_templates() -> list[TemplateCatalogResponse]:
    """Return the closed built-in catalog without accepting paths or template bodies."""

    try:
        catalog = BUILT_IN_TEMPLATE_REGISTRY.catalog()
    except TemplateRegistryError as error:
        raise ApiError(
            status_code=503,
            code="template_catalog_unavailable",
            message="Template catalog could not be read",
            retryable=True,
        ) from error
    return [
        TemplateCatalogResponse(
            case_type=item.case_type,
            template_id=item.template_id,
            template_version=item.template_version,
            title=item.title,
            goal_options=list(item.goal_options),
            required_claims=list(item.required_claims),
            conditional_claims=[
                TemplateConditionalClaimResponse(when=condition.when, field=condition.field)
                for condition in item.conditional_claims
            ],
            evidence_requirements=[
                TemplateEvidenceRequirementResponse(
                    key=requirement.key,
                    description=requirement.description,
                )
                for requirement in item.evidence_requirements
            ],
            content_sha256=item.content_sha256,
        )
        for item in catalog
    ]
