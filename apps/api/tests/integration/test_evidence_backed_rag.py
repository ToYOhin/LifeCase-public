"""M3-04 Evidence-backed RAG integration coverage with synthetic evidence only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from lifecase.agent import (
    EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION,
    EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
    STAGE_ORDER,
    AgentIntegrityError,
    AgentRunStatus,
    AgentStageName,
    AgentStageSchemaError,
    AgentValidationError,
    CallReservation,
    CreateAgentRunCommand,
    EvidenceAnswerStatus,
    EvidenceBackedRagCheckpointHandler,
    ExecuteStageCommand,
    StageBudget,
    StageInput,
    StageRunner,
    evidence_backed_rag_stage_input,
    stage_input_scope_sha256,
    warranty_extraction_stage_input,
)
from lifecase.cases import CaseService
from lifecase.evidence.context import (
    EvidenceContextBudget,
    EvidenceContextRequest,
    EvidencePolicy,
    EvidenceTrustLabel,
)
from lifecase.providers import (
    ModelProviderKind,
    ProviderCallMetadata,
    ProviderUsage,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)
from lifecase.retrieval import RetrievalService
from lifecase.storage import apply_migrations, connect_database
from lifecase.tools import (
    EVIDENCE_CONTEXT_TOOL_NAME,
    EvidenceContextToolRequest,
    ReadOnlyToolBudget,
)

_NOW = "2026-08-02T00:00:00.000000Z"
_PRIOR_COUNT = STAGE_ORDER.index(AgentStageName.VALIDATE)


class _PipelineProvider:
    def __init__(self, *, validate_modes: tuple[str, ...] = ("answered",)) -> None:
        self.validate_modes = list(validate_modes)
        self.requests: list[StructuredGenerationRequest] = []

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        self.requests.append(request)
        stage = request.output_schema()["properties"]["stage"]["const"]
        if stage == AgentStageName.EXTRACT.value:
            checkpoint = _unknown_extraction_checkpoint()
        elif stage == AgentStageName.VALIDATE.value:
            mode = self.validate_modes.pop(0)
            checkpoint = _answer_checkpoint(request, mode)
        else:
            checkpoint = {"synthetic_stage": stage}
        schema = request.output_schema()["properties"]
        output = {
            "stage": stage,
            "stage_version": schema["stage_version"]["const"],
            "checkpoint_schema_version": schema["checkpoint_schema_version"]["const"],
            "checkpoint": checkpoint,
        }
        return StructuredGenerationResult(
            request_id=request.request_id,
            output_json=json.dumps(output),
            metadata=ProviderCallMetadata(
                provider=ModelProviderKind.FAKE,
                model_id="lifecase-fake-rag-v1",
                request_scope_sha256=request.request_scope_sha256,
                latency_ms=1,
                usage=ProviderUsage(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                    cost_minor_units=0,
                    cost_currency="CNY",
                ),
            ),
        )


def test_rag_contract_allows_only_validate_tool_and_requires_full_prior_chain(
    tmp_path: Path,
) -> None:
    connection, case_id, _text = _database_with_evidence(tmp_path)
    try:
        assert all(
            definition.allowed_tools == ()
            for definition in EVIDENCE_BACKED_RAG_STAGE_REGISTRY.definitions[:-1]
        )
        definition = EVIDENCE_BACKED_RAG_STAGE_REGISTRY.get(AgentStageName.VALIDATE)
        assert definition.allowed_tools == (EVIDENCE_CONTEXT_TOOL_NAME,)
        assert definition.stage_version == "validate.rag.v1"
        assert definition.checkpoint_schema_version == "validate.rag.checkpoint.v1"
        assert {status.value for status in EvidenceAnswerStatus} == {"answered", "unknown"}

        with pytest.raises(AgentValidationError, match="chain"):
            evidence_backed_rag_stage_input(
                connection,
                _tool_request(case_id, "warranty marker"),
                prior_checkpoint_hashes=(),
            )
        too_large = _tool_request(case_id, "warranty marker", max_text_bytes=32_001)
        with pytest.raises(AgentValidationError, match="budget"):
            evidence_backed_rag_stage_input(
                connection,
                too_large,
                prior_checkpoint_hashes=("a" * 64,) * _PRIOR_COUNT,
            )
        for over_budget in (
            _tool_request(case_id, "warranty marker", retrieval_limit=11),
            _tool_request(case_id, "warranty marker", adjacent_before=3),
            _tool_request(case_id, "warranty marker", adjacent_after=3),
        ):
            with pytest.raises(AgentValidationError, match="budget"):
                evidence_backed_rag_stage_input(
                    connection,
                    over_budget,
                    prior_checkpoint_hashes=("a" * 64,) * _PRIOR_COUNT,
                )
    finally:
        connection.close()


def test_answered_pipeline_persists_only_cited_checkpoint_and_is_restart_stable(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection, case_id, text = _database_with_evidence(tmp_path)
    provider = _PipelineProvider()
    try:
        runner, run = _advance_to_validate(connection, case_id, provider)
        before = _domain_snapshot(connection)
        stage_input = evidence_backed_rag_stage_input(
            connection,
            _tool_request(case_id, '"warranty alpha-7391"'),
            prior_checkpoint_hashes=_checkpoint_hashes(run),
        )
        completed = _execute(runner, run, stage_input)

        assert completed.status is AgentRunStatus.COMPLETED
        checkpoint = completed.stages[-1].checkpoint
        assert checkpoint is not None
        payload = json.loads(checkpoint.payload_json)
        assert payload["schema_version"] == EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION
        assert payload["status"] == "answered"
        assert payload["tool"]["name"] == EVIDENCE_CONTEXT_TOOL_NAME
        ref = payload["statements"][0]["source_refs"][0]
        authoritative = connection.execute(
            """
            SELECT s.case_id, s.id AS source_id, ss.id AS segment_id,
                   ss.sequence, ss.page, ss.text_sha256
            FROM source AS s JOIN source_segment AS ss ON ss.source_id = s.id
            WHERE ss.id = 'segment-rag'
            """
        ).fetchone()
        assert authoritative is not None
        assert (
            ref["case_id"],
            ref["source_id"],
            ref["segment_id"],
            ref["sequence"],
            ref["page"],
            ref["text_sha256"],
        ) == tuple(authoritative)
        assert ref["quote_hash"] == hashlib.sha256(text.encode()).hexdigest()
        assert text not in checkpoint.payload_json
        assert _domain_snapshot(connection) == before
    finally:
        connection.close()

    restarted = connect_database(database_path)
    try:
        restored = StageRunner(
            restarted,
            provider,
            registry=EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
            checkpoint_handler=EvidenceBackedRagCheckpointHandler(),
        ).get_run(case_id, "run-rag")
        assert restored == completed
    finally:
        restarted.close()


def test_invalid_derived_quote_metadata_is_recomputed_without_repair(tmp_path: Path) -> None:
    connection, case_id, _text = _database_with_evidence(tmp_path)
    provider = _PipelineProvider(validate_modes=("forged",))
    try:
        runner, run = _advance_to_validate(connection, case_id, provider)
        stage_input = evidence_backed_rag_stage_input(
            connection,
            _tool_request(case_id, '"warranty alpha-7391"'),
            prior_checkpoint_hashes=_checkpoint_hashes(run),
        )
        completed = _execute(runner, run, stage_input)

        assert completed.status is AgentRunStatus.COMPLETED
        assert completed.stages[-1].repair_count == 0
        assert len(provider.requests) == 7
        checkpoint = completed.stages[-1].checkpoint
        assert checkpoint is not None
        payload = json.loads(checkpoint.payload_json)
        source_ref = payload["statements"][0]["source_refs"][0]
        context_text = json.loads(stage_input.payload_json)["stage_payload"]["tool_result"][
            "context"
        ]["items"][0]["text"]
        assert source_ref["quote_start"] == 0
        assert source_ref["quote_end"] == len(context_text)
        assert source_ref["quote_hash"] == hashlib.sha256(context_text.encode()).hexdigest()
    finally:
        connection.close()


def test_forged_or_cross_case_citations_fail_closed_after_repair(tmp_path: Path) -> None:
    connection, case_id, _text = _database_with_evidence(tmp_path)
    provider = _PipelineProvider(validate_modes=("cross_case", "cross_case"))
    try:
        runner, run = _advance_to_validate(connection, case_id, provider)
        stage_input = evidence_backed_rag_stage_input(
            connection,
            _tool_request(case_id, '"warranty alpha-7391"'),
            prior_checkpoint_hashes=_checkpoint_hashes(run),
        )
        with pytest.raises(AgentStageSchemaError):
            _execute(runner, run, stage_input)

        durable = runner.get_run(case_id, run.id)
        assert durable.status is AgentRunStatus.NEEDS_REVIEW
        assert durable.stages[-1].checkpoint is None
        assert durable.stages[-1].repair_count == 1
    finally:
        connection.close()


def test_self_hashed_forged_tool_result_is_replayed_and_rejected(tmp_path: Path) -> None:
    connection, case_id, _text = _database_with_evidence(tmp_path)
    provider = _PipelineProvider(validate_modes=("unknown", "unknown"))
    try:
        runner, run = _advance_to_validate(connection, case_id, provider)
        original = evidence_backed_rag_stage_input(
            connection,
            _tool_request(case_id, '"warranty alpha-7391"'),
            prior_checkpoint_hashes=_checkpoint_hashes(run),
        )
        payload = json.loads(original.payload_json)
        tool_result = payload["stage_payload"]["tool_result"]
        forged_context = tool_result["context"]
        forged_context["matches"] = []
        forged_context["items"] = []
        forged_context["used_text_bytes"] = 0
        canonical_context = json.dumps(
            forged_context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        tool_result["context_sha256"] = hashlib.sha256(canonical_context.encode()).hexdigest()
        payload_json = json.dumps(payload)
        forged = StageInput(
            case_id=case_id,
            stage=AgentStageName.VALIDATE,
            stage_version=original.stage_version,
            input_schema_version=original.input_schema_version,
            input_scope_sha256=stage_input_scope_sha256(
                case_id,
                AgentStageName.VALIDATE,
                original.stage_version,
                original.input_schema_version,
                payload_json,
            ),
            payload_json=payload_json,
        )

        with pytest.raises(AgentStageSchemaError):
            _execute(runner, run, forged)
        assert runner.get_run(case_id, run.id).stages[-1].checkpoint is None
    finally:
        connection.close()


def test_no_evidence_forces_unknown_and_never_generates_an_answer(tmp_path: Path) -> None:
    connection, case_id, _text = _database_with_evidence(tmp_path)
    provider = _PipelineProvider(validate_modes=("empty_answer", "unknown"))
    try:
        runner, run = _advance_to_validate(connection, case_id, provider)
        stage_input = evidence_backed_rag_stage_input(
            connection,
            _tool_request(case_id, '"absent synthetic fact"'),
            prior_checkpoint_hashes=_checkpoint_hashes(run),
        )
        context = json.loads(stage_input.payload_json)["stage_payload"]["tool_result"]["context"]
        assert context["items"] == []

        completed = _execute(runner, run, stage_input)
        payload = json.loads(completed.stages[-1].checkpoint.payload_json)  # type: ignore[union-attr]
        assert payload["status"] == "unknown"
        assert payload["statements"] == []
        assert payload["uncertainty_reason"]
        assert completed.stages[-1].repair_count == 1
    finally:
        connection.close()


def test_authority_change_before_checkpoint_rolls_back_context_and_checkpoint(
    tmp_path: Path,
) -> None:
    connection, case_id, text = _database_with_evidence(tmp_path)
    provider = _PipelineProvider()
    mutated_text = "Synthetic mutation must roll back."

    def mutate_before_finalize(event: str) -> None:
        if event == "before_stage_finalize" and provider.requests[-1].output_schema_name == (
            "lifecase_evidence_backed_answer_checkpoint_v1"
        ):
            connection.execute(
                "UPDATE source_segment SET text = ?, text_sha256 = ? WHERE id = ?",
                (mutated_text, hashlib.sha256(mutated_text.encode()).hexdigest(), "segment-rag"),
            )

    try:
        base_runner, run = _advance_to_validate(connection, case_id, provider)
        runner = StageRunner(
            connection,
            provider,
            registry=EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
            checkpoint_handler=EvidenceBackedRagCheckpointHandler(),
            failure_injector=mutate_before_finalize,
        )
        stage_input = evidence_backed_rag_stage_input(
            connection,
            _tool_request(case_id, '"warranty alpha-7391"'),
            prior_checkpoint_hashes=_checkpoint_hashes(run),
        )
        with pytest.raises(AgentIntegrityError):
            _execute(runner, run, stage_input)

        row = connection.execute(
            "SELECT text FROM source_segment WHERE id = 'segment-rag'"
        ).fetchone()
        assert row is not None and row["text"] == text
        stage = connection.execute(
            "SELECT checkpoint_json FROM agent_stage_run WHERE run_id = ? AND stage = 'validate'",
            (run.id,),
        ).fetchone()
        assert stage is not None and stage["checkpoint_json"] is None
        assert base_runner.get_run(case_id, run.id).last_completed_stage is (
            AgentStageName.PROPOSE_ACTIONS
        )
    finally:
        connection.close()


def _database_with_evidence(tmp_path: Path) -> tuple[sqlite3.Connection, str, str]:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic Evidence-backed RAG",
        goal="Answer only from authoritative synthetic evidence",
        template_id="warranty",
    )
    text = "Synthetic warranty alpha-7391 lasts twenty-four months."
    text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO source (
            id, case_id, original_name, media_type, sha256,
            size_bytes, object_path, imported_at
        ) VALUES ('source-rag', ?, 'synthetic.txt', 'text/plain', ?, 1, ?, ?)
        """,
        (case.id, "0" * 64, f"objects/00/00/{'0' * 64}", _NOW),
    )
    connection.execute(
        """
        INSERT INTO source_segment (
            id, source_id, sequence, page, bbox_json, text,
            extraction_method, confidence, text_sha256, extractor_version
        ) VALUES ('segment-rag', 'source-rag', 0, 1, NULL, ?,
                  'direct', 1.0, ?, 'synthetic.v1')
        """,
        (text, text_sha256),
    )
    RetrievalService(connection).rebuild_index()
    return connection, case.id, text


def _tool_request(
    case_id: str,
    query: str,
    *,
    max_text_bytes: int = 20_000,
    retrieval_limit: int = 10,
    adjacent_before: int = 1,
    adjacent_after: int = 1,
) -> EvidenceContextToolRequest:
    return EvidenceContextToolRequest(
        tool_name=EVIDENCE_CONTEXT_TOOL_NAME,
        context_request=EvidenceContextRequest(
            case_id=case_id,
            query=query,
            retrieval_limit=retrieval_limit,
            adjacent_before=adjacent_before,
            adjacent_after=adjacent_after,
            budget=EvidenceContextBudget(
                max_text_bytes=max_text_bytes,
                max_item_text_bytes=min(max_text_bytes, 10_000),
            ),
            policy=EvidencePolicy(frozenset({EvidenceTrustLabel.UNTRUSTED})),
        ),
        budget=ReadOnlyToolBudget(),
    )


def _advance_to_validate(
    connection: sqlite3.Connection,
    case_id: str,
    provider: _PipelineProvider,
) -> tuple[StageRunner, object]:
    runner = StageRunner(
        connection,
        provider,
        registry=EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
        checkpoint_handler=EvidenceBackedRagCheckpointHandler(),
    )
    run = runner.create_run(CreateAgentRunCommand(run_id="run-rag", case_id=case_id))
    for stage in STAGE_ORDER[:-1]:
        prior = _checkpoint_hashes(run)
        if stage is AgentStageName.EXTRACT:
            stage_input = warranty_extraction_stage_input(
                case_id,
                (),
                prior_checkpoint_hashes=prior,
            )
        else:
            stage_input = _generic_stage_input(case_id, stage, prior)
        run = _execute(runner, run, stage_input)
    return runner, run


def _execute(runner: StageRunner, run: object, stage_input: StageInput):
    return asyncio.run(
        runner.execute_stage(
            ExecuteStageCommand(
                run_id=run.id,  # type: ignore[attr-defined]
                expected_run_version=run.version,  # type: ignore[attr-defined]
                expected_stage_version=None,
                stage_input=stage_input,
                stage_budget=StageBudget(),
                reservation=CallReservation(20, 10, 0),
            )
        )
    )


def _generic_stage_input(
    case_id: str,
    stage: AgentStageName,
    prior: tuple[str, ...],
) -> StageInput:
    definition = EVIDENCE_BACKED_RAG_STAGE_REGISTRY.get(stage)
    payload_json = json.dumps(
        {
            "case_id": case_id,
            "prior_checkpoint_hashes": list(prior),
            "stage_payload": {},
        }
    )
    return StageInput(
        case_id=case_id,
        stage=stage,
        stage_version=definition.stage_version,
        input_schema_version=definition.input_schema_version,
        input_scope_sha256=stage_input_scope_sha256(
            case_id,
            stage,
            definition.stage_version,
            definition.input_schema_version,
            payload_json,
        ),
        payload_json=payload_json,
    )


def _checkpoint_hashes(run: object) -> tuple[str, ...]:
    return tuple(
        stage.checkpoint.payload_sha256
        for stage in run.stages  # type: ignore[attr-defined]
        if stage.checkpoint is not None
    )


def _unknown_extraction_checkpoint() -> dict[str, object]:
    fields = (
        "merchant_or_manufacturer",
        "product_name",
        "purchase_date",
        "order_number",
        "amount",
        "serial_number",
    )
    return {
        "schema_version": "warranty_extraction.v1",
        "facts": [
            {
                "field": field,
                "origin": "unknown",
                "value": None,
                "confidence": None,
                "uncertainty_reason": "Synthetic stage fixture has no extraction input",
                "source_refs": [],
            }
            for field in fields
        ],
    }


def _answer_checkpoint(
    request: StructuredGenerationRequest,
    mode: str,
) -> dict[str, object]:
    tool_result = json.loads(request.input_json)["stage_payload"]["tool_result"]
    context = tool_result["context"]
    tool = {
        "name": EVIDENCE_CONTEXT_TOOL_NAME,
        "call_count": 1,
        "context_sha256": tool_result["context_sha256"],
    }
    if mode == "unknown":
        return {
            "schema_version": EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION,
            "status": "unknown",
            "statements": [],
            "uncertainty_reason": "The synthetic Context contains no supporting segment",
            "tool": tool,
        }
    if mode == "empty_answer":
        return {
            "schema_version": EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION,
            "status": "answered",
            "statements": [],
            "uncertainty_reason": None,
            "tool": tool,
        }
    item = context["items"][0]
    citation = item["citation"]
    text = item["text"]
    source_ref = {
        **citation,
        "quote_start": 0,
        "quote_end": len(text),
        "quote_hash": hashlib.sha256(text.encode()).hexdigest(),
    }
    if mode == "forged":
        source_ref["quote_hash"] = "f" * 64
    elif mode == "cross_case":
        source_ref["case_id"] = "other_case"
    return {
        "schema_version": EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION,
        "status": "answered",
        "statements": [
            {
                "text": "The synthetic warranty lasts twenty-four months.",
                "source_refs": [source_ref],
            }
        ],
        "uncertainty_reason": None,
        "tool": tool,
    }


def _domain_snapshot(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    tables = ("case_record", "claim", "claim_source", "source", "source_segment")
    return tuple(
        tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall())
        for table in tables
    )
