"""Evaluation-only JSON Schema diagnostics retain categories, never values."""

from __future__ import annotations

import json

from lifecase.evaluation.schema_diagnostics import classify_schema_mismatches


def test_schema_diagnostics_classify_only_closed_structural_categories() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["stage", "stage_version", "checkpoint_schema_version", "checkpoint"],
        "properties": {
            "stage": {"const": "extract"},
            "stage_version": {"const": "extract.v1"},
            "checkpoint_schema_version": {"const": "extract.checkpoint.v1"},
            "checkpoint": {
                "type": "object",
                "additionalProperties": False,
                "required": ["status", "enabled", "items"],
                "properties": {
                    "status": {"enum": ["known", "unknown"]},
                    "enabled": {"type": "boolean"},
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "sha256", "score"],
                            "properties": {
                                "name": {"type": "string", "maxLength": 3},
                                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                                "score": {"type": "number", "minimum": 0, "maximum": 1},
                            },
                        },
                    },
                },
            },
        },
    }
    candidate = {
        "stage": "normalize",
        "stage_version": "extract.v1",
        "checkpoint_schema_version": "extract.checkpoint.v1",
        "checkpoint": {
            "status": "maybe",
            "enabled": "yes",
            "items": [
                {
                    "name": "long",
                    "sha256": "invalid",
                    "score": 2,
                    "extra": True,
                }
            ],
        },
        "commentary": "not retained by the classifier",
    }

    categories = classify_schema_mismatches(
        json.dumps(candidate),
        json.dumps(schema),
    )

    assert tuple(category.value for category in categories) == (
        "stage_const",
        "type",
        "enum",
        "additional_fields",
        "length_boundary",
        "pattern",
        "numeric_boundary",
    )


def test_schema_diagnostics_distinguish_non_json_envelope_and_checkpoint_shape() -> None:
    schema = json.dumps(
        {
            "type": "object",
            "required": ["stage", "checkpoint"],
            "properties": {
                "stage": {"const": "extract"},
                "checkpoint": {
                    "type": "object",
                    "required": ["facts"],
                    "properties": {"facts": {"type": "array"}},
                },
            },
        }
    )

    assert tuple(category.value for category in classify_schema_mismatches("not-json", schema)) == (
        "non_json",
    )
    assert tuple(category.value for category in classify_schema_mismatches("{}", schema)) == (
        "envelope_fields",
    )
    assert tuple(
        category.value
        for category in classify_schema_mismatches('{"stage":"extract","checkpoint":{}}', schema)
    ) == ("checkpoint_shape",)
