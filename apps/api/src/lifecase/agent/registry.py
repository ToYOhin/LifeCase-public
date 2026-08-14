"""Versioned M3-02 stage registry with no implicit tool permissions."""

from __future__ import annotations

import json
from dataclasses import dataclass

from lifecase.agent.errors import AgentValidationError
from lifecase.agent.models import (
    STAGE_ORDER,
    AgentCheckpoint,
    AgentStageName,
    StageStopCondition,
    checkpoint_payload,
)
from lifecase.providers.models import ModelProviderKind, canonical_json_object


@dataclass(frozen=True, slots=True)
class StageDefinition:
    """Reviewed configuration for exactly one orchestration stage."""

    stage: AgentStageName
    stage_version: str
    input_schema_version: str
    output_schema_version: str
    checkpoint_schema_version: str
    input_schema_name: str
    input_schema_json: str
    output_schema_name: str
    output_schema_json: str
    system_instruction: str
    stage_instruction: str
    allowed_tools: tuple[str, ...]
    allowed_providers: tuple[ModelProviderKind, ...]
    timeout_seconds: float
    max_repair_attempts: int
    stop_conditions: tuple[StageStopCondition, ...]


class StageRegistry:
    """Immutable fixed-order stage lookup."""

    def __init__(self, definitions: tuple[StageDefinition, ...]) -> None:
        if tuple(definition.stage for definition in definitions) != STAGE_ORDER:
            raise AgentValidationError("Agent stage registry is invalid")
        self._definitions = definitions
        self._by_name = {definition.stage: definition for definition in definitions}

    @property
    def definitions(self) -> tuple[StageDefinition, ...]:
        return self._definitions

    def get(self, stage: AgentStageName) -> StageDefinition:
        try:
            return self._by_name[stage]
        except (KeyError, TypeError) as error:
            raise AgentValidationError("Agent stage is invalid") from error


def validate_stage_output(
    definition: StageDefinition,
    output_json: str,
    *,
    input_scope_sha256: str,
) -> AgentCheckpoint:
    """Convert an exact provider envelope into bounded checkpoint state."""

    invalid = AgentValidationError("Agent stage output is invalid")
    try:
        value = json.loads(output_json)
    except (json.JSONDecodeError, TypeError, UnicodeError) as error:
        raise invalid from error
    if not isinstance(value, dict) or set(value) != {
        "stage",
        "stage_version",
        "checkpoint_schema_version",
        "checkpoint",
    }:
        raise invalid
    if (
        value["stage"] != definition.stage.value
        or value["stage_version"] != definition.stage_version
        or value["checkpoint_schema_version"] != definition.checkpoint_schema_version
    ):
        raise invalid
    payload_json, payload_sha256 = checkpoint_payload(value["checkpoint"])
    return AgentCheckpoint(
        stage=definition.stage,
        stage_version=definition.stage_version,
        schema_version=definition.checkpoint_schema_version,
        input_scope_sha256=input_scope_sha256,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
    )


def _definition(stage: AgentStageName) -> StageDefinition:
    stage_version = f"{stage.value}.v1"
    checkpoint_version = f"{stage.value}.checkpoint.v1"
    input_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["case_id", "prior_checkpoint_hashes", "stage_payload"],
        "properties": {
            "case_id": {"type": "string", "minLength": 1, "maxLength": 100},
            "prior_checkpoint_hashes": {
                "type": "array",
                "maxItems": 7,
                "items": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
            "stage_payload": {"type": "object"},
        },
    }
    output_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "stage",
            "stage_version",
            "checkpoint_schema_version",
            "checkpoint",
        ],
        "properties": {
            "stage": {"const": stage.value},
            "stage_version": {"const": stage_version},
            "checkpoint_schema_version": {"const": checkpoint_version},
            "checkpoint": {"type": "object"},
        },
    }
    return StageDefinition(
        stage=stage,
        stage_version=stage_version,
        input_schema_version=f"{stage.value}.input.v1",
        output_schema_version=f"{stage.value}.output.v1",
        checkpoint_schema_version=checkpoint_version,
        input_schema_name=f"lifecase_{stage.value}_input_v1",
        input_schema_json=canonical_json_object(input_schema, maximum_bytes=20_000),
        output_schema_name=f"lifecase_{stage.value}_checkpoint_v1",
        output_schema_json=canonical_json_object(output_schema, maximum_bytes=20_000),
        system_instruction=(
            "Return only the requested JSON object. Do not invent evidence, credentials, "
            "tool results, or user confirmation."
        ),
        stage_instruction=(
            f"Produce the bounded {stage.value} stage checkpoint using only the supplied input."
        ),
        allowed_tools=(),
        allowed_providers=tuple(ModelProviderKind),
        timeout_seconds=30.0,
        max_repair_attempts=1,
        stop_conditions=tuple(StageStopCondition),
    )


BUILT_IN_STAGE_REGISTRY = StageRegistry(tuple(_definition(stage) for stage in STAGE_ORDER))
