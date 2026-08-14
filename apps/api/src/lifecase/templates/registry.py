"""Closed registry for reviewed built-in Case template resources."""

from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from lifecase.templates.models import CaseTemplate, TemplateBinding, TemplateCatalogItem
from lifecase.templates.parser import TemplateParseError, parse_case_template


class TemplateRegistryError(RuntimeError):
    """Base class for safe built-in template registry failures."""


class TemplateNotFoundError(TemplateRegistryError):
    """Raised when no reviewed binding exists for a Case type and version."""


class TemplateBindingMismatchError(TemplateRegistryError):
    """Raised when resource identity does not match its reviewed registry entry."""


class TemplateResourceError(TemplateRegistryError):
    """Raised when a reviewed resource cannot be loaded or parsed safely."""


_WARRANTY_BINDING = TemplateBinding(
    case_type="warranty",
    template_id="warranty_claim",
    template_version=1,
    resource_name="warranty_claim.v1.yaml",
)
_RETURN_REFUND_BINDING = TemplateBinding(
    case_type="return_refund",
    template_id="return_refund_claim",
    template_version=1,
    resource_name="return_refund_claim.v1.yaml",
)
_REIMBURSEMENT_BINDING = TemplateBinding(
    case_type="reimbursement",
    template_id="expense_reimbursement",
    template_version=1,
    resource_name="expense_reimbursement.v1.yaml",
)
_SUBSCRIPTION_CANCELLATION_BINDING = TemplateBinding(
    case_type="subscription_cancellation",
    template_id="subscription_cancellation_claim",
    template_version=1,
    resource_name="subscription_cancellation_claim.v1.yaml",
)
_ORDERED_BINDINGS = (
    _WARRANTY_BINDING,
    _RETURN_REFUND_BINDING,
    _REIMBURSEMENT_BINDING,
    _SUBSCRIPTION_CANCELLATION_BINDING,
)
_BINDINGS = {
    (binding.case_type, binding.template_version): binding for binding in _ORDERED_BINDINGS
}
_TEMPLATE_ROOT = Path(__file__).resolve().parents[5] / "packages" / "case-templates"


class BuiltInTemplateRegistry:
    """Load only exact resources named by the closed application registry."""

    def binding_for(self, case_type: str, template_version: int) -> TemplateBinding:
        if (
            not isinstance(case_type, str)
            or not isinstance(template_version, int)
            or isinstance(template_version, bool)
        ):
            raise TemplateNotFoundError("Built-in template binding was not found")
        try:
            return _BINDINGS[(case_type, template_version)]
        except (KeyError, TypeError) as error:
            raise TemplateNotFoundError("Built-in template binding was not found") from error

    def load_for(self, case_type: str, template_version: int) -> CaseTemplate:
        binding = self.binding_for(case_type, template_version)
        return _load_registered_template(binding)

    def load_exact(
        self,
        *,
        case_type: str,
        template_id: str,
        template_version: int,
    ) -> CaseTemplate:
        binding = self.binding_for(case_type, template_version)
        if binding.template_id != template_id:
            raise TemplateBindingMismatchError("Case template binding does not match")
        return self.load_for(case_type, template_version)

    def catalog(self) -> tuple[TemplateCatalogItem, ...]:
        """Return the closed reviewed catalog in stable product order."""

        return tuple(
            _catalog_item(
                binding,
                self.load_for(binding.case_type, binding.template_version),
            )
            for binding in _ORDERED_BINDINGS
        )


@lru_cache(maxsize=8)
def _load_registered_template(binding: TemplateBinding) -> CaseTemplate:
    try:
        template = parse_case_template(_read_resource(binding.resource_name))
    except (OSError, TemplateParseError) as error:
        raise TemplateResourceError("Built-in template could not be loaded") from error
    if template.id != binding.template_id or template.version != binding.template_version:
        raise TemplateBindingMismatchError("Built-in template identity does not match")
    return template


BUILT_IN_TEMPLATE_REGISTRY = BuiltInTemplateRegistry()


def _catalog_item(binding: TemplateBinding, template: CaseTemplate) -> TemplateCatalogItem:
    return TemplateCatalogItem(
        case_type=binding.case_type,
        template_id=template.id,
        template_version=template.version,
        title=template.title,
        goal_options=template.goal_options,
        required_claims=template.required_claims,
        evidence_requirements=template.evidence_requirements,
        content_sha256=template.content_sha256,
    )


def _read_resource(resource_name: str) -> bytes:
    packaged = files("lifecase.templates").joinpath("resources", resource_name)
    try:
        return packaged.read_bytes()
    except FileNotFoundError:
        return (_TEMPLATE_ROOT / resource_name).read_bytes()
