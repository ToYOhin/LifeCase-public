"""Durable question inputs remain immutable, scoped, and charged on stale completion."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lifecase.agent.errors import (
    AgentErrorCode,
    AgentIntegrityError,
    AgentSequenceError,
    AgentValidationError,
)
from lifecase.agent.models import (
    AgentStageName,
    CallReservation,
    CancelAgentRunCommand,
    CreateAgentRunCommand,
    ExecuteStageCommand,
    StageBudget,
    StageInput,
    stage_input_scope_sha256,
    stage_order_for_run,
)
from lifecase.agent.registry import BUILT_IN_STAGE_REGISTRY, StageRegistry
from lifecase.agent.service import StageRunner, stage_call_scope_sha256
from lifecase.cases import CaseService
from lifecase.providers import (
    FakeModelProvider,
    FakeProviderScript,
    ModelProviderConfig,
    ModelProviderKind,
    ProviderUsage,
    canonical_json_object,
)
from lifecase.storage import MIGRATIONS, apply_migrations, connect_database
from lifecase.storage.backup import BackupIntegrityError, BackupService, verify_backup
from lifecase.storage.backup.service import _verify_database


def _binding(case_id: str, question: str = "目前还缺什么材料？") -> str:
    basis: dict[str, object] = {"facts": {"case_id": case_id, "items": []}, "sources": []}
    return canonical_json_object(
        {
            "schema_version": "live_agent_question.v1",
            "case_id": case_id,
            "question": question,
            "retrieval_query": "材料 OR warranty",
            "basis": basis,
            "basis_sha256": _sha(canonical_json_object(basis, maximum_bytes=100_000)),
            "authorization_sha256": "a" * 64,
        },
        maximum_bytes=100_000,
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture
def database(tmp_path: Path) -> Any:
    connection = connect_database(tmp_path / "question.sqlite3")
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic question storage", goal="Original goal", template_id="warranty"
    )
    try:
        yield connection, case.id
    finally:
        connection.close()


def _guard(monkeypatch: pytest.MonkeyPatch, callback: Any) -> None:
    module = ModuleType("lifecase.agent.question_context")
    module.require_current_binding = callback  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)


def _runner(connection: sqlite3.Connection) -> StageRunner:
    return StageRunner(
        connection, FakeModelProvider(ModelProviderConfig(provider=ModelProviderKind.FAKE), ())
    )


def _command(case_id: str, binding: str, suffix: str = "synthetic") -> CreateAgentRunCommand:
    return CreateAgentRunCommand(
        run_id=f"portfolio-live-q1-{suffix}",
        case_id=case_id,
        allow_case_history=True,
        input_binding_json=binding,
        input_binding_sha256=_sha(binding),
    )


def test_question_binding_roundtrip_and_idempotency_preserve_original_question(
    database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, case_id = database
    checks: list[bool] = []
    _guard(monkeypatch, lambda conn, binding, scope: checks.append(conn.in_transaction))
    runner = _runner(connection)
    command = _command(case_id, _binding(case_id))
    created = runner.create_run(command)
    assert created.input_binding_json == command.input_binding_json
    assert created.input_binding_sha256 == command.input_binding_sha256
    assert runner.create_run(command) == created
    assert runner.get_run(case_id, created.id) == created
    assert checks and all(checks)
    assert CaseService(connection).get_case(case_id).goal == "Original goal"
    with pytest.raises(AgentSequenceError):
        runner.create_run(_command(case_id, _binding(case_id, "另一个问题？")))
    assert runner.get_run(case_id, created.id) == created


def test_question_run_has_only_validate_and_legacy_orders_remain() -> None:
    assert stage_order_for_run("portfolio-live-q1-example") == (AgentStageName.VALIDATE,)
    assert len(stage_order_for_run("portfolio-live-v2-example")) == 2
    assert len(stage_order_for_run("portfolio-live-example")) == 7
    StageRegistry((BUILT_IN_STAGE_REGISTRY.get(AgentStageName.VALIDATE),))


def test_only_terminal_case_run_allows_another_question_uuid_inside_create_transaction(
    database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, case_id = database
    _guard(monkeypatch, lambda *args: None)
    runner = _runner(connection)
    first_command = _command(case_id, _binding(case_id), "first-tab")
    first = runner.create_run(first_command)
    second_command = _command(case_id, _binding(case_id), "second-tab")
    with pytest.raises(AgentSequenceError):
        runner.create_run(second_command)
    assert not connection.in_transaction
    assert connection.execute("SELECT COUNT(*) FROM agent_run").fetchone()[0] == 1
    assert runner.get_run(case_id, first.id) == first
    cancelled = runner.request_cancellation(CancelAgentRunCommand(first.id, case_id, first.version))
    assert cancelled.status.value == "cancelled"
    second = runner.create_run(second_command)
    assert second.id != first.id
    assert second.status.value == "queued"
    assert runner.create_run(first_command) == cancelled
    assert runner.create_run(second_command) == second
    assert connection.execute("SELECT COUNT(*) FROM agent_run").fetchone()[0] == 2


@pytest.mark.parametrize("mode", ["wrong_case", "wrong_basis_hash", "extra", "untrimmed"])
def test_question_binding_rejects_invalid_input(mode: str) -> None:
    from lifecase.agent.question_binding import validate_question_binding

    value = json.loads(_binding("case_synthetic"))
    if mode == "wrong_case":
        value["basis"]["facts"]["case_id"] = "case_other"
        value["basis_sha256"] = _sha(canonical_json_object(value["basis"], maximum_bytes=100_000))
    elif mode == "wrong_basis_hash":
        value["basis_sha256"] = "b" * 64
    elif mode == "extra":
        value["raw_prompt"] = "not an allowed persisted field"
    else:
        value["question"] = "  question  "
    with pytest.raises(AgentValidationError):
        validate_question_binding(
            canonical_json_object(value, maximum_bytes=100_000), "case_synthetic"
        )


def test_question_binding_cannot_be_attached_to_legacy_or_omitted_from_new_run() -> None:
    with pytest.raises(AgentValidationError):
        CreateAgentRunCommand(run_id="portfolio-live-q1-missing", case_id="case_synthetic")
    binding = _binding("case_synthetic")
    with pytest.raises(AgentValidationError):
        CreateAgentRunCommand(
            run_id="portfolio-live-v2-legacy",
            case_id="case_synthetic",
            input_binding_json=binding,
            input_binding_sha256=_sha(binding),
        )


def test_reader_and_backup_reject_binding_tampering_but_allow_stale_history(
    database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, case_id = database
    _guard(monkeypatch, lambda *args: None)
    runner = _runner(connection)
    created = runner.create_run(_command(case_id, _binding(case_id)))
    _guard(monkeypatch, lambda *args: pytest.fail("Historical reads must not require fresh inputs"))
    assert runner.get_run(case_id, created.id) == created
    _verify_database(connection)
    connection.execute(
        "UPDATE agent_run SET input_binding_json = ? WHERE id = ?",
        (_binding(case_id, "Changed without updating hash"), created.id),
    )
    with pytest.raises(AgentIntegrityError):
        runner.get_run(case_id, created.id)
    with pytest.raises(BackupIntegrityError):
        _verify_database(connection)


def test_binding_migration_keeps_old_rows_and_backup_accepts_old_schema(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "legacy.sqlite3")
    try:
        apply_migrations(connection, MIGRATIONS[:17])
        case = CaseService(connection).create_case(
            title="Old run", goal="Old goal", template_id="warranty"
        )
        connection.execute(
            """INSERT INTO agent_run (
                id, case_id, status, version, max_calls, max_input_tokens,
                max_output_tokens, max_total_tokens, max_cost_minor, cost_currency,
                created_at, updated_at
            ) VALUES ('portfolio-live-v2-old', ?, 'queued', 1, 14, 28000, 14000,
                      42000, 100, 'CNY', '2026-09-19T00:00:00Z', '2026-09-19T00:00:00Z')""",
            (case.id,),
        )
        before = tuple(connection.execute("SELECT * FROM agent_run").fetchone())
        _verify_database(connection)
        apply_migrations(connection)
        after = tuple(connection.execute("SELECT * FROM agent_run").fetchone())
        assert after == (*before, None, None)
        _verify_database(connection)
    finally:
        connection.close()


def test_question_binding_survives_archive_restore_without_requiring_current_inputs(
    database: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, case_id = database
    _guard(monkeypatch, lambda *args: None)
    expected = _runner(connection).create_run(_command(case_id, _binding(case_id)))
    service = BackupService(connection, tmp_path / "question.sqlite3", tmp_path / "data")
    artifact = service.create()
    archive = service.get_download(artifact.id).path
    original = archive.read_bytes()
    assert verify_backup(archive).validation_id == artifact.id
    validation = service.validate(archive)
    _guard(
        monkeypatch, lambda *args: pytest.fail("Restoring history must not require fresh inputs")
    )
    service.apply(validation.validation_id, "question-copy", "RESTORE INTO NEW DIRECTORY")
    restored = connect_database(
        tmp_path / "recovery" / "restored" / "question-copy" / "lifecase.sqlite3"
    )
    try:
        assert _runner(restored).get_run(case_id, expected.id) == expected
    finally:
        restored.close()
    assert archive.read_bytes() == original


def test_stale_creation_leaves_no_run_or_transaction(
    database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, case_id = database

    def reject(*args: Any) -> None:
        raise AgentIntegrityError("Question inputs changed", code=AgentErrorCode.INTEGRITY)

    _guard(monkeypatch, reject)
    with pytest.raises(AgentIntegrityError):
        _runner(connection).create_run(_command(case_id, _binding(case_id)))
    assert not connection.in_transaction
    assert connection.execute("SELECT COUNT(*) FROM agent_run").fetchone()[0] == 0


def test_stale_completion_commits_no_checkpoint_and_charges_the_completed_call(
    database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, case_id = database
    checks = 0

    def guard(conn: sqlite3.Connection, binding: str, scope: str) -> None:
        nonlocal checks
        assert conn.in_transaction
        assert scope == case_id
        checks += 1
        if checks == 3:
            raise AgentIntegrityError("Question inputs changed", code=AgentErrorCode.INTEGRITY)

    _guard(monkeypatch, guard)
    definition = BUILT_IN_STAGE_REGISTRY.get(AgentStageName.VALIDATE)
    registry = StageRegistry((definition,))
    binding = _binding(case_id)
    run = _runner(connection).create_run(_command(case_id, binding))
    payload = canonical_json_object(
        {"case_id": case_id, "prior_checkpoint_hashes": [], "stage_payload": {}},
        maximum_bytes=100_000,
    )
    scope = stage_input_scope_sha256(
        case_id,
        definition.stage,
        definition.stage_version,
        definition.input_schema_version,
        payload,
    )
    stage_input = StageInput(
        case_id,
        definition.stage,
        definition.stage_version,
        definition.input_schema_version,
        scope,
        payload,
    )
    output = canonical_json_object(
        {
            "stage": "validate",
            "stage_version": definition.stage_version,
            "checkpoint_schema_version": definition.checkpoint_schema_version,
            "checkpoint": {"safe": True},
        },
        maximum_bytes=65_536,
    )
    provider = FakeModelProvider(
        ModelProviderConfig(provider=ModelProviderKind.FAKE),
        (
            FakeProviderScript(
                request_scope_sha256=stage_call_scope_sha256(run.id, definition.stage, scope, 1),
                output_json=output,
                usage=ProviderUsage(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                    cost_minor_units=0,
                    cost_currency="CNY",
                ),
            ),
        ),
    )
    runner = StageRunner(connection, provider, registry=registry)
    with pytest.raises(AgentIntegrityError):
        asyncio.run(
            runner.execute_stage(
                ExecuteStageCommand(
                    run.id,
                    run.version,
                    None,
                    stage_input,
                    StageBudget(),
                    CallReservation(64, 64, 0),
                )
            )
        )
    stopped = runner.get_run(case_id, run.id)
    assert stopped.status.value == "needs_review"
    assert stopped.usage.calls == 1
    assert stopped.usage.input_tokens == 10
    assert stopped.usage.output_tokens == 5
    assert stopped.usage.reserved_total_tokens == 0
    assert stopped.stages[0].checkpoint is None
    assert stopped.stages[0].error_code == AgentErrorCode.INTEGRITY.value
    assert stopped.stages[0].retryable is False
    assert stopped.input_binding_json == binding
