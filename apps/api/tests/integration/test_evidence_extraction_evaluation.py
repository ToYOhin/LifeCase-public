"""Fixed five-case synthetic evaluation for M3-03 extraction semantics."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from lifecase.agent import (
    EVIDENCE_EXTRACTION_STAGE_REGISTRY,
    WARRANTY_EXTRACTION_SCHEMA_VERSION,
    AgentStageName,
    CallReservation,
    CreateAgentRunCommand,
    ExecuteStageCommand,
    ExtractionEvidenceSegment,
    ExtractionOrigin,
    StageBudget,
    StageRunner,
    WarrantyExtractionCheckpointHandler,
    WarrantyExtractionField,
    warranty_extraction_stage_input,
)
from lifecase.cases import CaseService
from lifecase.claims import ClaimService
from lifecase.providers import (
    ModelProviderKind,
    ProviderCallMetadata,
    ProviderUsage,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)
from lifecase.storage import apply_migrations, connect_database

_FIELD_ACCURACY_MINIMUM = 0.90
_CITATION_VALIDITY_MINIMUM = 1.00
_UNSUPPORTED_FACT_RATE_MAXIMUM = 0.00
_UNKNOWN_HANDLING_MINIMUM = 1.00
_NOW = "2026-08-02T00:00:00.000000Z"


@dataclass(frozen=True, slots=True)
class _ExpectedFact:
    origin: ExtractionOrigin
    value: object | None
    quote: str | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _Fixture:
    fixture_id: str
    text: str
    facts: dict[WarrantyExtractionField, _ExpectedFact]


@dataclass(frozen=True, slots=True)
class _Metrics:
    field_accuracy: float
    citation_validity: float
    unsupported_fact_rate: float
    unknown_handling: float


class _Provider:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        return StructuredGenerationResult(
            request_id=request.request_id,
            output_json=json.dumps(self.output),
            metadata=ProviderCallMetadata(
                provider=ModelProviderKind.FAKE,
                model_id="lifecase-fake-evaluation-v1",
                request_scope_sha256=request.request_scope_sha256,
                latency_ms=1,
                usage=ProviderUsage(
                    input_tokens=20,
                    output_tokens=10,
                    total_tokens=30,
                    cost_minor_units=1,
                    cost_currency="CNY",
                ),
            ),
        )


def test_five_synthetic_warranty_cases_meet_documented_thresholds(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection = connect_database(database_path)
    apply_migrations(connection)
    records: list[tuple[str, str, _Fixture]] = []
    try:
        for fixture in _fixtures():
            case = CaseService(connection).create_case(
                title=f"Synthetic extraction {fixture.fixture_id}",
                goal="Evaluate fixed warranty extraction semantics",
                template_id="warranty",
            )
            evidence = _insert_evidence(connection, case.id, fixture)
            output = _output(evidence, fixture)
            provider = _Provider(output)
            run_id = f"run-{fixture.fixture_id}"
            runner = StageRunner(
                connection,
                provider,
                registry=EVIDENCE_EXTRACTION_STAGE_REGISTRY,
                checkpoint_handler=WarrantyExtractionCheckpointHandler(),
            )
            run = runner.create_run(CreateAgentRunCommand(run_id=run_id, case_id=case.id))
            asyncio.run(
                runner.execute_stage(
                    ExecuteStageCommand(
                        run_id=run_id,
                        expected_run_version=run.version,
                        expected_stage_version=None,
                        stage_input=warranty_extraction_stage_input(case.id, (evidence,)),
                        stage_budget=StageBudget(),
                        reservation=CallReservation(100, 50, 1),
                    )
                )
            )
            records.append((case.id, run_id, fixture))

        metrics = _evaluate(connection, records)
        assert metrics.field_accuracy >= _FIELD_ACCURACY_MINIMUM
        assert metrics.citation_validity >= _CITATION_VALIDITY_MINIMUM
        assert metrics.unsupported_fact_rate <= _UNSUPPORTED_FACT_RATE_MAXIMUM
        assert metrics.unknown_handling >= _UNKNOWN_HANDLING_MINIMUM
        assert metrics == _Metrics(1.0, 1.0, 0.0, 1.0)
    finally:
        connection.close()

    restarted = connect_database(database_path)
    try:
        assert _evaluate(restarted, records) == metrics
    finally:
        restarted.close()


def _evaluate(
    connection: sqlite3.Connection,
    records: list[tuple[str, str, _Fixture]],
) -> _Metrics:
    total_fields = 0
    correct_fields = 0
    factual_fields = 0
    valid_citations = 0
    unsupported_facts = 0
    expected_unknown = 0
    correct_unknown = 0
    placeholder = _Provider({})
    claims = ClaimService(connection)
    for case_id, run_id, fixture in records:
        runner = StageRunner(
            connection,
            placeholder,
            registry=EVIDENCE_EXTRACTION_STAGE_REGISTRY,
            checkpoint_handler=WarrantyExtractionCheckpointHandler(),
        )
        run = runner.get_run(case_id, run_id)
        checkpoint = run.stages[0].checkpoint
        assert checkpoint is not None
        observed = {item["field"]: item for item in json.loads(checkpoint.payload_json)["facts"]}
        case_claims = {item.field: item for item in claims.list_case_claims(case_id)}
        for field, expected in fixture.facts.items():
            total_fields += 1
            item = observed[field.value]
            if item["origin"] == expected.origin.value and item["value"] == expected.value:
                correct_fields += 1
            if expected.origin is ExtractionOrigin.UNKNOWN:
                expected_unknown += 1
                if (
                    item["value"] is None
                    and item["source_refs"] == []
                    and field.value not in case_claims
                ):
                    correct_unknown += 1
                continue
            factual_fields += 1
            claim = case_claims[field.value]
            sources = claims.list_claim_sources(claim.id)
            if sources:
                valid_citations += 1
            else:
                unsupported_facts += 1
    return _Metrics(
        field_accuracy=correct_fields / total_fields,
        citation_validity=valid_citations / factual_fields,
        unsupported_fact_rate=unsupported_facts / factual_fields,
        unknown_handling=correct_unknown / expected_unknown,
    )


def _insert_evidence(
    connection: sqlite3.Connection,
    case_id: str,
    fixture: _Fixture,
) -> ExtractionEvidenceSegment:
    source_id = f"source-{fixture.fixture_id}"
    segment_id = f"segment-{fixture.fixture_id}"
    text_sha256 = hashlib.sha256(fixture.text.encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO source (
            id, case_id, original_name, media_type, sha256,
            size_bytes, object_path, imported_at
        ) VALUES (?, ?, 'synthetic.txt', 'text/plain', ?, 1, ?, ?)
        """,
        (source_id, case_id, "0" * 64, f"objects/00/00/{'0' * 64}", _NOW),
    )
    connection.execute(
        """
        INSERT INTO source_segment (
            id, source_id, sequence, page, bbox_json, text,
            extraction_method, confidence, text_sha256, extractor_version
        ) VALUES (?, ?, 0, 1, NULL, ?, 'direct', 1.0, ?, 'synthetic.v1')
        """,
        (segment_id, source_id, fixture.text, text_sha256),
    )
    return ExtractionEvidenceSegment(
        case_id=case_id,
        source_id=source_id,
        segment_id=segment_id,
        sequence=0,
        page=1,
        text=fixture.text,
        text_sha256=text_sha256,
    )


def _output(
    evidence: ExtractionEvidenceSegment,
    fixture: _Fixture,
) -> dict[str, object]:
    facts: list[dict[str, object]] = []
    for field in WarrantyExtractionField:
        expected = fixture.facts[field]
        source_refs: list[dict[str, object]] = []
        if expected.quote is not None:
            start = evidence.text.index(expected.quote)
            end = start + len(expected.quote)
            source_refs.append(
                {
                    "source_id": evidence.source_id,
                    "segment_id": evidence.segment_id,
                    "segment_text_sha256": evidence.text_sha256,
                    "quote_start": start,
                    "quote_end": end,
                    "quote_hash": hashlib.sha256(expected.quote.encode()).hexdigest(),
                }
            )
        facts.append(
            {
                "field": field.value,
                "origin": expected.origin.value,
                "value": expected.value,
                "confidence": (None if expected.origin is ExtractionOrigin.UNKNOWN else 0.9),
                "uncertainty_reason": expected.reason,
                "source_refs": source_refs,
            }
        )
    definition = EVIDENCE_EXTRACTION_STAGE_REGISTRY.get(AgentStageName.EXTRACT)
    return {
        "stage": "extract",
        "stage_version": definition.stage_version,
        "checkpoint_schema_version": definition.checkpoint_schema_version,
        "checkpoint": {
            "schema_version": WARRANTY_EXTRACTION_SCHEMA_VERSION,
            "facts": facts,
        },
    }


def _direct(value: object, quote: str) -> _ExpectedFact:
    return _ExpectedFact(ExtractionOrigin.EXTRACTED, value, quote)


def _unknown(reason: str) -> _ExpectedFact:
    return _ExpectedFact(ExtractionOrigin.UNKNOWN, None, None, reason)


def _inferred(value: object, quote: str, reason: str) -> _ExpectedFact:
    return _ExpectedFact(ExtractionOrigin.INFERRED, value, quote, reason)


def _fixtures() -> tuple[_Fixture, ...]:
    fields = WarrantyExtractionField
    return (
        _Fixture(
            "zh-complete",
            "商家：星河合成商店；商品：合成咖啡机；购买日期：2026-07-01；订单号：CN-001；"
            "金额：CNY 399.00；序列号：SN-CN-01。",
            {
                fields.MERCHANT_OR_MANUFACTURER: _direct("星河合成商店", "星河合成商店"),
                fields.PRODUCT_NAME: _direct("合成咖啡机", "合成咖啡机"),
                fields.PURCHASE_DATE: _direct("2026-07-01", "2026-07-01"),
                fields.ORDER_NUMBER: _direct("CN-001", "CN-001"),
                fields.AMOUNT: _direct({"amount_minor": 39900, "currency": "CNY"}, "CNY 399.00"),
                fields.SERIAL_NUMBER: _direct("SN-CN-01", "SN-CN-01"),
            },
        ),
        _Fixture(
            "en-sparse",
            "Synthetic Outlet sold a Model Pine item under order EN-002.",
            {
                fields.MERCHANT_OR_MANUFACTURER: _direct("Synthetic Outlet", "Synthetic Outlet"),
                fields.PRODUCT_NAME: _direct("Model Pine", "Model Pine"),
                fields.PURCHASE_DATE: _unknown("No purchase date is present"),
                fields.ORDER_NUMBER: _direct("EN-002", "EN-002"),
                fields.AMOUNT: _unknown("No paid amount is present"),
                fields.SERIAL_NUMBER: _unknown("No warranty identifier is present"),
            },
        ),
        _Fixture(
            "zh-inferred",
            "制造商：远山合成厂；商品：测试风扇；购买日：2026-06-12；支付 CNY 88.00；"
            "标签仅显示序列前缀 ZX，其余不可见。",
            {
                fields.MERCHANT_OR_MANUFACTURER: _direct("远山合成厂", "远山合成厂"),
                fields.PRODUCT_NAME: _direct("测试风扇", "测试风扇"),
                fields.PURCHASE_DATE: _direct("2026-06-12", "2026-06-12"),
                fields.ORDER_NUMBER: _unknown("No order number is present"),
                fields.AMOUNT: _direct({"amount_minor": 8800, "currency": "CNY"}, "CNY 88.00"),
                fields.SERIAL_NUMBER: _inferred(
                    "ZX-prefix",
                    "序列前缀 ZX",
                    "Only the serial prefix is visible",
                ),
            },
        ),
        _Fixture(
            "en-receipt",
            "Receipt: North Synthetic, Item Lamp Q, 2026-05-09, order EN-004, total USD 45.50.",
            {
                fields.MERCHANT_OR_MANUFACTURER: _direct("North Synthetic", "North Synthetic"),
                fields.PRODUCT_NAME: _direct("Lamp Q", "Lamp Q"),
                fields.PURCHASE_DATE: _direct("2026-05-09", "2026-05-09"),
                fields.ORDER_NUMBER: _direct("EN-004", "EN-004"),
                fields.AMOUNT: _direct({"amount_minor": 4550, "currency": "USD"}, "USD 45.50"),
                fields.SERIAL_NUMBER: _unknown("No serial number is present"),
            },
        ),
        _Fixture(
            "mixed-minimal",
            "产品：合成耳机 R；订单 MIX-005；商户 Test Harbor；金额 CNY 120.00。",
            {
                fields.MERCHANT_OR_MANUFACTURER: _direct("Test Harbor", "Test Harbor"),
                fields.PRODUCT_NAME: _direct("合成耳机 R", "合成耳机 R"),
                fields.PURCHASE_DATE: _unknown("No purchase date is present"),
                fields.ORDER_NUMBER: _direct("MIX-005", "MIX-005"),
                fields.AMOUNT: _direct({"amount_minor": 12000, "currency": "CNY"}, "CNY 120.00"),
                fields.SERIAL_NUMBER: _unknown("No warranty identifier is present"),
            },
        ),
    )
