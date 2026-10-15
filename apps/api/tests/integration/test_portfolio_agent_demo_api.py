"""P1-01 Case-scoped Portfolio Agent Demo HTTP coverage."""

from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import cast

import pytest
from fastapi.testclient import TestClient

from lifecase.agent import (
    EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
    AgentProviderFailureError,
    AgentRunRecord,
    CallReservation,
    EvidenceBackedRagCheckpointHandler,
    ExecuteStageCommand,
    ExtractionEvidenceSegment,
    StageBudget,
    StageInput,
    StageRunner,
    warranty_extraction_stage_input,
)
from lifecase.agent.demo import PortfolioAgentDemoService
from lifecase.api.errors import ApiErrorResponse
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.providers import (
    FakeModelProvider,
    ModelProviderConfig,
    ModelProviderKind,
    ProviderErrorCode,
    ProviderNetworkError,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)
from lifecase.storage import connect_database


def test_agent_demo_openapi_documents_the_exact_safe_transport_contract(
    tmp_path: Path,
) -> None:
    schema = create_app(_config(tmp_path)).openapi()
    operations = {
        ("/api/v1/cases/{case_id}/agent-run", "get"): "200",
        ("/api/v1/cases/{case_id}/agent-run", "post"): "201",
        ("/api/v1/cases/{case_id}/agent-run/execute", "post"): "200",
        ("/api/v1/cases/{case_id}/agent-run/retry", "post"): "200",
        ("/api/v1/cases/{case_id}/agent-run/recover", "post"): "200",
        ("/api/v1/cases/{case_id}/agent-run/cancel", "post"): "200",
    }

    for (path, method), success_status in operations.items():
        operation = schema["paths"][path][method]
        assert "requestBody" not in operation
        assert set(operation["responses"]) == {
            success_status,
            "404",
            "409",
            "422",
            "503",
        }
        assert operation["responses"][success_status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/AgentDemoResponse"
        }
        for error_status in ("404", "409", "422", "503"):
            assert operation["responses"][error_status]["content"]["application/json"][
                "schema"
            ] == {"$ref": "#/components/schemas/ApiErrorResponse"}

    error_envelope = schema["components"]["schemas"]["ApiErrorResponse"]
    assert error_envelope["required"] == ["error"]
    assert error_envelope["additionalProperties"] is False
    error_detail = schema["components"]["schemas"]["ApiErrorDetailResponse"]
    assert error_detail["required"] == [
        "code",
        "message",
        "details",
        "retryable",
        "request_id",
    ]
    assert error_detail["additionalProperties"] is False


def test_agent_demo_is_disabled_by_default_without_blocking_case_reads(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case = _create_case(client, goal="synthetic warranty marker")

        state = client.get(f"/api/v1/cases/{case['id']}/agent-run")
        start = client.post(f"/api/v1/cases/{case['id']}/agent-run")
        restored = client.get(f"/api/v1/cases/{case['id']}")

    assert state.status_code == 200
    assert state.json() == {
        "demo_version": "portfolio_agent_demo.v1",
        "capability": {
            "status": "disabled",
            "provider": "disabled",
            "action": "Enable the deterministic Portfolio Agent Demo explicitly.",
            "supported_case_types": ["warranty"],
        },
        "run": None,
    }
    assert start.status_code == 409
    documented_error = ApiErrorResponse.model_validate(start.json())
    assert documented_error.error.code == "agent_demo_disabled"
    assert documented_error.error.retryable is False
    assert (
        start.json()["error"]
        | {
            "code": "agent_demo_disabled",
            "retryable": False,
        }
        == start.json()["error"]
    )
    assert restored.status_code == 200


def test_agent_demo_environment_opt_in_is_explicit_and_model_stays_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIFECASE_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("LIFECASE_PORTFOLIO_AGENT_DEMO", "true")
    monkeypatch.delenv("LIFECASE_MODEL_PROVIDER", raising=False)

    config = ApplicationConfig.from_environment()

    assert config.portfolio_agent_demo_enabled is True
    assert config.model_provider.provider is ModelProviderKind.DISABLED

    monkeypatch.setenv("LIFECASE_PORTFOLIO_AGENT_DEMO", "not-a-boolean")
    with pytest.raises(ValueError, match="must be a boolean"):
        ApplicationConfig.from_environment()


def test_explicit_evaluation_provider_is_not_part_of_the_http_composition(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case = _create_case(client, goal="synthetic provider isolation")

    provider = _FailingEvaluationProvider()
    connection = connect_database(config.database_path)
    try:
        service = PortfolioAgentDemoService(
            connection,
            enabled=True,
            evaluation_provider=provider,
        )
        service.create(str(case["id"]))
        with pytest.raises(AgentProviderFailureError):
            asyncio.run(service.execute(str(case["id"])))
    finally:
        connection.close()

    assert provider.calls == 1

    with TestClient(create_app(config)) as client:
        state = client.get(f"/api/v1/cases/{case['id']}/agent-run")

    assert state.status_code == 200
    assert state.json()["capability"]["provider"] == "disabled"
    assert state.json()["run"]["status"] == "needs_review"


def test_agent_demo_completes_seven_stages_and_recovers_after_restart(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, enabled=True)
    with TestClient(create_app(config)) as client:
        case = _create_case(client, goal="warranty marker alpha-7391")
        _upload_demo_evidence(client, case["id"])

        created = client.post(f"/api/v1/cases/{case['id']}/agent-run")
        completed = client.post(f"/api/v1/cases/{case['id']}/agent-run/execute")

    assert created.status_code == 201
    assert created.json()["run"]["status"] == "queued"
    assert completed.status_code == 200
    run = completed.json()["run"]
    assert run["status"] == "completed"
    assert [stage["name"] for stage in run["stages"]] == [
        "extract",
        "normalize",
        "link_claims",
        "classify_case",
        "check_gaps",
        "propose_actions",
        "validate",
    ]
    assert all(stage["status"] == "checkpointed" for stage in run["stages"])
    assert run["usage"]["calls"] == 7
    assert run["tool_calls"] == [
        {
            "name": "evidence_context.search.v1",
            "risk": "A",
            "call_count": 1,
            "result": "match",
        }
    ]
    assert run["answer"]["status"] == "answered"
    assert run["answer"]["citations"][0]["case_id"] == case["id"]
    assert any(claim["status"] == "candidate" for claim in run["claims"])
    assert any(claim["status"] == "inferred" for claim in run["claims"])
    assert all(claim["created_by"] == "agent" for claim in run["claims"])
    assert "checkpoint_json" not in completed.text.lower()
    assert "object_path" not in completed.text.lower()

    with TestClient(create_app(config)) as restarted:
        recovered = restarted.get(f"/api/v1/cases/{case['id']}/agent-run")

    assert recovered.status_code == 200
    assert recovered.json() == completed.json()


def test_deterministic_agent_demo_usage_and_local_latency_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record safe aggregate evidence for the fixed, offline demo path."""

    original_connect = socket.socket.connect

    def loopback_only_connect(instance: socket.socket, address: object) -> object:
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(instance, address)
        raise AssertionError("external network access is forbidden in deterministic evidence")

    monkeypatch.setattr(socket.socket, "connect", loopback_only_connect)

    sample_count = 5
    per_run_ceiling_seconds = 5.0
    aggregate_ceiling_seconds = sample_count * per_run_ceiling_seconds
    elapsed_samples: list[float] = []
    config = _config(tmp_path, enabled=True)

    with TestClient(create_app(config)) as client:
        for _ in range(sample_count):
            case = _create_case(client, goal="warranty marker alpha-7391")
            _upload_demo_evidence(client, case["id"])
            assert client.post(f"/api/v1/cases/{case['id']}/agent-run").status_code == 201

            started = perf_counter()
            completed = client.post(f"/api/v1/cases/{case['id']}/agent-run/execute")
            elapsed_samples.append(perf_counter() - started)

            assert completed.status_code == 200
            run = completed.json()["run"]
            assert run["status"] == "completed"
            assert run["budget"] == {
                "max_calls": 14,
                "max_input_tokens": 28_000,
                "max_output_tokens": 14_000,
                "max_total_tokens": 42_000,
                "max_cost_minor": 100,
                "cost_currency": "CNY",
            }
            assert run["usage"] == {
                "calls": 7,
                "input_tokens": 70,
                "output_tokens": 35,
                "total_tokens": 105,
                "cost_minor": 0,
                "cost_currency": "CNY",
            }

    aggregate_seconds = sum(elapsed_samples)
    assert max(elapsed_samples) <= per_run_ceiling_seconds
    assert aggregate_seconds <= aggregate_ceiling_seconds
    print(
        json.dumps(
            {
                "aggregate_ceiling_seconds": aggregate_ceiling_seconds,
                "aggregate_seconds": round(aggregate_seconds, 6),
                "max_seconds": round(max(elapsed_samples), 6),
                "median_seconds": round(median(elapsed_samples), 6),
                "per_run_ceiling_seconds": per_run_ceiling_seconds,
                "runs": sample_count,
            },
            sort_keys=True,
        )
    )


def test_agent_demo_no_match_changes_rag_decision_to_unknown(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=True)
    with TestClient(create_app(config)) as client:
        case = _create_case(client, goal="zeta-no-match-0000")
        _upload_demo_evidence(client, case["id"])
        assert client.post(f"/api/v1/cases/{case['id']}/agent-run").status_code == 201

        completed = client.post(f"/api/v1/cases/{case['id']}/agent-run/execute")

    assert completed.status_code == 200
    run = completed.json()["run"]
    assert run["answer"] == {
        "status": "unknown",
        "statements": [],
        "citations": [],
        "uncertainty_reason": "No authoritative Case evidence matched the Case goal.",
        "fact_references": [],
    }
    assert run["tool_calls"][0]["result"] == "no_match"


def test_agent_demo_cancels_queued_run_and_rejects_duplicate_commands(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, enabled=True)
    with TestClient(create_app(config)) as client:
        case = _create_case(client, goal="synthetic cancellation")
        created = client.post(f"/api/v1/cases/{case['id']}/agent-run")
        duplicate = client.post(f"/api/v1/cases/{case['id']}/agent-run")
        cancelled = client.post(f"/api/v1/cases/{case['id']}/agent-run/cancel")
        execute = client.post(f"/api/v1/cases/{case['id']}/agent-run/execute")

    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "agent_run_exists"
    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["status"] == "cancelled"
    assert execute.status_code == 409
    assert execute.json()["error"]["code"] == "agent_run_not_executable"


def test_agent_demo_rejects_case_types_without_a_reviewed_extraction_schema(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, enabled=True)
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/api/v1/cases",
            json={
                "title": "Synthetic refund",
                "goal": "review a return",
                "template_id": "return_refund",
            },
        )
        case = response.json()
        started = client.post(f"/api/v1/cases/{case['id']}/agent-run")

    assert started.status_code == 409
    assert started.json()["error"]["code"] == "agent_demo_case_unsupported"


def test_interrupted_run_recovers_then_uses_its_single_retry(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=True)
    with TestClient(create_app(config)) as client:
        case = _create_case(client, goal="warranty marker alpha-7391")
        _upload_demo_evidence(client, case["id"])
        assert client.post(f"/api/v1/cases/{case['id']}/agent-run").status_code == 201

    connection = connect_database(config.database_path)
    try:
        run, stage_input = _queued_extraction(connection, str(case["id"]))
        runner = StageRunner(
            connection,
            _empty_fake_provider(),
            registry=EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
            checkpoint_handler=EvidenceBackedRagCheckpointHandler(),
            failure_injector=lambda event: (
                _raise_interruption() if event == "after_stage_reservation" else None
            ),
        )
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            asyncio.run(
                runner.execute_stage(
                    ExecuteStageCommand(
                        run_id=run.id,
                        expected_run_version=run.version,
                        expected_stage_version=None,
                        stage_input=stage_input,
                        stage_budget=StageBudget(),
                        reservation=CallReservation(64, 64, 0),
                    )
                )
            )
    finally:
        connection.close()

    with TestClient(create_app(config)) as restarted:
        interrupted = restarted.get(f"/api/v1/cases/{case['id']}/agent-run")
        recovered = restarted.post(f"/api/v1/cases/{case['id']}/agent-run/recover")
        completed = restarted.post(f"/api/v1/cases/{case['id']}/agent-run/retry")

    assert interrupted.json()["run"]["recovery_required"] is True
    assert (
        recovered.json()["run"]
        | {
            "status": "needs_review",
            "can_retry": True,
        }
        == recovered.json()["run"]
    )
    assert completed.status_code == 200
    assert completed.json()["run"]["status"] == "completed"
    assert completed.json()["run"]["stages"][0]["calls"] == 2


def test_nonretryable_failure_and_tampered_checkpoint_fail_closed(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=True)
    with TestClient(create_app(config)) as client:
        case = _create_case(client, goal="warranty marker alpha-7391")
        _upload_demo_evidence(client, case["id"])
        assert client.post(f"/api/v1/cases/{case['id']}/agent-run").status_code == 201

    connection = connect_database(config.database_path)
    try:
        run, stage_input = _queued_extraction(connection, str(case["id"]))
        runner = StageRunner(
            connection,
            _empty_fake_provider(),
            registry=EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
            checkpoint_handler=EvidenceBackedRagCheckpointHandler(),
        )
        with pytest.raises(AgentProviderFailureError):
            asyncio.run(
                runner.execute_stage(
                    ExecuteStageCommand(
                        run_id=run.id,
                        expected_run_version=run.version,
                        expected_stage_version=None,
                        stage_input=stage_input,
                        stage_budget=StageBudget(),
                        reservation=CallReservation(64, 64, 0),
                    )
                )
            )
    finally:
        connection.close()

    with TestClient(create_app(config)) as client:
        failed = client.get(f"/api/v1/cases/{case['id']}/agent-run")
        retry = client.post(f"/api/v1/cases/{case['id']}/agent-run/retry")

    assert (
        failed.json()["run"]
        | {
            "status": "needs_review",
            "can_retry": False,
        }
        == failed.json()["run"]
    )
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "agent_run_not_executable"

    other_config = _config(tmp_path / "tampered", enabled=True)
    with TestClient(create_app(other_config)) as client:
        other_case = _create_case(client, goal="warranty marker alpha-7391")
        _upload_demo_evidence(client, other_case["id"])
        client.post(f"/api/v1/cases/{other_case['id']}/agent-run")
        completed = client.post(f"/api/v1/cases/{other_case['id']}/agent-run/execute")
        assert completed.status_code == 200

    connection = connect_database(other_config.database_path)
    try:
        connection.execute(
            "UPDATE agent_stage_run SET checkpoint_json = '{}' WHERE stage = 'validate'"
        )
    finally:
        connection.close()
    with TestClient(create_app(other_config)) as client:
        rejected = client.get(f"/api/v1/cases/{other_case['id']}/agent-run")
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "agent_integrity_failure"


def _config(tmp_path: Path, *, enabled: bool = False) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "lifecase.sqlite3",
        data_root=tmp_path / "data",
        upload_root=tmp_path / "uploads",
        portfolio_agent_demo_enabled=enabled,
    )


def _create_case(client: TestClient, *, goal: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/cases",
        json={
            "title": "Synthetic Portfolio Agent Demo",
            "goal": goal,
            "template_id": "warranty",
        },
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def _upload_demo_evidence(client: TestClient, case_id: object) -> None:
    response = client.post(
        f"/api/v1/cases/{case_id}/sources",
        files={
            "file": (
                "synthetic-warranty.txt",
                (
                    "Merchant Alpha Store. Product SynthPhone. Order ORD-001. "
                    "Paid CNY 259.00. Serial prefix SYN. Warranty marker alpha-7391 "
                    "lasts twenty-four months."
                ),
                "text/plain",
            )
        },
    )
    assert response.status_code == 201


def _queued_extraction(
    connection: sqlite3.Connection,
    case_id: str,
) -> tuple[AgentRunRecord, StageInput]:
    reader = StageRunner(
        connection,
        _empty_fake_provider(),
        registry=EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
        checkpoint_handler=EvidenceBackedRagCheckpointHandler(),
    )
    row = connection.execute("SELECT id FROM agent_run WHERE case_id = ?", (case_id,)).fetchone()
    assert row is not None
    run = reader.get_run(case_id, str(row["id"]))
    evidence_rows = connection.execute(
        """
        SELECT s.id, ss.id, ss.sequence, ss.page, ss.text, ss.text_sha256
        FROM source AS s JOIN source_segment AS ss ON ss.source_id = s.id
        WHERE s.case_id = ? ORDER BY ss.sequence, ss.id
        """,
        (case_id,),
    ).fetchall()
    evidence = tuple(
        ExtractionEvidenceSegment(
            case_id=case_id,
            source_id=str(item[0]),
            segment_id=str(item[1]),
            sequence=int(item[2]),
            page=None if item[3] is None else int(item[3]),
            text=str(item[4]),
            text_sha256=str(item[5]),
        )
        for item in evidence_rows
    )
    return run, warranty_extraction_stage_input(case_id, evidence)


def _empty_fake_provider() -> FakeModelProvider:
    return FakeModelProvider(
        ModelProviderConfig(provider=ModelProviderKind.FAKE),
        (),
    )


def _raise_interruption() -> None:
    raise RuntimeError("synthetic interruption")


class _FailingEvaluationProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResult:
        self.calls += 1
        raise ProviderNetworkError(
            "Synthetic evaluation provider is unavailable",
            code=ProviderErrorCode.NETWORK,
            retryable=True,
        )
