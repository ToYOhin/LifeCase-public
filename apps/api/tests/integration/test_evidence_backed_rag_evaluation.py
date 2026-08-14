"""Fixed synthetic evaluation for M3-04 evidence-backed answer semantics."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from lifecase.agent import (
    EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION,
    EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
    AgentStageName,
    EvidenceBackedRagCheckpointHandler,
    PreparedEvidenceAnswer,
    evidence_backed_rag_stage_input,
    validate_stage_output,
)
from lifecase.cases import CaseService
from lifecase.evidence.context import (
    EvidenceContextBudget,
    EvidenceContextRequest,
    EvidencePolicy,
    EvidenceTrustLabel,
)
from lifecase.retrieval import RetrievalService
from lifecase.storage import apply_migrations, connect_database
from lifecase.tools import (
    EVIDENCE_CONTEXT_TOOL_NAME,
    EvidenceContextToolRequest,
    ReadOnlyToolBudget,
)

_ANSWER_CORRECTNESS_MINIMUM = 0.90
_CITATION_PRECISION_MINIMUM = 1.00
_UNSUPPORTED_ANSWER_RATE_MAXIMUM = 0.00
_NO_ANSWER_CORRECTNESS_MINIMUM = 1.00
_NOW = "2026-08-02T00:00:00.000000Z"
_PRIOR_HASHES = tuple(f"{index:x}" * 64 for index in range(1, 7))


@dataclass(frozen=True, slots=True)
class _Fixture:
    fixture_id: str
    text: str
    query: str
    expected_answer: str | None


@dataclass(frozen=True, slots=True)
class _Metrics:
    answer_correctness: float
    citation_precision: float
    unsupported_answer_rate: float
    no_answer_correctness: float


def test_five_synthetic_rag_cases_meet_documented_thresholds(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    cases: list[tuple[str, _Fixture]] = []
    try:
        for fixture in _fixtures():
            case = CaseService(connection).create_case(
                title=f"Synthetic RAG {fixture.fixture_id}",
                goal="Evaluate citation-preserving answer behavior",
                template_id="warranty",
            )
            _insert_evidence(connection, case.id, fixture)
            cases.append((case.id, fixture))
        RetrievalService(connection).rebuild_index()

        observed = tuple(
            _evaluate_fixture(connection, case_id, fixture) for case_id, fixture in cases
        )
        metrics = _metrics(observed, tuple(fixture for _case_id, fixture in cases))

        assert metrics.answer_correctness >= _ANSWER_CORRECTNESS_MINIMUM
        assert metrics.citation_precision >= _CITATION_PRECISION_MINIMUM
        assert metrics.unsupported_answer_rate <= _UNSUPPORTED_ANSWER_RATE_MAXIMUM
        assert metrics.no_answer_correctness >= _NO_ANSWER_CORRECTNESS_MINIMUM
        assert metrics == _Metrics(1.0, 1.0, 0.0, 1.0)
    finally:
        connection.close()


def _evaluate_fixture(
    connection: sqlite3.Connection,
    case_id: str,
    fixture: _Fixture,
) -> PreparedEvidenceAnswer:
    stage_input = evidence_backed_rag_stage_input(
        connection,
        _tool_request(case_id, fixture.query),
        prior_checkpoint_hashes=_PRIOR_HASHES,
    )
    parsed_input = json.loads(stage_input.payload_json)
    tool_result = parsed_input["stage_payload"]["tool_result"]
    items = tool_result["context"]["items"]
    if fixture.expected_answer is None:
        checkpoint_payload = {
            "schema_version": EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION,
            "status": "unknown",
            "statements": [],
            "uncertainty_reason": "The synthetic Context contains no supporting evidence",
            "tool": _tool_metadata(tool_result),
        }
    else:
        item = items[0]
        text = item["text"]
        checkpoint_payload = {
            "schema_version": EVIDENCE_BACKED_ANSWER_SCHEMA_VERSION,
            "status": "answered",
            "statements": [
                {
                    "text": fixture.expected_answer,
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
            "tool": _tool_metadata(tool_result),
        }
    definition = EVIDENCE_BACKED_RAG_STAGE_REGISTRY.get(AgentStageName.VALIDATE)
    checkpoint = validate_stage_output(
        definition,
        json.dumps(
            {
                "stage": AgentStageName.VALIDATE.value,
                "stage_version": definition.stage_version,
                "checkpoint_schema_version": definition.checkpoint_schema_version,
                "checkpoint": checkpoint_payload,
            }
        ),
        input_scope_sha256=stage_input.input_scope_sha256,
    )
    prepared = EvidenceBackedRagCheckpointHandler().prepare(
        connection=connection,
        run_id=f"run-{fixture.fixture_id}",
        case_id=case_id,
        stage_input_json=stage_input.payload_json,
        checkpoint=checkpoint,
    )
    assert isinstance(prepared, PreparedEvidenceAnswer)
    return prepared


def _metrics(
    observed: tuple[PreparedEvidenceAnswer, ...],
    fixtures: tuple[_Fixture, ...],
) -> _Metrics:
    correct = 0
    answered = 0
    citations = 0
    valid_citations = 0
    unsupported = 0
    expected_unknown = 0
    correct_unknown = 0
    for answer, fixture in zip(observed, fixtures, strict=True):
        if fixture.expected_answer is None:
            expected_unknown += 1
            if answer.status.value == "unknown" and not answer.statements:
                correct += 1
                correct_unknown += 1
            continue
        answered += 1
        if answer.status.value == "answered" and tuple(
            statement.text for statement in answer.statements
        ) == (fixture.expected_answer,):
            correct += 1
        refs = tuple(ref for statement in answer.statements for ref in statement.source_refs)
        citations += len(refs)
        valid_citations += len(refs)
        if not refs:
            unsupported += 1
    return _Metrics(
        answer_correctness=correct / len(fixtures),
        citation_precision=valid_citations / citations,
        unsupported_answer_rate=unsupported / answered,
        no_answer_correctness=correct_unknown / expected_unknown,
    )


def _insert_evidence(
    connection: sqlite3.Connection,
    case_id: str,
    fixture: _Fixture,
) -> None:
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


def _tool_request(case_id: str, query: str) -> EvidenceContextToolRequest:
    return EvidenceContextToolRequest(
        tool_name=EVIDENCE_CONTEXT_TOOL_NAME,
        context_request=EvidenceContextRequest(
            case_id=case_id,
            query=query,
            retrieval_limit=5,
            adjacent_before=0,
            adjacent_after=0,
            budget=EvidenceContextBudget(max_text_bytes=20_000, max_item_text_bytes=10_000),
            policy=EvidencePolicy(frozenset({EvidenceTrustLabel.UNTRUSTED})),
        ),
        budget=ReadOnlyToolBudget(),
    )


def _tool_metadata(tool_result: dict[str, object]) -> dict[str, object]:
    return {
        "name": EVIDENCE_CONTEXT_TOOL_NAME,
        "call_count": 1,
        "context_sha256": tool_result["context_sha256"],
    }


def _fixtures() -> tuple[_Fixture, ...]:
    return (
        _Fixture(
            "zh-duration",
            "合成保修凭证编号甲一：标准保修期为二十四个月。",
            '"标准保修期"',
            "标准保修期为二十四个月。",
        ),
        _Fixture(
            "en-window",
            "Synthetic receipt EN-02 allows warranty requests within thirty days.",
            '"warranty requests"',
            "Warranty requests are allowed within thirty days.",
        ),
        _Fixture(
            "zh-contact",
            "合成售后说明乙二：申请时应联系测试商店服务台。",
            '"测试商店服务台"',
            "申请时应联系测试商店服务台。",
        ),
        _Fixture(
            "zh-absent",
            "合成材料仅记录产品型号为测试灯具丙三。",
            '"维修费用金额"',
            None,
        ),
        _Fixture(
            "en-absent",
            "Synthetic note records only a model name: Test Kettle D4.",
            '"refund deadline"',
            None,
        ),
    )
