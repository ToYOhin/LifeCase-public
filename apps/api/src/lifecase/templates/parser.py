"""Strict parser for reviewed built-in YAML Case templates."""

import hashlib
from collections.abc import Mapping
from typing import Any

import yaml  # type: ignore[import-untyped]
from yaml.events import (  # type: ignore[import-untyped]
    AliasEvent,
    CollectionEndEvent,
    CollectionStartEvent,
    DocumentStartEvent,
    NodeEvent,
)
from yaml.nodes import MappingNode  # type: ignore[import-untyped]

from lifecase.templates.models import (
    CaseTemplate,
    ConditionalClaim,
    EvidenceRequirement,
    TemplateModelValidationError,
)

_MAX_TEMPLATE_BYTES = 65_536
_MAX_TEMPLATE_DEPTH = 8
_TOP_LEVEL_KEYS = frozenset(
    {
        "id",
        "version",
        "title",
        "goal_options",
        "required_claims",
        "conditional_claims",
        "evidence_requirements",
        "close_conditions",
        "forbidden_actions",
    }
)


class TemplateParseError(ValueError):
    """Raised when a built-in template fails strict, safe validation."""


class _DuplicateKeyError(yaml.YAMLError):  # type: ignore[misc]
    pass


class _StrictSafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    pass


def _construct_mapping(
    loader: _StrictSafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise _DuplicateKeyError from error
        if duplicate:
            raise _DuplicateKeyError
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def parse_case_template(payload: bytes) -> CaseTemplate:
    """Return a deterministic typed template or one safe validation error."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= _MAX_TEMPLATE_BYTES:
        raise TemplateParseError("Built-in template is invalid")
    try:
        text = payload.decode("utf-8", errors="strict")
        _validate_yaml_events(text)
        document = yaml.load(text, Loader=_StrictSafeLoader)
        mapping = _mapping(document, expected_keys=_TOP_LEVEL_KEYS)
        return CaseTemplate(
            id=_required(mapping, "id"),
            version=_required(mapping, "version"),
            title=_required(mapping, "title"),
            goal_options=_identifier_list(_required(mapping, "goal_options")),
            required_claims=_identifier_list(_required(mapping, "required_claims")),
            conditional_claims=_conditional_claims(_required(mapping, "conditional_claims")),
            evidence_requirements=_evidence_requirements(
                _required(mapping, "evidence_requirements")
            ),
            close_conditions=_identifier_list(_required(mapping, "close_conditions")),
            forbidden_actions=_identifier_list(_required(mapping, "forbidden_actions")),
            content_sha256=hashlib.sha256(payload).hexdigest(),
        )
    except TemplateParseError:
        raise
    except (
        UnicodeDecodeError,
        yaml.YAMLError,
        TemplateModelValidationError,
        RecursionError,
    ) as error:
        raise TemplateParseError("Built-in template is invalid") from error


def _validate_yaml_events(text: str) -> None:
    depth = 0
    documents = 0
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, DocumentStartEvent):
            documents += 1
            if documents > 1:
                raise TemplateParseError("Built-in template is invalid")
        if isinstance(event, AliasEvent):
            raise TemplateParseError("Built-in template is invalid")
        if isinstance(event, NodeEvent) and (event.anchor is not None or event.tag is not None):
            raise TemplateParseError("Built-in template is invalid")
        if isinstance(event, CollectionStartEvent):
            depth += 1
            if depth > _MAX_TEMPLATE_DEPTH:
                raise TemplateParseError("Built-in template is invalid")
        elif isinstance(event, CollectionEndEvent):
            depth -= 1


def _mapping(value: object, *, expected_keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, dict) or not value:
        raise TemplateParseError("Built-in template is invalid")
    if not all(isinstance(key, str) for key in value):
        raise TemplateParseError("Built-in template is invalid")
    typed = value
    if frozenset(typed) != expected_keys:
        raise TemplateParseError("Built-in template is invalid")
    return typed


def _required(mapping: Mapping[str, object], key: str) -> Any:
    value = mapping[key]
    if value is None:
        raise TemplateParseError("Built-in template is invalid")
    return value


def _identifier_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 50:
        raise TemplateParseError("Built-in template is invalid")
    if not all(isinstance(item, str) for item in value):
        raise TemplateParseError("Built-in template is invalid")
    return tuple(value)


def _conditional_claims(value: object) -> tuple[ConditionalClaim, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 50:
        raise TemplateParseError("Built-in template is invalid")
    result: list[ConditionalClaim] = []
    for item in value:
        mapping = _mapping(item, expected_keys=frozenset({"when", "field"}))
        result.append(
            ConditionalClaim(
                when=_required(mapping, "when"),
                field=_required(mapping, "field"),
            )
        )
    return tuple(result)


def _evidence_requirements(value: object) -> tuple[EvidenceRequirement, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 50:
        raise TemplateParseError("Built-in template is invalid")
    result: list[EvidenceRequirement] = []
    for item in value:
        mapping = _mapping(item, expected_keys=frozenset({"key", "description"}))
        result.append(
            EvidenceRequirement(
                key=_required(mapping, "key"),
                description=_required(mapping, "description"),
            )
        )
    return tuple(result)
