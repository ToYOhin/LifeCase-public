"""Safe structural diagnostics for evaluation-only model candidates."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any, TypeGuard


class SchemaDiagnosticCategory(StrEnum):
    """Closed, content-free reasons a candidate differs from its JSON Schema."""

    NON_JSON = "non_json"
    ENVELOPE_FIELDS = "envelope_fields"
    STAGE_CONST = "stage_const"
    CHECKPOINT_SHAPE = "checkpoint_shape"
    TYPE = "type"
    ENUM = "enum"
    ADDITIONAL_FIELDS = "additional_fields"
    LENGTH_BOUNDARY = "length_boundary"
    PATTERN = "pattern"
    NUMERIC_BOUNDARY = "numeric_boundary"
    BUSINESS_VALIDATION = "business_validation"


_CATEGORY_ORDER = tuple(SchemaDiagnosticCategory)
_STAGE_CONST_FIELDS = {"stage", "stage_version", "checkpoint_schema_version"}


def classify_schema_mismatches(
    output_json: str,
    output_schema_json: str,
) -> tuple[SchemaDiagnosticCategory, ...]:
    """Classify without returning candidate values, paths, or validator details."""

    try:
        value = json.loads(output_json)
        schema = json.loads(output_schema_json)
    except (json.JSONDecodeError, TypeError, UnicodeError):
        return (SchemaDiagnosticCategory.NON_JSON,)
    if not isinstance(schema, dict):
        return (SchemaDiagnosticCategory.CHECKPOINT_SHAPE,)
    categories: set[SchemaDiagnosticCategory] = set()
    _classify(value, schema, (), categories)
    return tuple(category for category in _CATEGORY_ORDER if category in categories)


def _classify(
    value: object,
    schema: dict[str, Any],
    path: tuple[str, ...],
    categories: set[SchemaDiagnosticCategory],
) -> None:
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        categories.add(
            SchemaDiagnosticCategory.ENVELOPE_FIELDS
            if not path
            else (
                SchemaDiagnosticCategory.CHECKPOINT_SHAPE
                if path == ("checkpoint",)
                else SchemaDiagnosticCategory.TYPE
            )
        )
        return

    if "const" in schema and value != schema["const"]:
        categories.add(
            SchemaDiagnosticCategory.STAGE_CONST
            if len(path) == 1 and path[0] in _STAGE_CONST_FIELDS
            else SchemaDiagnosticCategory.CHECKPOINT_SHAPE
        )
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        categories.add(SchemaDiagnosticCategory.ENUM)

    if isinstance(value, dict):
        properties = schema.get("properties")
        known = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        if isinstance(required, list) and any(key not in value for key in required):
            categories.add(
                SchemaDiagnosticCategory.ENVELOPE_FIELDS
                if not path
                else SchemaDiagnosticCategory.CHECKPOINT_SHAPE
            )
        if schema.get("additionalProperties") is False and any(key not in known for key in value):
            categories.add(SchemaDiagnosticCategory.ADDITIONAL_FIELDS)
        for key, child in value.items():
            child_schema = known.get(key)
            if isinstance(child_schema, dict):
                _classify(child, child_schema, (*path, key), categories)
    elif isinstance(value, list):
        _classify_length(len(value), schema, categories, item=True)
        if schema.get("uniqueItems") is True and len({_json_key(item) for item in value}) != len(
            value
        ):
            categories.add(SchemaDiagnosticCategory.CHECKPOINT_SHAPE)
        items = schema.get("items")
        if isinstance(items, dict):
            for child in value:
                _classify(child, items, (*path, "[]"), categories)
    elif isinstance(value, str):
        _classify_length(len(value), schema, categories, item=False)
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                if re.search(pattern, value) is None:
                    categories.add(SchemaDiagnosticCategory.PATTERN)
            except re.error:
                categories.add(SchemaDiagnosticCategory.CHECKPOINT_SHAPE)
    elif _is_number(value):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if (_is_number(minimum) and value < minimum) or (_is_number(maximum) and value > maximum):
            categories.add(SchemaDiagnosticCategory.NUMERIC_BOUNDARY)


def _matches_type(value: object, expected: object) -> bool:
    names = (expected,) if isinstance(expected, str) else expected
    if not isinstance(names, list | tuple):
        return False
    return any(_matches_named_type(value, name) for name in names)


def _matches_named_type(value: object, name: object) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return _is_number(value)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    return False


def _classify_length(
    length: int,
    schema: dict[str, Any],
    categories: set[SchemaDiagnosticCategory],
    *,
    item: bool,
) -> None:
    minimum = schema.get("minItems" if item else "minLength")
    maximum = schema.get("maxItems" if item else "maxLength")
    if (isinstance(minimum, int) and length < minimum) or (
        isinstance(maximum, int) and length > maximum
    ):
        categories.add(SchemaDiagnosticCategory.LENGTH_BOUNDARY)


def _json_key(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)
