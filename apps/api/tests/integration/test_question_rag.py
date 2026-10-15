"""Question analysis keeps current fact authority separate from original quotes."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from lifecase.agent import rag
from lifecase.agent.errors import AgentIntegrityError, AgentStageSchemaError, AgentValidationError
from lifecase.agent.models import (
    AgentCheckpoint,
    AgentRunStatus,
    AgentStageName,
    CallReservation,
    CreateAgentRunCommand,
    ExecuteStageCommand,
    RecoverAgentRunCommand,
    StageBudget,
    checkpoint_payload,
    stage_input_scope_sha256,
)
from lifecase.agent.question_context import basis_sha256, question_basis
from lifecase.agent.service import StageRunner
from lifecase.cases import CaseService
from lifecase.claims.current_facts import CurrentFactsPersistenceError, CurrentFactsService
from lifecase.claims.models import (
    ClaimConfirmationBasis,
    ClaimCreatedBy,
    ClaimSourceInput,
    ClaimSourceRelation,
    ClaimStatus,
    ClaimValue,
    ConfirmClaimCommand,
    CorrectClaimCommand,
    CreateClaimCommand,
    RejectClaimCommand,
)
from lifecase.claims.service import ClaimService
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
    StructuredGenerationResult,
    canonical_json_object,
)
from lifecase.retrieval import RetrievalService
from lifecase.storage import apply_migrations, connect_database
from lifecase.tools import (
    EVIDENCE_CONTEXT_TOOL_NAME,
    EvidenceContextToolRequest,
    ReadOnlyToolBudget,
)


@pytest.fixture
def evidence(tmp_path: Path, request):
    connection = connect_database(tmp_path / "question.sqlite3")
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="合成问答案件", goal="原案件目标保持不变", template_id="warranty"
    )
    text = getattr(request, "param", "合成 washing machine 型号 H8。保修二十四个月。")
    digest = hashlib.sha256(text.encode()).hexdigest()
    connection.execute(
        """INSERT INTO source
        (id, case_id, original_name, media_type, sha256, size_bytes, object_path, imported_at)
        VALUES ('question-source', ?, 'synthetic.txt', 'text/plain', ?, 1, ?, ?)""",
        (case.id, "0" * 64, f"objects/00/00/{'0' * 64}", "2026-09-19T00:00:00Z"),
    )
    connection.execute(
        """INSERT INTO source_segment
        (id, source_id, sequence, page, bbox_json, text, extraction_method,
         confidence, text_sha256, extractor_version)
        VALUES ('question-segment', 'question-source', 0, NULL, NULL, ?,
                'direct', 1.0, ?, 'synthetic.v1')""",
        (text, digest),
    )
    RetrievalService(connection).rebuild_index()
    service = ClaimService(connection)
    start = text.index("H8")
    claim = service.create_claim(
        CreateClaimCommand(
            case_id=case.id,
            field="product_name",
            value=ClaimValue.from_input("H8"),
            status=ClaimStatus.CANDIDATE,
            confidence=None,
            created_by=ClaimCreatedBy.USER,
            actor="synthetic_operator",
            reason="从合成原文登记",
            sources=(
                ClaimSourceInput(
                    source_id="question-source",
                    segment_id="question-segment",
                    segment_text_sha256=digest,
                    quote_start=start,
                    quote_end=start + 2,
                    quote_hash=hashlib.sha256(b"H8").hexdigest(),
                    relation=ClaimSourceRelation.SUPPORTS,
                ),
            ),
        )
    )
    corrected = service.correct_claim(
        CorrectClaimCommand(
            claim_id=claim.id,
            expected_version=claim.version,
            value=ClaimValue.from_input("H9"),
            actor="synthetic_reviewer",
            reason="明确修正型号，保留原文",
        )
    )
    yield connection, case.id, corrected, text
    connection.close()


def _input(connection, case_id, *, query="washing machine", facts=None, budget=None):
    return rag.evidence_backed_rag_stage_input(
        connection,
        EvidenceContextToolRequest(
            tool_name=EVIDENCE_CONTEXT_TOOL_NAME,
            context_request=EvidenceContextRequest(
                case_id=case_id,
                query=query,
                retrieval_limit=10,
                adjacent_before=1,
                adjacent_after=1,
                budget=budget or EvidenceContextBudget(20_000, 10_000),
                policy=EvidencePolicy(frozenset({EvidenceTrustLabel.UNTRUSTED})),
            ),
            budget=ReadOnlyToolBudget(),
        ),
        prior_checkpoint_hashes=(),
        question="已人工修正后的设备型号是什么？还缺什么材料？",
        current_facts=facts
        if facts is not None
        else rag.analysis_facts_context(connection, case_id),
    )


def _checkpoint(stage_input, *, fact=None, source=None, text="当前型号 H9"):
    payload = {
        "schema_version": "evidence_backed_answer.v2",
        "status": "answered",
        "statements": [
            {
                "text": text,
                "source_refs": [] if source is None else [source],
                "fact_refs": [] if fact is None else [fact],
            }
        ],
        "uncertainty_reason": None,
        "tool": {
            "name": EVIDENCE_CONTEXT_TOOL_NAME,
            "call_count": 1,
            "context_sha256": json.loads(stage_input.payload_json)["stage_payload"]["tool_result"][
                "context_sha256"
            ],
        },
    }
    payload_json, digest = checkpoint_payload(payload)
    return AgentCheckpoint(
        stage=AgentStageName.VALIDATE,
        stage_version=stage_input.stage_version,
        schema_version="validate.question.checkpoint.v1",
        input_scope_sha256=stage_input.input_scope_sha256,
        payload_json=payload_json,
        payload_sha256=digest,
    )


def _fact_ref(claim):
    return {
        "case_id": claim.case_id,
        "claim_id": claim.id,
        "version": claim.version,
        "value_sha256": claim.value.sha256,
    }


def _prepare(connection, case_id, stage_input, checkpoint):
    return rag.EvidenceBackedRagCheckpointHandler().prepare(
        connection=connection,
        run_id="synthetic-question",
        case_id=case_id,
        stage_input_json=stage_input.payload_json,
        checkpoint=checkpoint,
    )


def test_question_preserves_original_chinese_and_separates_retrieval(evidence):
    connection, case_id, claim, _text = evidence
    stage_input = _input(connection, case_id)
    payload = json.loads(stage_input.payload_json)["stage_payload"]
    assert payload["question"] == "已人工修正后的设备型号是什么？还缺什么材料？"
    assert payload["retrieval_query"] == "washing machine"
    assert CaseService(connection).get_case(case_id).goal == "原案件目标保持不变"
    fact = payload["current_facts"]["items"][0]
    assert fact["claim_id"] == claim.id
    assert fact["value"] == "H9"
    assert fact["confirmation"]["actor"] == "synthetic_reviewer"
    assert fact["confirmation"]["reason"] == "明确修正型号，保留原文"
    assert fact["citations"][0]["quote_text"] == "H8"


def test_corrected_fact_can_answer_without_matching_source(evidence):
    connection, case_id, claim, _text = evidence
    stage_input = _input(connection, case_id, query='"absent-mark-7391"')
    prepared = _prepare(
        connection, case_id, stage_input, _checkpoint(stage_input, fact=_fact_ref(claim))
    )
    assert prepared.status is rag.EvidenceAnswerStatus.ANSWERED
    assert prepared.statements[0].source_refs == ()
    assert prepared.statements[0].fact_refs[0].claim_id == claim.id


@pytest.mark.parametrize("change", ["case", "version", "hash", "old_text"])
def test_fact_reference_rejects_forgery_and_old_value(evidence, change):
    connection, case_id, claim, _text = evidence
    stage_input = _input(connection, case_id)
    ref = _fact_ref(claim)
    text = "当前型号 H9"
    if change == "case":
        ref["case_id"] = "another-case"
    elif change == "version":
        ref["version"] = 1
    elif change == "hash":
        ref["value_sha256"] = "0" * 64
    else:
        text = "当前型号 H8"
    with pytest.raises(AgentValidationError):
        _prepare(connection, case_id, stage_input, _checkpoint(stage_input, fact=ref, text=text))


def test_old_source_quote_cannot_override_corrected_fact(evidence):
    connection, case_id, _claim, text = evidence
    stage_input = _input(connection, case_id)
    item = json.loads(stage_input.payload_json)["stage_payload"]["tool_result"]["context"]["items"][
        0
    ]
    source = dict(
        item["citation"],
        quote_start=0,
        quote_end=len(text),
        quote_hash=hashlib.sha256(text.encode()).hexdigest(),
    )
    with pytest.raises(AgentValidationError):
        _prepare(
            connection,
            case_id,
            stage_input,
            _checkpoint(stage_input, source=source, text="型号是 H8"),
        )
    start = text.index("保修")
    policy = dict(
        item["citation"],
        quote_start=start,
        quote_end=len(text),
        quote_hash=hashlib.sha256(text[start:].encode()).hexdigest(),
    )
    assert (
        _prepare(
            connection,
            case_id,
            stage_input,
            _checkpoint(stage_input, source=policy, text="保修二十四个月。"),
        ).status
        is rag.EvidenceAnswerStatus.ANSWERED
    )


def test_fact_change_between_prepare_and_commit_is_rejected(evidence):
    connection, case_id, claim, _text = evidence
    stage_input = _input(connection, case_id)
    checkpoint = _checkpoint(stage_input, fact=_fact_ref(claim))
    prepared = _prepare(connection, case_id, stage_input, checkpoint)
    ClaimService(connection).correct_claim(
        CorrectClaimCommand(
            claim_id=claim.id,
            expected_version=claim.version,
            value=ClaimValue.from_input("H10"),
            actor="second_reviewer",
            reason="进一步人工修正",
        )
    )
    connection.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(AgentValidationError, match="facts"):
            rag.EvidenceBackedRagCheckpointHandler().commit(
                connection=connection,
                run_id="synthetic-question",
                case_id=case_id,
                checkpoint=checkpoint,
                prepared=prepared,
            )
        assert connection.in_transaction
    finally:
        connection.execute("ROLLBACK")


def test_fact_context_reuses_transaction_without_owning_commit(evidence):
    connection, case_id, _claim, _text = evidence
    connection.execute("BEGIN")
    try:
        with pytest.raises(CurrentFactsPersistenceError, match="idle"):
            CurrentFactsService(connection).list_case_facts(case_id)
        facts = rag.analysis_facts_context(connection, case_id)
        assert facts["case_id"] == case_id
        assert connection.in_transaction
    finally:
        connection.execute("ROLLBACK")


def test_user_confirmed_fact_without_source_is_supported(evidence):
    connection, case_id, _claim, _text = evidence
    service = ClaimService(connection)
    claim = service.create_claim(
        CreateClaimCommand(
            case_id=case_id,
            field="serial_number",
            value=ClaimValue.from_input("SYN-001"),
            status=ClaimStatus.CANDIDATE,
            confidence=None,
            created_by=ClaimCreatedBy.USER,
            actor="synthetic_operator",
            reason="本人登记合成序列号",
        )
    )
    pending = _input(connection, case_id)
    with pytest.raises(AgentValidationError):
        _prepare(
            connection,
            case_id,
            pending,
            _checkpoint(pending, fact=_fact_ref(claim), text="序列号 SYN-001"),
        )
    claim = service.confirm_claim(
        ConfirmClaimCommand(
            claim_id=claim.id,
            expected_version=claim.version,
            basis=ClaimConfirmationBasis.USER,
            actor="synthetic_operator",
            reason="本人确认合成序列号",
        )
    )
    stage_input = _input(connection, case_id)
    assert (
        _prepare(
            connection,
            case_id,
            stage_input,
            _checkpoint(stage_input, fact=_fact_ref(claim), text="序列号 SYN-001"),
        ).status
        is rag.EvidenceAnswerStatus.ANSWERED
    )


def test_facts_changed_during_call_fail_without_model_repair(evidence):
    connection, case_id, claim, _text = evidence
    stage_input = _input(connection, case_id)
    basis = question_basis(connection, case_id)
    stage_payload = json.loads(stage_input.payload_json)["stage_payload"]
    binding = canonical_json_object(
        {
            "schema_version": "live_agent_question.v1",
            "case_id": case_id,
            "question": stage_payload["question"],
            "retrieval_query": stage_payload["retrieval_query"],
            "basis": basis,
            "basis_sha256": basis_sha256(basis),
            "authorization_sha256": "a" * 64,
        },
        maximum_bytes=100_000,
    )

    class ChangingFactsProvider:
        calls = 0

        async def generate_structured(self, request):
            self.calls += 1
            if self.calls == 1:
                ClaimService(connection).correct_claim(
                    CorrectClaimCommand(
                        claim_id=claim.id,
                        expected_version=claim.version,
                        value=ClaimValue.from_input("H10"),
                        actor="concurrent_reviewer",
                        reason="模型返回前人工修正",
                    )
                )
            checkpoint = json.loads(_checkpoint(stage_input, fact=_fact_ref(claim)).payload_json)
            return StructuredGenerationResult(
                request_id=request.request_id,
                output_json=json.dumps(
                    {
                        "stage": "validate",
                        "stage_version": "validate.question.v1",
                        "checkpoint_schema_version": "validate.question.checkpoint.v1",
                        "checkpoint": checkpoint,
                    }
                ),
                metadata=ProviderCallMetadata(
                    provider=ModelProviderKind.FAKE,
                    model_id="lifecase-fake-question-v1",
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

    provider = ChangingFactsProvider()
    runner = StageRunner(
        connection,
        provider,
        registry=rag.QUESTION_RAG_REGISTRY,
        checkpoint_handler=rag.EvidenceBackedRagCheckpointHandler(),
    )
    run = runner.create_run(
        CreateAgentRunCommand(
            run_id="portfolio-live-q1-00000000-0000-4000-8000-000000000001",
            case_id=case_id,
            input_binding_json=binding,
            input_binding_sha256=hashlib.sha256(binding.encode()).hexdigest(),
        )
    )
    with pytest.raises(AgentIntegrityError):
        asyncio.run(
            runner.execute_stage(
                ExecuteStageCommand(
                    run_id=run.id,
                    expected_run_version=run.version,
                    expected_stage_version=None,
                    stage_input=stage_input,
                    stage_budget=StageBudget(),
                    reservation=CallReservation(20, 10, 0),
                )
            )
        )
    stopped = runner.get_run(case_id, run.id)
    assert provider.calls == 1
    assert stopped.status is AgentRunStatus.NEEDS_REVIEW
    assert stopped.stages[0].error_code == "agent_integrity_failure"
    assert stopped.stages[0].retryable is False
    assert stopped.usage.calls == 1
    assert stopped.usage.reserved_total_tokens == 0
    assert stopped.stages[0].checkpoint is None
    assert stopped.stages[0].repair_count == 0


def _recorded_q07(stage_input, claim, number):
    """Rebind synthetic identities only; retain the recorded text and bad quotes."""
    path = Path(__file__).parents[1] / "fixtures" / "q07_deepseek_candidates.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["candidates"]
    output = next(row["output"] for row in rows if row["call_number"] == number)
    stage = json.loads(stage_input.payload_json)["stage_payload"]
    checkpoint = output["checkpoint"]
    checkpoint["tool"]["context_sha256"] = stage["tool_result"]["context_sha256"]
    citation = stage["tool_result"]["context"]["items"][0]["citation"]
    for statement in checkpoint["statements"]:
        for ref in statement["source_refs"]:
            ref.update(citation)
        for ref in statement["fact_refs"]:
            ref.update(_fact_ref(claim))
    return output


@pytest.mark.parametrize("number", [7, 8, 21, 22])
def test_q07_recorded_bad_quotes_remain_rejected_after_normalization(evidence, number):
    connection, case_id, claim, text = evidence
    stage_input = _input(connection, case_id)
    output = _recorded_q07(stage_input, claim, number)
    payload, digest = checkpoint_payload(output["checkpoint"])
    checkpoint = AgentCheckpoint(
        stage=AgentStageName.VALIDATE,
        stage_version=stage_input.stage_version,
        schema_version="validate.question.checkpoint.v1",
        input_scope_sha256=stage_input.input_scope_sha256,
        payload_json=payload,
        payload_sha256=digest,
    )
    handler = rag.EvidenceBackedRagCheckpointHandler()
    normalized = handler.normalize(
        connection=connection,
        case_id=case_id,
        stage_input_json=stage_input.payload_json,
        checkpoint=checkpoint,
    )
    refs = [
        ref
        for statement in json.loads(normalized.payload_json)["statements"]
        for ref in statement["source_refs"]
    ]
    # Initial quotes are valid H8; repairs contain an empty-string hash and lose 月.
    assert [(ref["quote_start"], ref["quote_end"]) for ref in refs] == (
        [(22, 24)] if number in (7, 21) else [(0, len(text))]
    )
    with pytest.raises(AgentValidationError) as caught:
        _prepare(connection, case_id, stage_input, normalized)
    assert "corrected or unsettled" in str(caught.value.__cause__)


class _Q07Provider:
    def __init__(self, stage_input, claim, *, first=None, repeat_bad=False):
        self.stage_input, self.claim = stage_input, claim
        self.first, self.repeat_bad = first, repeat_bad
        self.requests = []

    async def generate_structured(self, request):
        self.requests.append(request)
        stage = json.loads(request.input_json)["stage_payload"]
        candidates = stage.get("source_quote_candidates", [])
        if self.first is not None and len(self.requests) == 1:
            output = _recorded_q07(self.stage_input, self.claim, self.first)
        elif self.repeat_bad or not candidates:
            output = _recorded_q07(self.stage_input, self.claim, 22 if self.first == 21 else 8)
        else:
            candidate = next(
                item for item in candidates if item["quote_text"] == "保修二十四个月。"
            )
            checkpoint = json.loads(
                _checkpoint(
                    self.stage_input,
                    fact=_fact_ref(self.claim),
                    source=candidate["source_ref"],
                    text="当前型号 H9；材料记载保修二十四个月。",
                ).payload_json
            )
            output = {
                "stage": "validate",
                "stage_version": "validate.question.v1",
                "checkpoint_schema_version": "validate.question.checkpoint.v1",
                "checkpoint": checkpoint,
            }
        return StructuredGenerationResult(
            request_id=request.request_id,
            output_json=json.dumps(output),
            metadata=ProviderCallMetadata(
                provider=ModelProviderKind.FAKE,
                model_id="lifecase-q07-replay",
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


def _q07_runner(connection, case_id, stage_input, provider, **kwargs):
    stage = json.loads(stage_input.payload_json)["stage_payload"]
    basis = question_basis(connection, case_id)
    binding = canonical_json_object(
        {
            "schema_version": "live_agent_question.v1",
            "case_id": case_id,
            "question": stage["question"],
            "retrieval_query": stage["retrieval_query"],
            "basis": basis,
            "basis_sha256": basis_sha256(basis),
            "authorization_sha256": "a" * 64,
        },
        maximum_bytes=100_000,
    )
    runner = StageRunner(
        connection,
        provider,
        registry=rag.QUESTION_RAG_REGISTRY,
        checkpoint_handler=rag.EvidenceBackedRagCheckpointHandler(),
        **kwargs,
    )
    run = runner.create_run(
        CreateAgentRunCommand(
            run_id="portfolio-live-q1-00000000-0000-4000-8000-000000000007",
            case_id=case_id,
            input_binding_json=binding,
            input_binding_sha256=hashlib.sha256(binding.encode()).hexdigest(),
        )
    )
    return runner, run


def _q07_execute(runner, run, stage_input):
    return asyncio.run(
        runner.execute_stage(
            ExecuteStageCommand(
                run_id=run.id,
                expected_run_version=run.version,
                expected_stage_version=run.stages[0].version if run.stages else None,
                stage_input=stage_input,
                stage_budget=StageBudget(),
                reservation=CallReservation(20, 10, 0),
            )
        )
    )


@pytest.mark.parametrize("first", [None, 7, 21])
def test_q07_copy_ready_quote_completes_initial_or_single_repair(evidence, first):
    connection, case_id, claim, text = evidence
    stage_input = _input(connection, case_id)
    original = stage_input.payload_json
    provider = _Q07Provider(stage_input, claim, first=first)
    runner, run = _q07_runner(connection, case_id, stage_input, provider)
    completed = _q07_execute(runner, run, stage_input)
    assert completed.status is AgentRunStatus.COMPLETED
    assert len(provider.requests) == (1 if first is None else 2)
    assert completed.stages[0].repair_count == (0 if first is None else 1)
    assert completed.usage.reserved_total_tokens == 0
    assert len({request.input_json for request in provider.requests}) == 1
    sent = json.loads(provider.requests[0].input_json)
    candidates = sent["stage_payload"].pop("source_quote_candidates")
    assert sent == json.loads(original)
    assert len(candidates) == 1
    assert candidates[0]["quote_text"] == text[25:33] == "保修二十四个月。"
    assert candidates[0]["source_ref"]["quote_start"] == 25
    assert candidates[0]["source_ref"]["quote_end"] == 33
    assert (
        candidates[0]["source_ref"]["quote_hash"]
        == hashlib.sha256(text[25:33].encode()).hexdigest()
    )
    assert "source_quote_candidates" in provider.requests[0].stage_instruction
    assert stage_input.payload_json == original == _input(connection, case_id).payload_json
    saved = runner.get_run(case_id, run.id)
    assert saved.stages[0].checkpoint.input_scope_sha256 == stage_input.input_scope_sha256
    assert saved.input_binding_json == run.input_binding_json
    answer = json.loads(saved.stages[0].checkpoint.payload_json)
    assert answer["statements"][0]["source_refs"] == [candidates[0]["source_ref"]]
    assert answer["statements"][0]["fact_refs"] == [_fact_ref(claim)]


@pytest.mark.parametrize("first", [7, 21])
def test_q07_recorded_two_bad_candidates_stop_after_single_repair(evidence, first):
    connection, case_id, claim, _text = evidence
    stage_input = _input(connection, case_id)
    provider = _Q07Provider(stage_input, claim, first=first, repeat_bad=True)
    runner, run = _q07_runner(connection, case_id, stage_input, provider)
    with pytest.raises(AgentStageSchemaError):
        _q07_execute(runner, run, stage_input)
    stopped = runner.get_run(case_id, run.id)
    assert len(provider.requests) == 2
    assert stopped.status is AgentRunStatus.NEEDS_REVIEW
    assert stopped.stages[0].repair_count == 1
    assert stopped.stages[0].checkpoint is None
    assert stopped.usage.reserved_total_tokens == 0


def test_q07_original_input_recovers_without_scope_migration(evidence):
    connection, case_id, claim, _text = evidence
    stage_input = _input(connection, case_id)
    provider = _Q07Provider(stage_input, claim)

    def interrupt(event):
        if event == "after_stage_reservation":
            raise RuntimeError("synthetic interruption before provider")

    runner, run = _q07_runner(
        connection, case_id, stage_input, provider, failure_injector=interrupt
    )
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        _q07_execute(runner, run, stage_input)
    interrupted = runner.get_run(case_id, run.id)
    assert provider.requests == []
    restarted = StageRunner(
        connection,
        provider,
        registry=rag.QUESTION_RAG_REGISTRY,
        checkpoint_handler=rag.EvidenceBackedRagCheckpointHandler(),
    )
    recovered = restarted.recover_interrupted(
        RecoverAgentRunCommand(
            run_id=run.id,
            case_id=case_id,
            expected_version=interrupted.version,
        )
    )
    assert recovered.stages[0].input_scope_sha256 == stage_input.input_scope_sha256
    completed = _q07_execute(restarted, recovered, _input(connection, case_id))
    assert completed.status is AgentRunStatus.COMPLETED
    assert len(provider.requests) == 1


def _replace_input_payload(stage_input, payload):
    encoded = canonical_json_object(payload, maximum_bytes=100_000)
    return replace(
        stage_input,
        payload_json=encoded,
        input_scope_sha256=stage_input_scope_sha256(
            stage_input.case_id,
            stage_input.stage,
            stage_input.stage_version,
            stage_input.input_schema_version,
            encoded,
        ),
    )


@pytest.mark.parametrize("forgery", ["fact", "source", "candidates"])
def test_q07_forged_input_stops_before_reserving_or_calling(evidence, forgery):
    connection, case_id, claim, _text = evidence
    stage_input = _input(connection, case_id)
    provider = _Q07Provider(stage_input, claim)
    runner, run = _q07_runner(connection, case_id, stage_input, provider)
    payload = json.loads(stage_input.payload_json)
    stage = payload["stage_payload"]
    if forgery == "fact":
        stage["current_facts"]["items"][0]["usable_as_fact"] = False
    elif forgery == "source":
        context = stage["tool_result"]["context"]
        context["items"][0]["citation"]["source_id"] = "forged-source"
        stage["tool_result"]["context_sha256"] = hashlib.sha256(
            canonical_json_object(context, maximum_bytes=100_000).encode()
        ).hexdigest()
    else:
        stage["source_quote_candidates"] = [{"quote_text": "伪造保修"}]
    with pytest.raises(AgentValidationError):
        _q07_execute(runner, run, _replace_input_payload(stage_input, payload))
    assert provider.requests == []
    assert runner.get_run(case_id, run.id) == run


@pytest.mark.parametrize(
    "evidence",
    [
        "😀 washing machine 型号 H8。\r\n保修二十四个月。\r\n仅合成演示！",
        "washing machine 型号 H8，但不享受任何保修。",
        "washing machine 型号 H8。" + "长" * 2_001 + "。保修二十四个月。",
        "washing machine 型号 H8。" + "保修二十四个月。" * 80,
        "washing machine 型号 H8。" + ("长" * 1_990 + "。") * 4 + "保修二十四个月。",
    ],
    indirect=True,
    ids=["unicode-crlf", "negative-sentence", "long-sentence", "count-limit", "byte-limit"],
)
def test_q07_candidates_keep_whole_sentences_and_bounded_unicode_offsets(evidence):
    connection, case_id, _claim, text = evidence
    stage_input = _input(connection, case_id, budget=EvidenceContextBudget(32_000, 32_000))
    wire = rag.question_provider_input(connection, stage_input, maximum_bytes=100_000)
    candidates = json.loads(wire)["stage_payload"]["source_quote_candidates"]
    assert len(candidates) <= 32
    assert len(wire.encode()) <= min(100_000, len(stage_input.payload_json.encode()) + 16_000)
    for item in candidates:
        quote, ref = item["quote_text"], item["source_ref"]
        assert text[ref["quote_start"] : ref["quote_end"]] == quote
        assert ref["quote_hash"] == hashlib.sha256(quote.encode()).hexdigest()
        assert "H8" not in quote and len(quote) <= 2_000
        assert quote.endswith(("。", "！"))
    if "但不享受" in text:
        assert candidates == []  # No clipping a negative sentence around H8.
    elif text.count("保修二十四个月。") == 80:
        assert len(candidates) == 32
    else:
        assert any(item["quote_text"] == "保修二十四个月。" for item in candidates)
    # An input already at its wire allowance gets no extra field, not truncated facts.
    assert (
        rag.question_provider_input(
            connection, stage_input, maximum_bytes=len(stage_input.payload_json.encode())
        )
        == stage_input.payload_json
    )


@pytest.mark.parametrize("status", ["candidate", "rejected"])
def test_q07_unsettled_warranty_quote_is_not_offered(evidence, status):
    connection, case_id, _claim, text = evidence
    service = ClaimService(connection)
    claim = service.create_claim(
        CreateClaimCommand(
            case_id=case_id,
            field="warranty_note",
            value=ClaimValue.from_input("二十四个月"),
            status=ClaimStatus.CANDIDATE,
            confidence=None,
            created_by=ClaimCreatedBy.USER,
            actor="synthetic_operator",
            reason="未核实的保修登记",
            sources=(
                ClaimSourceInput(
                    source_id="question-source",
                    segment_id="question-segment",
                    segment_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    quote_start=25,
                    quote_end=33,
                    quote_hash=hashlib.sha256(text[25:33].encode()).hexdigest(),
                    relation=ClaimSourceRelation.SUPPORTS,
                ),
            ),
        )
    )
    if status == "rejected":
        service.reject_claim(
            RejectClaimCommand(
                claim_id=claim.id,
                expected_version=claim.version,
                actor="synthetic_reviewer",
                reason="合成拒绝",
            )
        )
    stage_input = _input(connection, case_id)
    wire = rag.question_provider_input(connection, stage_input, maximum_bytes=100_000)
    assert json.loads(wire)["stage_payload"]["source_quote_candidates"] == []


@pytest.mark.parametrize("field", ["case_id", "source_id", "text_sha256", "quote_hash"])
def test_q07_copied_candidate_tampering_still_rejected(evidence, field):
    connection, case_id, claim, _text = evidence
    stage_input = _input(connection, case_id)
    wire = rag.question_provider_input(connection, stage_input, maximum_bytes=100_000)
    ref = json.loads(wire)["stage_payload"]["source_quote_candidates"][0]["source_ref"]
    ref[field] = "0" * 64 if field.endswith("hash") or field.endswith("sha256") else "another-case"
    checkpoint = _checkpoint(
        stage_input, fact=_fact_ref(claim), source=ref, text="当前型号 H9，保修二十四个月。"
    )
    handler = rag.EvidenceBackedRagCheckpointHandler()
    normalized = handler.normalize(
        connection=connection,
        case_id=case_id,
        stage_input_json=stage_input.payload_json,
        checkpoint=checkpoint,
    )
    with pytest.raises(AgentValidationError):
        _prepare(connection, case_id, stage_input, normalized)
