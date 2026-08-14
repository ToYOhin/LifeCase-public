"""Deterministic Case template evaluation over authoritative Claim state."""

import sqlite3

from lifecase.cases.service import (
    CaseNotFoundError,
    CasePersistenceError,
    CaseService,
    CaseTemplateBindingError,
)
from lifecase.claims.models import ClaimStatus
from lifecase.claims.service import ClaimService, ClaimServiceError
from lifecase.templates.models import (
    CloseConditionEvaluation,
    EvaluateTemplateCommand,
    TemplateEvaluation,
    TemplateModelValidationError,
)
from lifecase.templates.registry import (
    BUILT_IN_TEMPLATE_REGISTRY,
    BuiltInTemplateRegistry,
    TemplateRegistryError,
)

_SATISFYING_STATUSES = frozenset({ClaimStatus.CONFIRMED, ClaimStatus.CORRECTED})


class TemplateServiceError(RuntimeError):
    """Base class for safe template evaluation failures."""


class TemplateCaseNotFoundError(TemplateServiceError):
    """Raised when the target Case does not exist."""


class TemplateEvaluationInputError(TemplateServiceError):
    """Raised when finite condition inputs are unknown or invalid."""


class TemplateConfigurationError(TemplateServiceError):
    """Raised when a Case cannot be matched to a reviewed built-in template."""


class TemplateEvaluationPersistenceError(TemplateServiceError):
    """Raised when authoritative Case or Claim state cannot be read safely."""


class TemplateService:
    """Evaluate one Case without lifecycle mutation or external side effects."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        registry: BuiltInTemplateRegistry = BUILT_IN_TEMPLATE_REGISTRY,
    ) -> None:
        self._connection = connection
        self._registry = registry

    def evaluate(self, command: EvaluateTemplateCommand) -> TemplateEvaluation:
        try:
            case = CaseService(self._connection).get_case(command.case_id)
            template = self._registry.load_exact(
                case_type=case.type,
                template_id=case.template_id,
                template_version=case.template_version,
            )
            known_conditions = frozenset(item.when for item in template.conditional_claims)
            if not command.active_conditions <= known_conditions:
                raise TemplateEvaluationInputError("Template condition is not recognized")
            known_close_conditions = frozenset(template.close_conditions)
            if not command.completed_close_conditions <= known_close_conditions:
                raise TemplateEvaluationInputError("Close condition is not recognized")

            relevant_fields = frozenset(
                (
                    *template.required_claims,
                    *(item.field for item in template.conditional_claims),
                    *(item.key for item in template.evidence_requirements),
                )
            )
            satisfied_fields = self._load_satisfying_fields(case.id, relevant_fields)
            missing_required = tuple(
                field for field in template.required_claims if field not in satisfied_fields
            )
            missing_conditional = tuple(
                item.field
                for item in template.conditional_claims
                if item.when in command.active_conditions and item.field not in satisfied_fields
            )
            missing_evidence = tuple(
                requirement
                for requirement in template.evidence_requirements
                if requirement.key not in satisfied_fields
            )
            satisfied_close = tuple(
                condition
                for condition in template.close_conditions
                if condition in command.completed_close_conditions
            )
            remaining_close = tuple(
                condition
                for condition in template.close_conditions
                if condition not in command.completed_close_conditions
            )
            return TemplateEvaluation(
                case_id=case.id,
                template_id=template.id,
                template_version=template.version,
                template_content_sha256=template.content_sha256,
                missing_required_claims=missing_required,
                missing_conditional_claims=missing_conditional,
                missing_evidence_requirements=missing_evidence,
                close_conditions=CloseConditionEvaluation(
                    satisfied=satisfied_close,
                    remaining=remaining_close,
                    can_close=bool(satisfied_close),
                ),
            )
        except TemplateServiceError:
            raise
        except CaseNotFoundError as error:
            raise TemplateCaseNotFoundError from error
        except CaseTemplateBindingError as error:
            raise TemplateConfigurationError("Case template configuration is invalid") from error
        except TemplateRegistryError as error:
            raise TemplateConfigurationError("Case template configuration is invalid") from error
        except TemplateModelValidationError as error:
            raise TemplateEvaluationInputError("Template evaluation input is invalid") from error
        except (CasePersistenceError, sqlite3.Error, ValueError) as error:
            raise TemplateEvaluationPersistenceError(
                "Template evaluation could not read authoritative state"
            ) from error

    def _load_satisfying_fields(
        self,
        case_id: str,
        relevant_fields: frozenset[str],
    ) -> frozenset[str]:
        try:
            claims = ClaimService(self._connection).list_case_claims(case_id)
        except ClaimServiceError as error:
            raise TemplateEvaluationPersistenceError(
                "Template evaluation could not read authoritative Claim state"
            ) from error
        return frozenset(
            claim.field
            for claim in claims
            if claim.field in relevant_fields
            and claim.status in _SATISFYING_STATUSES
            and not claim.contradiction
        )
