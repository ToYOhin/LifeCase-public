"""S1 production simplification through real HTTP, adapters, and durable state."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from lifecase.agent import ExtractionEvidenceSegment
from lifecase.agent.demo import (
    PORTFOLIO_AGENT_DEMO_VERSION,
    PortfolioAgentDemoService,
    _answer_checkpoint,
    _extraction_checkpoint,
)
from lifecase.agent.errors import AgentIntegrityError, AgentSequenceError, AgentValidationError
from lifecase.agent.live import LiveAgentService
from lifecase.agent.models import (
    AgentRunRecord,
    AgentStageName,
    CallReservation,
    ExecuteStageCommand,
    StageBudget,
    StageInput,
    stage_input_scope_sha256,
)
from lifecase.agent.rag import (
    EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
    TWO_STAGE_RAG_REGISTRY,
    EvidenceBackedRagCheckpointHandler,
)
from lifecase.agent.service import StageRunner
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.providers import (
    ModelProviderConfig,
    ModelProviderKind,
    OpenAIResponsesProvider,
    OpenAIResponsesResource,
    ProviderRuntimeClients,
    canonical_json_object,
)
from lifecase.storage import connect_database

_LEGACY_STAGES = [
    "extract",
    "normalize",
    "link_claims",
    "classify_case",
    "check_gaps",
    "propose_actions",
    "validate",
]


class _Responses:
    """Deterministic transport only; the application still uses its real adapter."""

    def __init__(self, *, fail_validate: type[Exception] | None = None) -> None:
        self.stages: list[str] = []
        self.fail_validate = fail_validate

    def create(self, **kwargs: Any) -> Awaitable[Any]:
        input_json = cast(str, kwargs["input"][1]["content"][0]["text"])
        stage_input = json.loads(input_json)
        properties = kwargs["text"]["format"]["schema"]["properties"]
        stage = cast(str, properties["stage"]["const"])
        self.stages.append(stage)

        async def execute() -> dict[str, object]:
            if stage == "validate" and self.fail_validate is not None:
                failure = self.fail_validate
                self.fail_validate = None
                raise failure("synthetic validate transport interruption")
            if stage == "extract":
                checkpoint = _extraction_checkpoint(
                    tuple(
                        ExtractionEvidenceSegment(**item)
                        for item in stage_input["stage_payload"]["evidence"]
                    )
                )
            elif stage == "validate":
                checkpoint = _answer_checkpoint(input_json)
            else:
                # Only the legacy fixture should ever request these outputs.
                checkpoint = {
                    "schema_version": "portfolio_agent_demo_stage.v1",
                    "demo_version": PORTFOLIO_AGENT_DEMO_VERSION,
                    "summary": f"{stage} completed without external action",
                    "read_only": True,
                }
            output = canonical_json_object(
                {
                    "stage": stage,
                    "stage_version": properties["stage_version"]["const"],
                    "checkpoint_schema_version": properties["checkpoint_schema_version"]["const"],
                    "checkpoint": checkpoint,
                },
                maximum_bytes=65_536,
            )
            return {
                "status": "completed",
                "model": "gpt-s1-synthetic",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": output}]}
                ],
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
            }

        return execute()


class _Client:
    def __init__(self, *, fail_validate: type[Exception] | None = None) -> None:
        self.resource = _Responses(fail_validate=fail_validate)
        self.responses: OpenAIResponsesResource = self.resource


def test_new_live_run_uses_only_two_calls_and_restores_its_evidence_results(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    boundary = _Client()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        _authorize(client, case_id)
        completed = client.post(f"/api/v1/cases/{case_id}/live-agent/execute")
        assert completed.status_code == 200, completed.text
        run = completed.json()["run"]
        assert run["status"] == "completed"
        assert run["stage_order"] == ["extract", "validate"]
        assert [stage["name"] for stage in run["stages"]] == ["extract", "validate"]
        assert all(stage["status"] == "checkpointed" for stage in run["stages"])
        assert run["usage"]["calls"] == 2
        assert run["usage"]["total_tokens"] == 60
        assert run["claims"]
        assert {claim["status"] for claim in run["claims"]} == {"candidate", "inferred"}
        assert all(claim["created_by"] == "agent" for claim in run["claims"])
        assert run["answer"]["status"] == "answered"
        assert run["answer"]["citations"]
        assert all(item["case_id"] == case_id for item in run["answer"]["citations"])
        assert client.get(f"/api/v1/cases/{case_id}/actions").json() == []
        assert client.get(f"/api/v1/cases/{case_id}/deadlines").json() == []

    assert boundary.resource.stages == ["extract", "validate"]
    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        restored = restarted.get(f"/api/v1/cases/{case_id}/live-agent")
    assert restored.status_code == 200
    assert restored.json()["run"] == run
    assert boundary.resource.stages == ["extract", "validate"]


@pytest.mark.parametrize("failure", [ConnectionError, TimeoutError])
def test_validate_failure_retries_only_validate_and_keeps_extraction(
    tmp_path: Path, failure: type[Exception]
) -> None:
    config = _config(tmp_path)
    boundary = _Client(fail_validate=failure)
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        _authorize(client, case_id)
        failed = client.post(f"/api/v1/cases/{case_id}/live-agent/execute")
        assert failed.status_code == 503, failed.text
        stopped = client.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"]
        assert stopped["status"] == "needs_review"
        assert stopped["can_retry"] is True
        assert [stage["name"] for stage in stopped["stages"]] == ["extract", "validate"]
        assert stopped["stages"][0]["status"] == "checkpointed"
        assert stopped["claims"]
        assert stopped["answer"] is None

    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        completed = restarted.post(f"/api/v1/cases/{case_id}/live-agent/retry")
        assert completed.status_code == 200, completed.text
        run = completed.json()["run"]
        assert run["status"] == "completed"
        assert run["claims"] == stopped["claims"]
        assert [stage["calls"] for stage in run["stages"]] == [1, 2]
        assert run["usage"]["calls"] == 3
        assert run["answer"]["status"] == "answered"
        assert restarted.post(f"/api/v1/cases/{case_id}/live-agent/retry").status_code == 409
    assert boundary.resource.stages == ["extract", "validate", "validate"]


def test_new_live_run_can_cancel_before_execution_and_remains_cancelled_after_restart(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    boundary = _Client()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        _authorize(client, case_id)
        cancelled = client.post(f"/api/v1/cases/{case_id}/live-agent/cancel")
        assert cancelled.status_code == 200
        run = cancelled.json()["run"]
        assert run["status"] == "cancelled"
        assert run["stages"] == []
        assert run["usage"]["calls"] == 0

    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        assert restarted.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"] == run
        assert restarted.post(f"/api/v1/cases/{case_id}/live-agent/execute").status_code == 409
        assert restarted.post(f"/api/v1/cases/{case_id}/live-agent/retry").status_code == 409
    assert boundary.resource.stages == []


def test_extraction_checkpoint_and_claims_rollback_together_then_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    boundary = _Client()
    original_commit = EvidenceBackedRagCheckpointHandler.commit

    def interrupt_commit(self: EvidenceBackedRagCheckpointHandler, **kwargs: Any) -> None:
        original_commit(self, **kwargs)
        raise RuntimeError("synthetic interruption after claim inserts")

    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        _authorize(client, case_id)
        with monkeypatch.context() as patch:
            patch.setattr(EvidenceBackedRagCheckpointHandler, "commit", interrupt_commit)
            failed = client.post(f"/api/v1/cases/{case_id}/live-agent/execute")
        assert failed.status_code == 503, failed.text
        interrupted = client.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"]
        assert interrupted["recovery_required"] is True
        assert interrupted["claims"] == []
        assert interrupted["last_completed_stage"] is None
        assert interrupted["stages"][0]["status"] == "running"

    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        recovered = restarted.post(f"/api/v1/cases/{case_id}/live-agent/recover")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["run"]["can_retry"] is True
        completed = restarted.post(f"/api/v1/cases/{case_id}/live-agent/retry")
        assert completed.status_code == 200, completed.text
        run = completed.json()["run"]
        assert run["status"] == "completed"
        assert run["claims"]
        assert len({claim["id"] for claim in run["claims"]}) == len(run["claims"])
        assert [stage["name"] for stage in run["stages"]] == ["extract", "validate"]
        assert [stage["calls"] for stage in run["stages"]] == [2, 1]
    assert boundary.resource.stages == ["extract", "extract", "validate"]


def test_completed_legacy_live_run_is_readable_and_new_run_does_not_rewrite_it(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    boundary = _Client()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)

    # Generate authentic seven-stage rows through the prior composition, never SQL updates.
    legacy_id = "portfolio-live-" + "1" * 64
    connection = connect_database(config.database_path)
    try:
        legacy = PortfolioAgentDemoService(
            connection,
            enabled=True,
            evaluation_provider=OpenAIResponsesProvider(config.model_provider, boundary),
            run_id_prefix="portfolio-live-",
            allow_new_runs=True,
        )
        legacy.create(case_id, run_id=legacy_id)
        asyncio.run(legacy.execute(case_id))
        before = _legacy_rows(connection, legacy_id)
    finally:
        connection.close()
    assert boundary.resource.stages == _LEGACY_STAGES

    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        prior = restarted.get(f"/api/v1/cases/{case_id}/live-agent")
        assert prior.status_code == 200, prior.text
        old_run = prior.json()["run"]
        assert old_run["id"] == legacy_id
        assert old_run["status"] == "completed"
        assert old_run["stage_order"] == _LEGACY_STAGES
        assert [stage["name"] for stage in old_run["stages"]] == _LEGACY_STAGES
        assert old_run["usage"]["calls"] == 7
        _authorize(restarted, case_id)
        completed = restarted.post(f"/api/v1/cases/{case_id}/live-agent/execute")
        assert completed.status_code == 200, completed.text
        assert completed.json()["run"]["id"] != legacy_id
        assert [item["id"] for item in completed.json()["run_history"]][1:] == [legacy_id]
        assert completed.json()["run_history"][1]["calls"] == 7
        assert completed.json()["run_history"][1]["stage_order"] == _LEGACY_STAGES
    assert boundary.resource.stages == [*_LEGACY_STAGES, "extract", "validate"]
    connection = connect_database(config.database_path)
    try:
        assert _legacy_rows(connection, legacy_id) == before
    finally:
        connection.close()


@pytest.mark.parametrize("interrupted", [False, True])
def test_unfinished_legacy_live_run_keeps_its_authorized_seven_stage_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    config = _config(tmp_path)
    boundary = _Client()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)

    connection = connect_database(config.database_path)
    try:
        old_preview = LiveAgentService(connection, config, ProviderRuntimeClients()).preview(
            case_id, legacy=True
        )
        legacy_id = "portfolio-live-" + old_preview.preview_sha256
        legacy = PortfolioAgentDemoService(
            connection,
            enabled=True,
            evaluation_provider=OpenAIResponsesProvider(config.model_provider, boundary),
            run_id_prefix="portfolio-live-",
            allow_new_runs=True,
            enable_retrieval_fallback=True,
        )
        legacy.create(case_id, run_id=legacy_id)
        if interrupted:
            original_inject = StageRunner._inject_failure

            def interrupt_after_reservation(self: StageRunner, event: str) -> None:
                original_inject(self, event)
                if event == "after_stage_reservation":
                    raise RuntimeError("synthetic old-process interruption")

            with monkeypatch.context() as patch:
                patch.setattr(StageRunner, "_inject_failure", interrupt_after_reservation)
                with pytest.raises(RuntimeError, match="synthetic old-process interruption"):
                    asyncio.run(legacy.execute(case_id))
    finally:
        connection.close()

    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        prior = restarted.get(f"/api/v1/cases/{case_id}/live-agent")
        assert prior.status_code == 200, prior.text
        assert prior.json()["run"]["id"] == legacy_id
        assert prior.json()["run"]["stage_order"] == _LEGACY_STAGES
        command = "execute"
        if interrupted:
            assert prior.json()["run"]["recovery_required"] is True
            recovered = restarted.post(f"/api/v1/cases/{case_id}/live-agent/recover")
            assert recovered.status_code == 200, recovered.text
            assert recovered.json()["run"]["can_retry"] is True
            command = "retry"
        completed = restarted.post(f"/api/v1/cases/{case_id}/live-agent/{command}")
        assert completed.status_code == 200, completed.text
        run = completed.json()["run"]
        assert run["id"] == legacy_id
        assert run["status"] == "completed"
        assert run["stage_order"] == _LEGACY_STAGES
        assert [stage["name"] for stage in run["stages"]] == _LEGACY_STAGES
        assert run["usage"]["calls"] == (8 if interrupted else 7)
        assert run["claims"]
        assert run["answer"]["status"] == "answered"
    assert boundary.resource.stages == _LEGACY_STAGES


@pytest.mark.parametrize(
    "invalid_input",
    ["legacy_registry_for_v2", "incorrect_hash", "six_hashes_for_v2", "single_hash_for_legacy"],
)
def test_validate_rejects_wrong_registry_or_chain_before_calls_or_reservation(
    tmp_path: Path, invalid_input: str
) -> None:
    config = _config(tmp_path)
    boundary = _Client()
    legacy_run = invalid_input == "single_hash_for_legacy"
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)
        if not legacy_run:
            _authorize(client, case_id)
            run_id = str(client.get(f"/api/v1/cases/{case_id}/live-agent").json()["run"]["id"])

    connection = connect_database(config.database_path)
    try:
        service = PortfolioAgentDemoService(
            connection, enabled=True, run_id_prefix="portfolio-live-", allow_new_runs=True
        )
        if legacy_run:
            old_preview = LiveAgentService(connection, config, ProviderRuntimeClients()).preview(
                case_id, legacy=True
            )
            run_id = "portfolio-live-" + old_preview.preview_sha256
            service.create(case_id, run_id=run_id)
        registry = EVIDENCE_BACKED_RAG_STAGE_REGISTRY if legacy_run else TWO_STAGE_RAG_REGISTRY
        provider = OpenAIResponsesProvider(config.model_provider, boundary)
        reader = StageRunner(
            connection,
            provider,
            registry=registry,
            checkpoint_handler=EvidenceBackedRagCheckpointHandler(),
        )
        run = reader.get_run(case_id, run_id)
        preceding_stages = _LEGACY_STAGES[:-1] if legacy_run else ["extract"]
        for name in preceding_stages:
            run = _execute_one_stage(
                reader, run, service._stage_input(case_id, run, AgentStageName(name))
            )
        valid_input = service._stage_input(case_id, run, AgentStageName.VALIDATE)
        payload = json.loads(valid_input.payload_json)
        extract_hash = payload["prior_checkpoint_hashes"][0]
        selected_registry = registry
        if invalid_input == "legacy_registry_for_v2":
            selected_registry = EVIDENCE_BACKED_RAG_STAGE_REGISTRY
        elif invalid_input == "incorrect_hash":
            payload["prior_checkpoint_hashes"] = ["0" * 64]
        elif invalid_input == "six_hashes_for_v2":
            payload["prior_checkpoint_hashes"] = [extract_hash] * 6
        else:
            payload["prior_checkpoint_hashes"] = [extract_hash]
        definition = selected_registry.get(AgentStageName.VALIDATE)
        payload_json = canonical_json_object(payload, maximum_bytes=100_000)
        # Exercise the low-level boundary directly: callers can construct a valid hash
        # without using the stricter RAG input helper.
        supplied = StageInput(
            case_id=case_id,
            stage=AgentStageName.VALIDATE,
            stage_version=definition.stage_version,
            input_schema_version=definition.input_schema_version,
            input_scope_sha256=stage_input_scope_sha256(
                case_id,
                AgentStageName.VALIDATE,
                definition.stage_version,
                definition.input_schema_version,
                payload_json,
            ),
            payload_json=payload_json,
        )
        before_run = tuple(
            connection.execute("SELECT * FROM agent_run WHERE id = ?", (run_id,)).fetchone()
        )
        before_stages = _legacy_rows(connection, run_id)
        rejecting_runner = StageRunner(
            connection,
            provider,
            registry=selected_registry,
            checkpoint_handler=EvidenceBackedRagCheckpointHandler(),
        )
        with pytest.raises((AgentValidationError, AgentSequenceError, AgentIntegrityError)):
            _execute_one_stage(rejecting_runner, run, supplied)
        assert boundary.resource.stages == preceding_stages
        assert (
            tuple(connection.execute("SELECT * FROM agent_run WHERE id = ?", (run_id,)).fetchone())
            == before_run
        )
        assert _legacy_rows(connection, run_id) == before_stages
        assert reader.get_run(case_id, run_id) == run
    finally:
        connection.close()

    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        restored = restarted.get(f"/api/v1/cases/{case_id}/live-agent")
        assert restored.status_code == 200, restored.text
        assert restored.json()["run"]["status"] == "checkpointed"
        completed = restarted.post(f"/api/v1/cases/{case_id}/live-agent/execute")
        assert completed.status_code == 200, completed.text
        assert completed.json()["run"]["status"] == "completed"
    assert boundary.resource.stages == [*preceding_stages, "validate"]


def test_legacy_extract_checkpoint_survives_restart_and_next_call_is_normalize(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    boundary = _Client()
    with TestClient(create_app(config, openai_client=boundary)) as client:
        case_id = _create_case_with_evidence(client)

    connection = connect_database(config.database_path)
    try:
        old_preview = LiveAgentService(connection, config, ProviderRuntimeClients()).preview(
            case_id, legacy=True
        )
        run_id = "portfolio-live-" + old_preview.preview_sha256
        service = PortfolioAgentDemoService(
            connection, enabled=True, run_id_prefix="portfolio-live-", allow_new_runs=True
        )
        created = service.create(case_id, run_id=run_id)
        assert created.run is not None
        reader = StageRunner(
            connection,
            OpenAIResponsesProvider(config.model_provider, boundary),
            registry=EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
            checkpoint_handler=EvidenceBackedRagCheckpointHandler(),
        )
        run = _execute_one_stage(
            reader,
            created.run,
            service._stage_input(case_id, created.run, AgentStageName.EXTRACT),
        )
        assert run.last_completed_stage is AgentStageName.EXTRACT
        extraction_row = _legacy_rows(connection, run_id)[0]
    finally:
        connection.close()
    assert boundary.resource.stages == ["extract"]

    with TestClient(create_app(config, openai_client=boundary)) as restarted:
        restored = restarted.get(f"/api/v1/cases/{case_id}/live-agent")
        assert restored.status_code == 200, restored.text
        assert restored.json()["run"]["status"] == "checkpointed"
        assert restored.json()["run"]["stage_order"] == _LEGACY_STAGES
        completed = restarted.post(f"/api/v1/cases/{case_id}/live-agent/execute")
        assert completed.status_code == 200, completed.text
        assert completed.json()["run"]["status"] == "completed"
        assert completed.json()["run"]["stages"][0]["calls"] == 1
    assert boundary.resource.stages == _LEGACY_STAGES
    connection = connect_database(config.database_path)
    try:
        assert _legacy_rows(connection, run_id)[0] == extraction_row
    finally:
        connection.close()


def _execute_one_stage(
    runner: StageRunner, run: AgentRunRecord, stage_input: StageInput
) -> AgentRunRecord:
    return asyncio.run(
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


def _legacy_rows(connection: sqlite3.Connection, run_id: str) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT * FROM agent_stage_run WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
    ]


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "lifecase.sqlite3",
        data_root=tmp_path / "data",
        upload_root=tmp_path / "uploads",
        model_provider=ModelProviderConfig(
            provider=ModelProviderKind.OPENAI, model_id="gpt-s1-synthetic"
        ),
        live_agent_enabled=True,
    )


def _create_case_with_evidence(client: TestClient) -> str:
    created = client.post(
        "/api/v1/cases",
        json={
            "title": "S1 Synthetic Warranty",
            "goal": "warranty marker alpha-7391",
            "template_id": "warranty",
        },
    )
    assert created.status_code == 201
    case_id = str(created.json()["id"])
    uploaded = client.post(
        f"/api/v1/cases/{case_id}/sources",
        files={
            "file": (
                "synthetic-s1.txt",
                "Merchant Alpha Store. Product SynthPhone. Order ORD-S1. "
                "Purchase date 2026-07-15. Paid CNY 259.00. Serial prefix SYN. "
                "Warranty marker alpha-7391 lasts twenty-four months.",
                "text/plain",
            )
        },
    )
    assert uploaded.status_code == 201
    return case_id


def _authorize(client: TestClient, case_id: str) -> None:
    preview = client.get(f"/api/v1/cases/{case_id}/live-agent")
    assert preview.status_code == 200, preview.text
    created = client.post(
        f"/api/v1/cases/{case_id}/live-agent",
        json={
            "preview_sha256": preview.json()["preview"]["preview_sha256"],
            "confirm_read_only": True,
        },
    )
    assert created.status_code == 201, created.text
