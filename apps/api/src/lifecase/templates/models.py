"""Immutable domain models for reviewed, versioned Case templates."""

import re
from dataclasses import dataclass

_IDENTIFIER_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_RESOURCE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,99}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class TemplateModelValidationError(ValueError):
    """Raised when typed template data violates its stable boundary."""


def validate_identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise TemplateModelValidationError("Template identifier is invalid")
    return value


def _validate_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise TemplateModelValidationError("Template text is invalid")
    clean = value.strip()
    if not 1 <= len(clean) <= maximum:
        raise TemplateModelValidationError("Template text is invalid")
    return clean


def _validate_identifiers(values: object) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not 1 <= len(values) <= 50:
        raise TemplateModelValidationError("Template identifier list is invalid")
    validated = tuple(validate_identifier(value) for value in values)
    if len(set(validated)) != len(validated):
        raise TemplateModelValidationError("Template identifier list is invalid")
    return validated


@dataclass(frozen=True, slots=True)
class ConditionalClaim:
    """One Claim required when a named, finite condition is enabled."""

    when: str
    field: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "when", validate_identifier(self.when))
        object.__setattr__(self, "field", validate_identifier(self.field))


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    """One evidence category represented by an authoritative Claim field."""

    key: str
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", validate_identifier(self.key))
        object.__setattr__(self, "description", _validate_text(self.description, maximum=500))


@dataclass(frozen=True, slots=True)
class CaseTemplate:
    """A completely validated, immutable Case workflow template."""

    id: str
    version: int
    title: str
    goal_options: tuple[str, ...]
    required_claims: tuple[str, ...]
    conditional_claims: tuple[ConditionalClaim, ...]
    evidence_requirements: tuple[EvidenceRequirement, ...]
    close_conditions: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    content_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", validate_identifier(self.id))
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version <= 0:
            raise TemplateModelValidationError("Template version is invalid")
        object.__setattr__(self, "title", _validate_text(self.title, maximum=160))
        object.__setattr__(self, "goal_options", _validate_identifiers(self.goal_options))
        object.__setattr__(self, "required_claims", _validate_identifiers(self.required_claims))
        object.__setattr__(self, "close_conditions", _validate_identifiers(self.close_conditions))
        object.__setattr__(self, "forbidden_actions", _validate_identifiers(self.forbidden_actions))
        if (
            not isinstance(self.conditional_claims, tuple)
            or not 1 <= len(self.conditional_claims) <= 50
        ):
            raise TemplateModelValidationError("Conditional Claims are invalid")
        if not all(isinstance(item, ConditionalClaim) for item in self.conditional_claims):
            raise TemplateModelValidationError("Conditional Claims are invalid")
        conditions = tuple(item.when for item in self.conditional_claims)
        fields = tuple(item.field for item in self.conditional_claims)
        if len(set(conditions)) != len(conditions) or len(set(fields)) != len(fields):
            raise TemplateModelValidationError("Conditional Claims are invalid")
        if (
            not isinstance(self.evidence_requirements, tuple)
            or not 1 <= len(self.evidence_requirements) <= 50
        ):
            raise TemplateModelValidationError("Evidence requirements are invalid")
        if not all(isinstance(item, EvidenceRequirement) for item in self.evidence_requirements):
            raise TemplateModelValidationError("Evidence requirements are invalid")
        evidence_keys = tuple(item.key for item in self.evidence_requirements)
        if len(set(evidence_keys)) != len(evidence_keys):
            raise TemplateModelValidationError("Evidence requirements are invalid")
        if not isinstance(self.content_sha256, str) or (
            _SHA256_PATTERN.fullmatch(self.content_sha256) is None
        ):
            raise TemplateModelValidationError("Template identity is invalid")

    @property
    def identity(self) -> str:
        """Return the stable logical template identity."""

        return f"{self.id}@{self.version}"


@dataclass(frozen=True, slots=True)
class TemplateBinding:
    """Reviewed mapping from a business Case type to one template version."""

    case_type: str
    template_id: str
    template_version: int
    resource_name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_type", validate_identifier(self.case_type))
        object.__setattr__(self, "template_id", validate_identifier(self.template_id))
        if (
            not isinstance(self.template_version, int)
            or isinstance(self.template_version, bool)
            or self.template_version <= 0
        ):
            raise TemplateModelValidationError("Template version is invalid")
        if (
            not isinstance(self.resource_name, str)
            or _RESOURCE_PATTERN.fullmatch(self.resource_name) is None
            or ".." in self.resource_name
        ):
            raise TemplateModelValidationError("Template resource is invalid")


@dataclass(frozen=True, slots=True)
class TemplateCatalogItem:
    """User-visible metadata for one reviewed built-in template binding."""

    case_type: str
    template_id: str
    template_version: int
    title: str
    goal_options: tuple[str, ...]
    required_claims: tuple[str, ...]
    conditional_claims: tuple[ConditionalClaim, ...]
    evidence_requirements: tuple[EvidenceRequirement, ...]
    content_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_type", validate_identifier(self.case_type))
        object.__setattr__(self, "template_id", validate_identifier(self.template_id))
        if (
            not isinstance(self.template_version, int)
            or isinstance(self.template_version, bool)
            or self.template_version <= 0
        ):
            raise TemplateModelValidationError("Template version is invalid")
        object.__setattr__(self, "title", _validate_text(self.title, maximum=160))
        object.__setattr__(self, "goal_options", _validate_identifiers(self.goal_options))
        object.__setattr__(self, "required_claims", _validate_identifiers(self.required_claims))
        if (
            not isinstance(self.conditional_claims, tuple)
            or not self.conditional_claims
            or not all(isinstance(item, ConditionalClaim) for item in self.conditional_claims)
        ):
            raise TemplateModelValidationError("Conditional claims are invalid")
        if (
            not isinstance(self.evidence_requirements, tuple)
            or not self.evidence_requirements
            or not all(isinstance(item, EvidenceRequirement) for item in self.evidence_requirements)
        ):
            raise TemplateModelValidationError("Evidence requirements are invalid")
        if not isinstance(self.content_sha256, str) or (
            _SHA256_PATTERN.fullmatch(self.content_sha256) is None
        ):
            raise TemplateModelValidationError("Template identity is invalid")


@dataclass(frozen=True, slots=True)
class EvaluateTemplateCommand:
    """Finite, deterministic inputs for one Case template evaluation."""

    case_id: str
    active_conditions: frozenset[str] = frozenset()
    completed_close_conditions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str):
            raise TemplateModelValidationError("Case ID is invalid")
        case_id = self.case_id.strip()
        if not 1 <= len(case_id) <= 200:
            raise TemplateModelValidationError("Case ID is invalid")
        object.__setattr__(self, "case_id", case_id)
        for values in (self.active_conditions, self.completed_close_conditions):
            if not isinstance(values, frozenset) or len(values) > 50:
                raise TemplateModelValidationError("Template evaluation input is invalid")
            for value in values:
                validate_identifier(value)


@dataclass(frozen=True, slots=True)
class CloseConditionEvaluation:
    """Pure close-condition result; it never changes Case lifecycle state."""

    satisfied: tuple[str, ...]
    remaining: tuple[str, ...]
    can_close: bool


@dataclass(frozen=True, slots=True)
class TemplateEvaluation:
    """Deterministic gaps and close readiness for one bound Case template."""

    case_id: str
    template_id: str
    template_version: int
    template_content_sha256: str
    missing_required_claims: tuple[str, ...]
    missing_conditional_claims: tuple[str, ...]
    missing_evidence_requirements: tuple[EvidenceRequirement, ...]
    close_conditions: CloseConditionEvaluation
