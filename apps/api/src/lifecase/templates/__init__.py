"""Versioned, deterministic built-in Case template engine."""

from lifecase.templates.models import (
    CaseTemplate,
    CloseConditionEvaluation,
    ConditionalClaim,
    EvaluateTemplateCommand,
    EvidenceRequirement,
    TemplateBinding,
    TemplateCatalogItem,
    TemplateEvaluation,
    TemplateModelValidationError,
)
from lifecase.templates.parser import TemplateParseError, parse_case_template
from lifecase.templates.registry import (
    BUILT_IN_TEMPLATE_REGISTRY,
    BuiltInTemplateRegistry,
    TemplateBindingMismatchError,
    TemplateNotFoundError,
    TemplateRegistryError,
    TemplateResourceError,
)

__all__ = [
    "BUILT_IN_TEMPLATE_REGISTRY",
    "BuiltInTemplateRegistry",
    "CaseTemplate",
    "CloseConditionEvaluation",
    "ConditionalClaim",
    "EvaluateTemplateCommand",
    "EvidenceRequirement",
    "TemplateBinding",
    "TemplateCatalogItem",
    "TemplateBindingMismatchError",
    "TemplateEvaluation",
    "TemplateModelValidationError",
    "TemplateNotFoundError",
    "TemplateParseError",
    "TemplateRegistryError",
    "TemplateResourceError",
    "parse_case_template",
]
