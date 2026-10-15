"""Controlled local Ollama Agent evaluation without a live-model CI call."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from lifecase.evaluation.ollama_agent import (
    OllamaAgentEvaluationConfig,
    evaluate_ollama_agent,
    render_safe_report,
)
from lifecase.providers import (
    ModelProviderKind,
    ProviderCallMetadata,
    ProviderUsage,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    canonical_json_object,
)


def test_controlled_ollama_agent_evaluation_uses_temporary_synthetic_cases() -> None:
    report = asyncio.run(
        evaluate_ollama_agent(
            OllamaAgentEvaluationConfig(
                model_id="qwen2.5:3b",
                runs=2,
                per_run_seconds=30.0,
            ),
            provider=_DeterministicOllamaEvaluationProvider(),
        )
    )

    safe = report.to_safe_dict()
    assert report.overall_pass is True
    assert safe["provider"] == "ollama"
    assert safe["runs"] == 2
    assert safe["answered_runs"] == 1
    assert safe["unknown_runs"] == 1
    assert safe["seven_stage_runs"] == 2
    assert safe["recovered_runs"] == 2
    assert safe["claims_correct"] == 12
    assert safe["field_disposition_accuracy"] == 1.0
    assert safe["citations_valid"] == safe["citations_checked"]
    assert safe["unsupported_claims"] == 0
    assert safe["answer_correct_runs"] == 2
    assert safe["provider_calls"] == 14
    assert safe["input_tokens"] == 1_400
    assert safe["output_tokens"] == 700
    assert safe["schema_diagnostic_categories"] == []
    assert safe["schema_diagnostic_runs"] == {}
    assert safe["business_failure_codes"] == []
    assert safe["business_failure_runs"] == {}
    assert safe["failure_stages"] == []
    rendered = render_safe_report(report)
    assert "synthetic-warranty" not in rendered
    assert "lifecase.sqlite3" not in rendered
    assert "case_" not in rendered


def test_controlled_evaluation_records_schema_failure_and_continues() -> None:
    report = asyncio.run(
        evaluate_ollama_agent(
            OllamaAgentEvaluationConfig(
                model_id="qwen2.5:3b",
                runs=2,
                per_run_seconds=30.0,
            ),
            provider=_SchemaInvalidOllamaProvider(),
        )
    )

    safe = report.to_safe_dict()
    assert report.overall_pass is False
    assert safe["failed_runs"] == 2
    assert safe["failure_codes"] == ["agent_schema_invalid"]
    assert safe["failure_stages"] == ["extract"]
    assert safe["provider_calls"] == 4
    assert safe["seven_stage_runs"] == 0
    assert safe["recovered_runs"] == 2
    assert safe["schema_diagnostic_categories"] == ["checkpoint_shape"]
    assert safe["schema_diagnostic_runs"] == {"checkpoint_shape": 2}


def test_controlled_evaluation_classifies_schema_valid_business_failure() -> None:
    report = asyncio.run(
        evaluate_ollama_agent(
            OllamaAgentEvaluationConfig(
                model_id="qwen2.5:3b",
                runs=2,
                per_run_seconds=30.0,
            ),
            provider=_BusinessInvalidOllamaProvider(),
        )
    )

    safe = report.to_safe_dict()
    assert report.overall_pass is False
    assert safe["failure_codes"] == ["agent_schema_invalid"]
    assert safe["schema_diagnostic_categories"] == ["business_validation"]
    assert safe["schema_diagnostic_runs"] == {"business_validation": 2}
    assert safe["business_failure_codes"] == ["extract.merchant_or_manufacturer.origin_contract"]
    assert safe["business_failure_runs"] == {"extract.merchant_or_manufacturer.origin_contract": 2}
    rendered = render_safe_report(report)
    for forbidden in (
        "Alpha Store",
        "Merchant Alpha",
        "ORD-001",
        "source_id",
        "quote_hash",
        "lifecase.sqlite3",
        "case_",
    ):
        assert forbidden not in rendered


def test_controlled_evaluation_normalizes_untrusted_quote_metadata() -> None:
    report = asyncio.run(
        evaluate_ollama_agent(
            OllamaAgentEvaluationConfig(
                model_id="qwen2.5:3b",
                runs=2,
                per_run_seconds=30.0,
            ),
            provider=_QuoteAuthorityInvalidOllamaProvider(),
        )
    )

    safe = report.to_safe_dict()
    assert report.overall_pass is True
    assert safe["business_failure_codes"] == []
    assert safe["business_failure_runs"] == {}


def test_controlled_evaluation_reports_field_level_source_authority_failure() -> None:
    report = asyncio.run(
        evaluate_ollama_agent(
            OllamaAgentEvaluationConfig(
                model_id="qwen2.5:3b",
                runs=2,
                per_run_seconds=30.0,
            ),
            provider=_SourceAuthorityInvalidOllamaProvider(),
        )
    )

    safe = report.to_safe_dict()
    assert report.overall_pass is False
    assert safe["business_failure_codes"] == [
        "extract.merchant_or_manufacturer.source_ref_authority"
    ]
    assert safe["business_failure_runs"] == {
        "extract.merchant_or_manufacturer.source_ref_authority": 2
    }


class _DeterministicOllamaEvaluationProvider:
    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        schema = json.loads(request.output_schema_json)
        stage = str(schema["properties"]["stage"]["const"])
        stage_version = str(schema["properties"]["stage_version"]["const"])
        checkpoint_schema_version = str(schema["properties"]["checkpoint_schema_version"]["const"])
        stage_input = json.loads(request.input_json)
        if stage == "extract":
            checkpoint = _extraction_checkpoint(schema, stage_input)
        elif stage == "validate":
            checkpoint = _answer_checkpoint(schema, stage_input)
        else:
            checkpoint = {}
        output_json = canonical_json_object(
            {
                "stage": stage,
                "stage_version": stage_version,
                "checkpoint_schema_version": checkpoint_schema_version,
                "checkpoint": checkpoint,
            },
            maximum_bytes=65_536,
        )
        return StructuredGenerationResult(
            request_id=request.request_id,
            output_json=output_json,
            metadata=ProviderCallMetadata(
                provider=ModelProviderKind.OLLAMA,
                model_id="qwen2.5:3b",
                request_scope_sha256=request.request_scope_sha256,
                latency_ms=1,
                usage=ProviderUsage(
                    input_tokens=100,
                    output_tokens=50,
                    total_tokens=150,
                ),
            ),
        )


class _SchemaInvalidOllamaProvider:
    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        schema = json.loads(request.output_schema_json)
        output_json = canonical_json_object(
            {
                "stage": schema["properties"]["stage"]["const"],
                "stage_version": schema["properties"]["stage_version"]["const"],
                "checkpoint_schema_version": schema["properties"]["checkpoint_schema_version"][
                    "const"
                ],
                "checkpoint": {},
            },
            maximum_bytes=65_536,
        )
        return StructuredGenerationResult(
            request_id=request.request_id,
            output_json=output_json,
            metadata=ProviderCallMetadata(
                provider=ModelProviderKind.OLLAMA,
                model_id="qwen2.5:3b",
                request_scope_sha256=request.request_scope_sha256,
                latency_ms=1,
                usage=ProviderUsage(
                    input_tokens=100,
                    output_tokens=50,
                    total_tokens=150,
                ),
            ),
        )


class _BusinessInvalidOllamaProvider:
    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        result = await _DeterministicOllamaEvaluationProvider().generate_structured(request)
        output = json.loads(result.output_json)
        schema = json.loads(request.output_schema_json)
        if schema["properties"]["stage"]["const"] == "extract":
            output["checkpoint"]["facts"][0]["origin"] = "unknown"
            output["checkpoint"]["facts"][0]["source_refs"][0]["quote_hash"] = "0" * 64
        return StructuredGenerationResult(
            request_id=result.request_id,
            output_json=canonical_json_object(output, maximum_bytes=65_536),
            metadata=result.metadata,
        )


class _QuoteAuthorityInvalidOllamaProvider:
    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        result = await _DeterministicOllamaEvaluationProvider().generate_structured(request)
        output = json.loads(result.output_json)
        schema = json.loads(request.output_schema_json)
        if schema["properties"]["stage"]["const"] == "extract":
            output["checkpoint"]["facts"][0]["source_refs"][0]["quote_hash"] = "0" * 64
        return StructuredGenerationResult(
            request_id=result.request_id,
            output_json=canonical_json_object(output, maximum_bytes=65_536),
            metadata=result.metadata,
        )


class _SourceAuthorityInvalidOllamaProvider:
    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        result = await _DeterministicOllamaEvaluationProvider().generate_structured(request)
        output = json.loads(result.output_json)
        schema = json.loads(request.output_schema_json)
        if schema["properties"]["stage"]["const"] == "extract":
            output["checkpoint"]["facts"][0]["source_refs"][0]["segment_text_sha256"] = "0" * 64
        return StructuredGenerationResult(
            request_id=result.request_id,
            output_json=canonical_json_object(output, maximum_bytes=65_536),
            metadata=result.metadata,
        )


def _extraction_checkpoint(
    schema: dict[str, Any],
    stage_input: dict[str, Any],
) -> dict[str, object]:
    checkpoint_schema = schema["properties"]["checkpoint"]
    schema_version = checkpoint_schema["properties"]["schema_version"]["const"]
    evidence = stage_input["stage_payload"]["evidence"][0]
    text = str(evidence["text"])
    facts: list[dict[str, object]] = []
    values = (
        ("merchant_or_manufacturer", "Merchant Alpha Store", "Alpha Store", "extracted", None),
        ("product_name", "Product SynthPhone", "SynthPhone", "extracted", None),
        ("purchase_date", "Purchase date 2026-07-14", "2026-07-14", "extracted", None),
        ("order_number", "Order ORD-001", "ORD-001", "extracted", None),
        (
            "amount",
            "CNY 259.00",
            {"amount_minor": 25_900, "currency": "CNY"},
            "extracted",
            None,
        ),
        (
            "serial_number",
            "Serial prefix SYN",
            "SYN",
            "inferred",
            "The cited material identifies only a serial prefix.",
        ),
    )
    for field, quote, value, origin, uncertainty_reason in values:
        start = text.index(quote)
        facts.append(
            {
                "field": field,
                "origin": origin,
                "value": value,
                "confidence": 1.0,
                "uncertainty_reason": uncertainty_reason,
                "source_refs": [
                    {
                        "source_id": evidence["source_id"],
                        "segment_id": evidence["segment_id"],
                        "segment_text_sha256": evidence["text_sha256"],
                        "quote_start": start,
                        "quote_end": start + len(quote),
                        "quote_hash": hashlib.sha256(quote.encode()).hexdigest(),
                    }
                ],
            }
        )
    return {"schema_version": schema_version, "facts": facts}


def _answer_checkpoint(
    schema: dict[str, Any],
    stage_input: dict[str, Any],
) -> dict[str, object]:
    checkpoint_schema = schema["properties"]["checkpoint"]
    schema_version = checkpoint_schema["properties"]["schema_version"]["const"]
    tool_result = stage_input["stage_payload"]["tool_result"]
    items = tool_result["context"]["items"]
    tool = {
        "name": "evidence_context.search.v1",
        "call_count": 1,
        "context_sha256": tool_result["context_sha256"],
    }
    if not items:
        return {
            "schema_version": schema_version,
            "status": "unknown",
            "statements": [],
            "uncertainty_reason": "No authoritative Case evidence matched the Case goal.",
            "tool": tool,
        }
    item = items[0]
    text = str(item["text"])
    return {
        "schema_version": schema_version,
        "status": "answered",
        "statements": [
            {
                "text": "The warranty marker is present in the supplied evidence.",
                "source_refs": [
                    {
                        **item["citation"],
                        "quote_start": 0,
                        "quote_end": len(text),
                        "quote_hash": hashlib.sha256(text.encode()).hexdigest(),
                    }
                ],
            }
        ],
        "uncertainty_reason": None,
        "tool": tool,
    }
