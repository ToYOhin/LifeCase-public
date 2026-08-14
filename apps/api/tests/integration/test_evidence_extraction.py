"""M3-03 evidence-backed warranty extraction integration coverage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from lifecase.agent import (
    EVIDENCE_EXTRACTION_STAGE_REGISTRY,
    WARRANTY_EXTRACTION_SCHEMA_VERSION,
    AgentPersistenceError,
    AgentRunStatus,
    AgentStageName,
    AgentStageSchemaError,
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
from lifecase.claims import (
    ClaimCreatedBy,
    ClaimService,
    ClaimSourceRelation,
    ClaimStatus,
    ClaimValue,
    CreateClaimCommand,
)
from lifecase.providers import (
    ModelProviderKind,
    ProviderCallMetadata,
    ProviderUsage,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)
from lifecase.storage import apply_migrations, connect_database

_NOW = "2026-08-02T00:00:00.000000Z"


class _OutputProvider:
    def __init__(self, outputs: tuple[dict[str, object], ...]) -> None:
        self.outputs = outputs
        self.requests: list[StructuredGenerationRequest] = []

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        self.requests.append(request)
        output = self.outputs[len(self.requests) - 1]
        return StructuredGenerationResult(
            request_id=request.request_id,
            output_json=json.dumps(output),
            metadata=ProviderCallMetadata(
                provider=ModelProviderKind.FAKE,
                model_id="lifecase-fake-extraction-v1",
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


def test_warranty_extraction_prompt_exposes_the_full_business_contract() -> None:
    instruction = EVIDENCE_EXTRACTION_STAGE_REGISTRY.get(AgentStageName.EXTRACT).stage_instruction

    for required in (
        "exactly one fact for each of these six fields",
        "origin=unknown",
        "value=null, confidence=null",
        "quote_start=0",
        "quote_end to the number of Unicode characters in evidence.text",
        "quote_hash to that evidence.text_sha256",
        "Do not generate or calculate a SHA-256 value",
        "serial prefix is inferred",
    ):
        assert required in instruction


def test_extraction_contract_keeps_extracted_out_of_claim_states() -> None:
    assert {field.value for field in WarrantyExtractionField} == {
        "merchant_or_manufacturer",
        "product_name",
        "purchase_date",
        "order_number",
        "amount",
        "serial_number",
    }
    assert {origin.value for origin in ExtractionOrigin} == {
        "extracted",
        "inferred",
        "unknown",
    }
    assert "extracted" not in {status.value for status in ClaimStatus}

    definition = EVIDENCE_EXTRACTION_STAGE_REGISTRY.get(AgentStageName.EXTRACT)
    assert definition.stage_version == "extract.warranty.v1"
    assert definition.checkpoint_schema_version == "extract.warranty.checkpoint.v1"
    output_schema = json.loads(definition.output_schema_json)
    checkpoint = output_schema["properties"]["checkpoint"]
    assert checkpoint["additionalProperties"] is False
    assert checkpoint["properties"]["facts"]["minItems"] == 6
    assert definition.allowed_tools == ()


def test_extracted_inferred_and_unknown_map_to_authoritative_claim_semantics(
    tmp_path: Path,
) -> None:
    connection, case_id, evidence = _database_with_evidence(tmp_path)
    output = _valid_output(
        evidence,
        overrides={
            WarrantyExtractionField.SERIAL_NUMBER: _fact(
                WarrantyExtractionField.SERIAL_NUMBER,
                ExtractionOrigin.INFERRED,
                "SYN-prefix",
                evidence,
                quote="serial prefix SYN",
                confidence=0.55,
                uncertainty_reason=(
                    "Only a package prefix is visible; the complete serial is absent"
                ),
            ),
            WarrantyExtractionField.PURCHASE_DATE: _unknown_fact(
                WarrantyExtractionField.PURCHASE_DATE,
                "The synthetic evidence does not include a purchase date",
            ),
        },
    )
    provider = _OutputProvider((output,))
    try:
        run = _execute(connection, case_id, evidence, provider)
        claims = ClaimService(connection).list_case_claims(case_id)

        assert run.status is AgentRunStatus.CHECKPOINTED
        assert run.usage.calls == 1
        assert len(claims) == 5
        assert all(claim.created_by is ClaimCreatedBy.AGENT for claim in claims)
        assert all(claim.status is not ClaimStatus.CONFIRMED for claim in claims)
        by_field = {claim.field: claim for claim in claims}
        assert by_field["merchant_or_manufacturer"].status is ClaimStatus.CANDIDATE
        assert by_field["serial_number"].status is ClaimStatus.INFERRED
        assert "purchase_date" not in by_field

        direct_source = ClaimService(connection).list_claim_sources(
            by_field["merchant_or_manufacturer"].id
        )[0]
        inferred_source = ClaimService(connection).list_claim_sources(by_field["serial_number"].id)[
            0
        ]
        assert direct_source.relation is ClaimSourceRelation.SUPPORTS
        assert inferred_source.relation is ClaimSourceRelation.CONTEXT
        assert direct_source.case_id == case_id
        assert direct_source.source_id == evidence.source_id
        assert direct_source.segment_id == evidence.segment_id
        assert direct_source.segment_text_sha256 == evidence.text_sha256
        assert direct_source.quote_start == evidence.text.index("Alpha Store")
        assert direct_source.quote_end == direct_source.quote_start + len("Alpha Store")

        checkpoint = run.stages[0].checkpoint
        assert checkpoint is not None
        persisted = json.loads(checkpoint.payload_json)
        assert persisted["schema_version"] == WARRANTY_EXTRACTION_SCHEMA_VERSION
        origins = {item["field"]: item["origin"] for item in persisted["facts"]}
        assert origins["merchant_or_manufacturer"] == "extracted"
        assert origins["serial_number"] == "inferred"
        assert origins["purchase_date"] == "unknown"
    finally:
        connection.close()


def test_invalid_citation_gets_one_repair_then_commits(tmp_path: Path) -> None:
    connection, case_id, evidence = _database_with_evidence(tmp_path)
    invalid = _valid_output(evidence)
    invalid_ref = invalid["checkpoint"]["facts"][0]["source_refs"][0]
    invalid_ref["segment_text_sha256"] = "f" * 64
    repaired = _valid_output(evidence)
    provider = _OutputProvider((invalid, repaired))
    try:
        run = _execute(connection, case_id, evidence, provider)

        assert len(provider.requests) == 2
        assert run.usage.calls == 2
        assert run.stages[0].repair_count == 1
        assert len(ClaimService(connection).list_case_claims(case_id)) == 5
    finally:
        connection.close()


def test_model_quote_metadata_is_normalized_to_the_authoritative_full_segment(
    tmp_path: Path,
) -> None:
    connection, case_id, evidence = _database_with_evidence(tmp_path)
    output = _valid_output(evidence)
    proposed_ref = output["checkpoint"]["facts"][0]["source_refs"][0]
    proposed_ref["quote_start"] = 1
    proposed_ref["quote_end"] = 2
    proposed_ref["quote_hash"] = "0" * 64
    provider = _OutputProvider((output,))
    try:
        run = _execute(connection, case_id, evidence, provider)
        claim = next(
            item
            for item in ClaimService(connection).list_case_claims(case_id)
            if item.field == "merchant_or_manufacturer"
        )
        source = ClaimService(connection).list_claim_sources(claim.id)[0]
        checkpoint = run.stages[0].checkpoint

        assert run.usage.calls == 1
        assert checkpoint is not None
        assert source.quote_start == 0
        assert source.quote_end == len(evidence.text)
        assert source.quote_hash == evidence.text_sha256
        persisted_ref = json.loads(checkpoint.payload_json)["facts"][0]["source_refs"][0]
        assert persisted_ref == {
            "quote_end": len(evidence.text),
            "quote_hash": evidence.text_sha256,
            "quote_start": 0,
            "segment_id": evidence.segment_id,
            "segment_text_sha256": evidence.text_sha256,
            "source_id": evidence.source_id,
        }
    finally:
        connection.close()


def test_second_invalid_extraction_stops_without_claims_or_checkpoint(tmp_path: Path) -> None:
    connection, case_id, evidence = _database_with_evidence(tmp_path)
    invalid = _valid_output(evidence)
    invalid["checkpoint"]["facts"][0]["source_refs"] = []
    provider = _OutputProvider((invalid, invalid))
    try:
        with pytest.raises(AgentStageSchemaError) as caught:
            _execute(connection, case_id, evidence, provider)

        run = StageRunner(
            connection,
            provider,
            registry=EVIDENCE_EXTRACTION_STAGE_REGISTRY,
            checkpoint_handler=WarrantyExtractionCheckpointHandler(),
        ).get_run(case_id, "run-extract")
        assert caught.value.code.value == "agent_schema_invalid"
        assert run.status is AgentRunStatus.NEEDS_REVIEW
        assert run.stages[0].checkpoint is None
        assert run.usage.calls == 2
        assert connection.execute("SELECT COUNT(*) FROM claim").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "invalid_kind",
    [
        "duplicate_field",
        "illegal_origin",
        "inferred_without_reason",
        "unknown_with_value",
        "invalid_date",
        "invalid_amount",
        "oversized_value",
    ],
)
def test_extraction_business_schema_boundaries_fail_safely(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    connection, case_id, evidence = _database_with_evidence(tmp_path)
    invalid = _valid_output(evidence)
    facts = invalid["checkpoint"]["facts"]
    if invalid_kind == "duplicate_field":
        facts[-1]["field"] = facts[0]["field"]
    elif invalid_kind == "illegal_origin":
        facts[0]["origin"] = "confirmed"
    elif invalid_kind == "inferred_without_reason":
        facts[-1]["origin"] = "inferred"
        facts[-1]["uncertainty_reason"] = None
    elif invalid_kind == "unknown_with_value":
        facts[2]["value"] = "2026-07-01"
    elif invalid_kind == "invalid_date":
        facts[2] = _fact(
            WarrantyExtractionField.PURCHASE_DATE,
            ExtractionOrigin.EXTRACTED,
            "2026-99-01",
            evidence,
            quote="ORD-001",
        )
    elif invalid_kind == "invalid_amount":
        facts[4]["value"] = {"amount_minor": 25900, "currency": "cny"}
    else:
        facts[1]["value"] = "x" * 501
    provider = _OutputProvider((invalid, invalid))
    try:
        with pytest.raises(AgentStageSchemaError) as caught:
            _execute(connection, case_id, evidence, provider)

        assert caught.value.code.value == "agent_schema_invalid"
        assert len(provider.requests) == 2
        assert connection.execute("SELECT COUNT(*) FROM claim").fetchone()[0] == 0
        assert str(tmp_path) not in str(caught.value)
    finally:
        connection.close()


@pytest.mark.parametrize("authority_failure", ["cross_case", "stale_text"])
def test_cross_case_or_stale_authority_fails_closed(
    tmp_path: Path,
    authority_failure: str,
) -> None:
    connection, case_id, evidence = _database_with_evidence(tmp_path)
    if authority_failure == "cross_case":
        other_case = CaseService(connection).create_case(
            title="Other synthetic warranty",
            goal="Prove extraction Case isolation",
            template_id="warranty",
        )
        connection.execute(
            "UPDATE source SET case_id = ? WHERE id = ?",
            (other_case.id, evidence.source_id),
        )
    else:
        connection.execute(
            "UPDATE source_segment SET text = 'stale synthetic text' WHERE id = ?",
            (evidence.segment_id,),
        )
    invalid = _valid_output(evidence)
    provider = _OutputProvider((invalid, invalid))
    try:
        with pytest.raises(AgentStageSchemaError):
            _execute(connection, case_id, evidence, provider)

        assert len(provider.requests) == 2
        assert connection.execute("SELECT COUNT(*) FROM claim").fetchone()[0] == 0
        checkpoint = connection.execute(
            "SELECT checkpoint_json FROM agent_stage_run WHERE run_id = ?",
            ("run-extract",),
        ).fetchone()[0]
        assert checkpoint is None
    finally:
        connection.close()


def test_checkpoint_database_failure_rolls_back_claim_sources_history_and_checkpoint(
    tmp_path: Path,
) -> None:
    connection, case_id, evidence = _database_with_evidence(tmp_path)
    provider = _OutputProvider((_valid_output(evidence),))
    connection.execute(
        """
        CREATE TRIGGER fail_extraction_checkpoint
        BEFORE UPDATE ON agent_stage_run
        WHEN NEW.status = 'checkpointed'
        BEGIN
            SELECT RAISE(ABORT, 'synthetic private checkpoint failure');
        END
        """
    )
    try:
        with pytest.raises(AgentPersistenceError) as caught:
            _execute(connection, case_id, evidence, provider)

        assert "synthetic private checkpoint failure" not in str(caught.value)
        assert str(tmp_path) not in str(caught.value)
        assert connection.execute("SELECT COUNT(*) FROM claim").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM claim_source").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM claim_event").fetchone()[0] == 0
        stage = connection.execute(
            "SELECT status, checkpoint_json FROM agent_stage_run WHERE run_id = ?",
            ("run-extract",),
        ).fetchone()
        assert tuple(stage) == ("running", None)
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_existing_claim_history_is_unchanged_and_contradiction_is_recomputed(
    tmp_path: Path,
) -> None:
    connection, case_id, evidence = _database_with_evidence(tmp_path)
    claims = ClaimService(connection)
    prior = claims.create_claim(
        CreateClaimCommand(
            case_id=case_id,
            field="merchant_or_manufacturer",
            value=ClaimValue.from_input("Different Synthetic Store"),
            status=ClaimStatus.CANDIDATE,
            confidence=0.7,
            created_by=ClaimCreatedBy.USER,
            actor="local_user",
            reason="Synthetic prior candidate",
        )
    )
    prior_events = claims.list_claim_events(prior.id)
    provider = _OutputProvider((_valid_output(evidence),))
    try:
        _execute(connection, case_id, evidence, provider)
        current = claims.get_claim(prior.id)
        extracted = next(
            item
            for item in claims.list_case_claims(case_id)
            if item.id != prior.id and item.field == "merchant_or_manufacturer"
        )

        assert current.contradiction is True
        assert extracted.contradiction is True
        assert claims.list_claim_events(prior.id) == prior_events
        assert claims.list_claim_confirmations(prior.id) == ()
    finally:
        connection.close()


def test_checkpoint_and_claim_provenance_survive_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection, case_id, evidence = _database_with_evidence(tmp_path)
    provider = _OutputProvider((_valid_output(evidence),))
    run = _execute(connection, case_id, evidence, provider)
    claims = ClaimService(connection).list_case_claims(case_id)
    sources = {claim.id: ClaimService(connection).list_claim_sources(claim.id) for claim in claims}
    connection.close()

    restarted = connect_database(database_path)
    try:
        restored_runner = StageRunner(
            restarted,
            provider,
            registry=EVIDENCE_EXTRACTION_STAGE_REGISTRY,
            checkpoint_handler=WarrantyExtractionCheckpointHandler(),
        )
        restored_claims = ClaimService(restarted)
        assert restored_runner.get_run(case_id, "run-extract") == run
        assert restored_claims.list_case_claims(case_id) == claims
        for claim in claims:
            assert restored_claims.list_claim_sources(claim.id) == sources[claim.id]
    finally:
        restarted.close()


def _execute(
    connection: sqlite3.Connection,
    case_id: str,
    evidence: ExtractionEvidenceSegment,
    provider: _OutputProvider,
):
    runner = StageRunner(
        connection,
        provider,
        registry=EVIDENCE_EXTRACTION_STAGE_REGISTRY,
        checkpoint_handler=WarrantyExtractionCheckpointHandler(),
    )
    run = runner.create_run(CreateAgentRunCommand(run_id="run-extract", case_id=case_id))
    stage_input = warranty_extraction_stage_input(case_id, (evidence,))
    return asyncio.run(
        runner.execute_stage(
            ExecuteStageCommand(
                run_id=run.id,
                expected_run_version=run.version,
                expected_stage_version=None,
                stage_input=stage_input,
                stage_budget=StageBudget(),
                reservation=CallReservation(100, 50, 1),
            )
        )
    )


def _database_with_evidence(
    tmp_path: Path,
) -> tuple[sqlite3.Connection, str, ExtractionEvidenceSegment]:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic warranty extraction",
        goal="Evaluate evidence-backed extraction",
        template_id="warranty",
    )
    text = (
        "Merchant Alpha Store. Product SynthPhone. Order ORD-001. "
        "Paid CNY 259.00. Package shows serial prefix SYN."
    )
    text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO source (
            id, case_id, original_name, media_type, sha256,
            size_bytes, object_path, imported_at
        ) VALUES ('source-extract', ?, 'synthetic.txt', 'text/plain', ?, 1, ?, ?)
        """,
        (case.id, "0" * 64, f"objects/00/00/{'0' * 64}", _NOW),
    )
    connection.execute(
        """
        INSERT INTO source_segment (
            id, source_id, sequence, page, bbox_json, text,
            extraction_method, confidence, text_sha256, extractor_version
        ) VALUES ('segment-extract', 'source-extract', 0, 1, NULL, ?,
                  'direct', 1.0, ?, 'synthetic.v1')
        """,
        (text, text_sha256),
    )
    evidence = ExtractionEvidenceSegment(
        case_id=case.id,
        source_id="source-extract",
        segment_id="segment-extract",
        sequence=0,
        page=1,
        text=text,
        text_sha256=text_sha256,
    )
    return connection, case.id, evidence


def _valid_output(
    evidence: ExtractionEvidenceSegment,
    *,
    overrides: dict[WarrantyExtractionField, dict[str, object]] | None = None,
) -> dict[str, object]:
    facts = {
        WarrantyExtractionField.MERCHANT_OR_MANUFACTURER: _fact(
            WarrantyExtractionField.MERCHANT_OR_MANUFACTURER,
            ExtractionOrigin.EXTRACTED,
            "Alpha Store",
            evidence,
            quote="Alpha Store",
        ),
        WarrantyExtractionField.PRODUCT_NAME: _fact(
            WarrantyExtractionField.PRODUCT_NAME,
            ExtractionOrigin.EXTRACTED,
            "SynthPhone",
            evidence,
            quote="SynthPhone",
        ),
        WarrantyExtractionField.PURCHASE_DATE: _unknown_fact(
            WarrantyExtractionField.PURCHASE_DATE,
            "Purchase date is not present in the supplied synthetic evidence",
        ),
        WarrantyExtractionField.ORDER_NUMBER: _fact(
            WarrantyExtractionField.ORDER_NUMBER,
            ExtractionOrigin.EXTRACTED,
            "ORD-001",
            evidence,
            quote="ORD-001",
        ),
        WarrantyExtractionField.AMOUNT: _fact(
            WarrantyExtractionField.AMOUNT,
            ExtractionOrigin.EXTRACTED,
            {"amount_minor": 25900, "currency": "CNY"},
            evidence,
            quote="CNY 259.00",
        ),
        WarrantyExtractionField.SERIAL_NUMBER: _fact(
            WarrantyExtractionField.SERIAL_NUMBER,
            ExtractionOrigin.EXTRACTED,
            "SYN",
            evidence,
            quote="SYN",
        ),
    }
    facts.update(overrides or {})
    definition = EVIDENCE_EXTRACTION_STAGE_REGISTRY.get(AgentStageName.EXTRACT)
    return {
        "stage": "extract",
        "stage_version": definition.stage_version,
        "checkpoint_schema_version": definition.checkpoint_schema_version,
        "checkpoint": {
            "schema_version": WARRANTY_EXTRACTION_SCHEMA_VERSION,
            "facts": [facts[field] for field in WarrantyExtractionField],
        },
    }


def _fact(
    field: WarrantyExtractionField,
    origin: ExtractionOrigin,
    value: object,
    evidence: ExtractionEvidenceSegment,
    *,
    quote: str,
    confidence: float = 0.9,
    uncertainty_reason: str | None = None,
) -> dict[str, object]:
    quote_start = evidence.text.index(quote)
    quote_end = quote_start + len(quote)
    return {
        "field": field.value,
        "origin": origin.value,
        "value": value,
        "confidence": confidence,
        "uncertainty_reason": uncertainty_reason,
        "source_refs": [
            {
                "source_id": evidence.source_id,
                "segment_id": evidence.segment_id,
                "segment_text_sha256": evidence.text_sha256,
                "quote_start": quote_start,
                "quote_end": quote_end,
                "quote_hash": hashlib.sha256(quote.encode()).hexdigest(),
            }
        ],
    }


def _unknown_fact(field: WarrantyExtractionField, reason: str) -> dict[str, object]:
    return {
        "field": field.value,
        "origin": ExtractionOrigin.UNKNOWN.value,
        "value": None,
        "confidence": None,
        "uncertainty_reason": reason,
        "source_refs": [],
    }
