"""M4-03 deterministic Case Timeline and recovery coverage."""

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifecase.api.timeline as timeline_api
import lifecase.timeline.service as timeline_service
from lifecase.actions import (
    ActionKind,
    ActionPayload,
    ActionRiskLevel,
    ActionService,
    ActionStatusCommand,
    CreateActionCommand,
)
from lifecase.approvals import (
    ApprovalDecisionCommand,
    ApprovalService,
    ApprovalSummary,
    RequestApprovalCommand,
)
from lifecase.cases import CasePersistenceError
from lifecase.claims import (
    ClaimCreatedBy,
    ClaimService,
    ClaimStatus,
    ClaimValue,
    CreateClaimCommand,
)
from lifecase.config import ApplicationConfig
from lifecase.deadlines import (
    CreateSuggestedDeadlineCommand,
    DeadlineDate,
    DeadlineDateKind,
    DeadlineService,
    DeadlineStatusCommand,
)
from lifecase.main import create_app
from lifecase.storage import apply_migrations, connect_database
from lifecase.timeline import (
    TimelineCaseNotFoundError,
    TimelineEventKind,
    TimelineFeatureAvailability,
    TimelineIntegrityError,
    TimelinePersistenceError,
    TimelineService,
)

_T0 = "2026-08-02T00:00:00.000000Z"
_T1 = "2026-08-02T01:00:00.000000Z"
_T2 = "2026-08-02T02:00:00.000000Z"
_HASH = "0" * 64


def test_empty_and_missing_case_have_explicit_results(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_empty", version=1)

        result = TimelineService(connection).list_case_timeline("case_empty")

        assert result.events == ()
        assert (
            result.availability.approvals,
            result.availability.communications,
        ) == (
            TimelineFeatureAvailability.AVAILABLE,
            TimelineFeatureAvailability.AVAILABLE_AS_ACTION_PROJECTION,
        )
        with pytest.raises(TimelineCaseNotFoundError):
            TimelineService(connection).list_case_timeline("case_missing")
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_timeline_is_case_scoped_authoritative_ordered_and_read_only(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_one")
        _add_case(connection, "case_two", version=1)
        _add_source_bundle(connection, "case_one", "source_one", "artifact_one")
        _add_source_bundle(connection, "case_two", "source_two", "artifact_two")
        _add_transition(connection, "case_one", "transition_one")
        claim = ClaimService(connection).create_claim(
            CreateClaimCommand(
                case_id="case_one",
                field="order_number",
                value=ClaimValue.from_input("SYN-001"),
                status=ClaimStatus.CANDIDATE,
                confidence=0.9,
                created_by=ClaimCreatedBy.AGENT,
                actor="agent",
                reason="Synthetic extraction",
            )
        )
        connection.execute(
            "UPDATE claim_event SET occurred_at = ? WHERE claim_id = ?", (_T1, claim.id)
        )
        _add_agent(connection, "case_one", "run_one", "stage_one")
        before = _snapshot(connection)

        first = TimelineService(connection).list_case_timeline("case_one")
        second = TimelineService(connection).list_case_timeline("case_one")

        assert first == second
        assert [event.kind for event in first.events] == [
            TimelineEventKind.SOURCE_IMPORTED,
            TimelineEventKind.SOURCE_EXTRACTION_STATUS,
            TimelineEventKind.ARTIFACT_CREATED,
            TimelineEventKind.CASE_TRANSITIONED,
            TimelineEventKind.CLAIM_CREATED,
            TimelineEventKind.AGENT_RUN_STATUS,
            TimelineEventKind.AGENT_STAGE_STATUS,
        ]
        assert all(event.case_id == "case_one" for event in first.events)
        assert not any("two" in event.authority_id for event in first.events)
        source = first.events[0]
        assert (source.authority_id, source.label, source.status) == (
            "source_one",
            "synthetic-source_one.txt",
            "imported",
        )
        transition = first.events[3]
        assert (
            transition.prior_status,
            transition.status,
            transition.version,
            transition.actor,
            transition.reason,
        ) == ("intake", "needs_review", 2, "reviewer", "Synthetic transition")
        claim_event = first.events[4]
        assert (claim_event.subject_id, claim_event.label, claim_event.actor) == (
            claim.id,
            "order_number",
            "agent",
        )
        assert _snapshot(connection) == before
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_timeline_recovers_identically_after_database_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection = _database(tmp_path)
    _add_case(connection, "case_one", version=1)
    _add_source_bundle(connection, "case_one", "source_one", "artifact_one")
    before = TimelineService(connection).list_case_timeline("case_one")
    connection.close()

    restarted = connect_database(database_path)
    try:
        assert TimelineService(restarted).list_case_timeline("case_one") == before
    finally:
        restarted.close()


def test_timeline_supports_caller_owned_read_transaction(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_one", version=1)
        service = TimelineService(connection)
        with pytest.raises(TimelinePersistenceError):
            service.list_case_timeline_in_transaction("case_one")

        connection.execute("BEGIN")
        projected = service.list_case_timeline_in_transaction("case_one")
        assert connection.in_transaction is True
        connection.execute("COMMIT")

        assert projected == service.list_case_timeline("case_one")
    finally:
        connection.close()


def test_timeline_projects_only_committed_case_scoped_deadline_history(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_one", version=1)
        _add_case(connection, "case_two", version=1)
        deadlines = DeadlineService(connection)
        first = deadlines.create_suggested(
            CreateSuggestedDeadlineCommand(
                case_id="case_one",
                due=DeadlineDate(DeadlineDateKind.LOCAL_DATE, "2026-08-10", "UTC"),
                actor="local_user",
                reason="Synthetic Case one reminder",
            )
        )
        deadlines.confirm(
            DeadlineStatusCommand(
                deadline_id=first.id,
                expected_version=1,
                actor="local_user",
                reason="Confirm synthetic reminder",
            )
        )
        deadlines.create_suggested(
            CreateSuggestedDeadlineCommand(
                case_id="case_two",
                due=DeadlineDate(DeadlineDateKind.LOCAL_DATE, "2026-08-11", "UTC"),
                actor="other_user",
                reason="Synthetic Case two reminder",
            )
        )

        result = TimelineService(connection).list_case_timeline("case_one")

        assert [event.kind for event in result.events] == [
            TimelineEventKind.DEADLINE_CREATED,
            TimelineEventKind.DEADLINE_CONFIRMED,
        ]
        assert all(event.subject_id == first.id for event in result.events)
        assert [event.version for event in result.events] == [1, 2]
        assert not any(event.actor == "other_user" for event in result.events)
    finally:
        connection.close()


def test_timeline_projects_only_committed_case_scoped_action_history(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_one", version=1)
        _add_case(connection, "case_two", version=1)
        actions = ActionService(connection)
        first = actions.create_action(_collect_action("case_one", ActionRiskLevel.A))
        started = actions.start(
            ActionStatusCommand(
                first.id,
                first.version,
                "local_user",
                "Start committed synthetic Action",
            )
        )
        actions.create_action(_collect_action("case_two", ActionRiskLevel.B))

        result = TimelineService(connection).list_case_timeline("case_one")

        assert [event.kind for event in result.events] == [
            TimelineEventKind.ACTION_CREATED,
            TimelineEventKind.ACTION_STARTED,
        ]
        assert all(event.subject_id == first.id for event in result.events)
        assert [event.version for event in result.events] == [1, 2]
        assert [event.status for event in result.events] == ["ready", "running"]
        assert result.events[-1].actor == "local_user"
        assert started.version == 2
        assert not any(event.case_id == "case_two" for event in result.events)
    finally:
        connection.close()


def test_timeline_projects_committed_approval_history_after_action_history(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_one", version=1)
        action = ActionService(connection).create_action(
            _collect_action("case_one", ActionRiskLevel.B)
        )
        approvals = ApprovalService(connection)
        approval = approvals.request(
            RequestApprovalCommand(
                action.id,
                action.version,
                action.payload.sha256,
                ApprovalSummary.from_input("Synthetic Timeline Approval"),
                "local_label",
                "Request committed Approval",
            )
        )
        approvals.approve(
            ApprovalDecisionCommand(
                approval.id,
                action.id,
                approval.version,
                action.version,
                "local_label",
                "Approve committed Action",
            )
        )
        connection.execute(
            "UPDATE action_event SET occurred_at = CASE new_version "
            "WHEN 1 THEN ? ELSE ? END WHERE action_id = ?",
            (_T0, _T2, action.id),
        )
        connection.execute(
            "UPDATE approval_event SET occurred_at = CASE new_version "
            "WHEN 1 THEN ? ELSE ? END WHERE approval_id = ?",
            (_T1, _T2, approval.id),
        )

        result = TimelineService(connection).list_case_timeline("case_one")

        assert [event.kind for event in result.events] == [
            TimelineEventKind.ACTION_CREATED,
            TimelineEventKind.APPROVAL_REQUESTED,
            TimelineEventKind.ACTION_APPROVED,
            TimelineEventKind.APPROVAL_APPROVED,
        ]
        assert result.events[-1].parent_id == action.id
        assert result.availability.approvals is TimelineFeatureAvailability.AVAILABLE
    finally:
        connection.close()


def test_cross_case_artifact_reference_fails_closed(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_one", version=1)
        _add_case(connection, "case_two", version=1)
        _add_source_bundle(connection, "case_two", "source_two", "artifact_two")
        connection.execute(
            """
            INSERT INTO artifact (
                id, case_id, source_id, type, relative_path, sha256,
                size_bytes, created_by_run, created_at
            ) VALUES ('artifact_cross', 'case_one', 'source_two', 'derived_text',
                      'derived/cross.txt', ?, 1, NULL, ?)
            """,
            (_HASH, _T1),
        )

        with pytest.raises(TimelineIntegrityError):
            TimelineService(connection).list_case_timeline("case_one")

        assert connection.in_transaction is False
    finally:
        connection.close()


def test_corrupt_extraction_count_fails_closed(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_one", version=1)
        _add_source_bundle(connection, "case_one", "source_one", "artifact_one")
        connection.execute(
            "UPDATE source_extraction SET segment_count = 2 WHERE source_id = 'source_one'"
        )

        with pytest.raises(TimelineIntegrityError):
            TimelineService(connection).list_case_timeline("case_one")
    finally:
        connection.close()


def test_timeline_rejects_semantically_false_action_and_approval_events(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    connection = connect_database(config.database_path)
    apply_migrations(connection)
    _add_case(connection, "case_one", version=1)
    actions = ActionService(connection)
    action = actions.create_action(_collect_action("case_one", ActionRiskLevel.B))
    revalidated = actions.revalidate(
        ActionStatusCommand(
            action.id,
            action.version,
            "local_user",
            "Refresh exact synthetic Action authority",
        )
    )
    approvals = ApprovalService(connection)
    pending = approvals.request(
        RequestApprovalCommand(
            revalidated.id,
            revalidated.version,
            revalidated.payload.sha256,
            ApprovalSummary.from_input("Review exact revalidated Action"),
            "local_label",
            "Request synthetic semantic-integrity review",
        )
    )
    rejected = approvals.reject(
        ApprovalDecisionCommand(
            pending.id,
            revalidated.id,
            pending.version,
            revalidated.version,
            "local_label",
            "Reject exact synthetic Action",
        )
    )

    connection.execute(
        "UPDATE action_event SET event_type = 'cancelled' WHERE action_id = ? AND new_version = 2",
        (action.id,),
    )
    with TestClient(create_app(config)) as client:
        false_action = client.get("/api/v1/cases/case_one/timeline")
    assert (false_action.status_code, false_action.json()["error"]["code"]) == (
        409,
        "timeline_integrity_failed",
    )
    assert all(value not in false_action.text for value in ("cancelled", "SELECT", str(tmp_path)))

    connection.execute(
        "UPDATE action_event SET event_type = 'revalidated' "
        "WHERE action_id = ? AND new_version = 2",
        (action.id,),
    )
    connection.execute(
        "UPDATE approval_event SET event_type = 'expired' "
        "WHERE approval_id = ? AND new_version = 2",
        (rejected.id,),
    )
    with TestClient(create_app(config)) as client:
        false_approval = client.get("/api/v1/cases/case_one/timeline")
    assert (false_approval.status_code, false_approval.json()["error"]["code"]) == (
        409,
        "timeline_integrity_failed",
    )
    assert all(value not in false_approval.text for value in ("expired", "SELECT", str(tmp_path)))
    connection.close()


def test_missing_claim_or_case_history_fails_closed(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        _add_case(connection, "case_one", version=1)
        claim = ClaimService(connection).create_claim(
            CreateClaimCommand(
                case_id="case_one",
                field="order_number",
                value=ClaimValue.from_input("SYN-001"),
                status=ClaimStatus.CANDIDATE,
                confidence=0.9,
                created_by=ClaimCreatedBy.AGENT,
                actor="agent",
                reason="Synthetic extraction",
            )
        )
        connection.execute("DELETE FROM claim_event WHERE claim_id = ?", (claim.id,))

        with pytest.raises(TimelineIntegrityError):
            TimelineService(connection).list_case_timeline("case_one")

        connection.execute("DELETE FROM claim WHERE id = ?", (claim.id,))
        connection.execute("UPDATE case_record SET version = 2 WHERE id = 'case_one'")
        with pytest.raises(TimelineIntegrityError):
            TimelineService(connection).list_case_timeline("case_one")
    finally:
        connection.close()


def test_closed_database_fails_without_private_details(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    service = TimelineService(connection)
    connection.close()

    with pytest.raises(TimelinePersistenceError) as captured:
        service.list_case_timeline("case_one")

    assert "SELECT" not in str(captured.value)
    assert str(tmp_path) not in str(captured.value)


def test_wrapped_domain_storage_failure_remains_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _database(tmp_path)
    _add_case(connection, "case_one", version=1)

    def fail_history(*_args: object, **_kwargs: object) -> None:
        raise CasePersistenceError("private storage detail")

    monkeypatch.setattr(
        timeline_service.CaseService,
        "list_case_transitions",
        fail_history,
    )
    try:
        with pytest.raises(TimelinePersistenceError) as captured:
            TimelineService(connection).list_case_timeline("case_one")
        assert "private storage detail" not in str(captured.value)
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_timeline_api_returns_safe_projection_and_recovers_after_restart(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    connection = connect_database(config.database_path)
    apply_migrations(connection)
    _add_case(connection, "case_one", version=1)
    _add_source_bundle(connection, "case_one", "source_one", "artifact_one")
    connection.close()

    with TestClient(create_app(config)) as client:
        first = client.get("/api/v1/cases/case_one/timeline")
    with TestClient(create_app(config)) as restarted_client:
        second = restarted_client.get("/api/v1/cases/case_one/timeline")

    assert first.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    assert payload["case_id"] == "case_one"
    assert [event["kind"] for event in payload["events"]] == [
        "source_imported",
        "source_extraction_status",
        "artifact_created",
    ]
    assert payload["availability"] == {
        "approvals": "available",
        "communications": "available_as_action_projection",
    }
    serialized = first.text
    assert "object_path" not in serialized
    assert "relative_path" not in serialized
    assert "checkpoint_json" not in serialized
    assert "Synthetic evidence for" not in serialized


def test_timeline_api_maps_missing_integrity_and_storage_errors_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        missing = client.get("/api/v1/cases/missing/timeline")

    assert (missing.status_code, missing.json()["error"]["code"]) == (
        404,
        "case_not_found",
    )

    def fail_integrity(*_args: object, **_kwargs: object) -> None:
        raise TimelineIntegrityError("private authority detail")

    monkeypatch.setattr(timeline_api.TimelineService, "list_case_timeline", fail_integrity)
    with TestClient(create_app(config)) as client:
        integrity = client.get("/api/v1/cases/case_one/timeline")
    assert (integrity.status_code, integrity.json()["error"]["code"]) == (
        409,
        "timeline_integrity_failed",
    )
    assert "private authority detail" not in integrity.text

    def fail_storage(*_args: object, **_kwargs: object) -> None:
        raise TimelinePersistenceError("SELECT private FROM secret_table")

    monkeypatch.setattr(timeline_api.TimelineService, "list_case_timeline", fail_storage)
    with TestClient(create_app(config)) as client:
        failed = client.get("/api/v1/cases/case_one/timeline")
    assert (failed.status_code, failed.json()["error"]["code"]) == (
        503,
        "timeline_unavailable",
    )
    assert failed.json()["error"]["retryable"] is True
    assert "secret_table" not in failed.text


def _database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    return connection


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _collect_action(case_id: str, risk: ActionRiskLevel) -> CreateActionCommand:
    return CreateActionCommand(
        case_id=case_id,
        kind=ActionKind.COLLECT_EVIDENCE,
        risk_level=risk,
        payload=ActionPayload.from_input(
            ActionKind.COLLECT_EVIDENCE,
            {"requirement_key": "purchase_date", "requirement_type": "claim"},
        ),
        requirements=(),
        dependencies=(),
        deadline=None,
        actor="local_user",
        reason="Create committed synthetic Action",
    )


def _add_case(
    connection: sqlite3.Connection,
    case_id: str,
    *,
    version: int = 2,
) -> None:
    connection.execute(
        """
        INSERT INTO case_record (
            id, type, template_id, template_version, title, goal, status,
            priority, version, created_at, updated_at, closed_at
        ) VALUES (?, 'warranty', 'warranty_claim', 1, ?, 'Synthetic timeline goal',
                  'intake', 0, ?, ?, ?, NULL)
        """,
        (case_id, f"Synthetic {case_id}", version, _T0, _T1),
    )


def _add_source_bundle(
    connection: sqlite3.Connection,
    case_id: str,
    source_id: str,
    artifact_id: str,
) -> None:
    text = f"Synthetic evidence for {source_id}."
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO source (
            id, case_id, original_name, media_type, sha256,
            size_bytes, object_path, trust_label, imported_at
        ) VALUES (?, ?, ?, 'text/plain', ?, 1, ?, 'untrusted', ?)
        """,
        (
            source_id,
            case_id,
            f"synthetic-{source_id}.txt",
            _HASH,
            f"objects/00/00/{_HASH}",
            _T1,
        ),
    )
    connection.execute(
        """
        INSERT INTO source_segment (
            id, source_id, sequence, page, bbox_json, text, extraction_method,
            confidence, text_sha256, extractor_version
        ) VALUES (?, ?, 0, 1, NULL, ?, 'direct', 1.0, ?, 'synthetic.v1')
        """,
        (f"segment_{source_id}", source_id, text, text_hash),
    )
    connection.execute(
        """
        INSERT INTO source_extraction (
            source_id, extractor_version, status, segment_count,
            notices_json, error_code, updated_at
        ) VALUES (?, 'synthetic.v1', 'completed', 1, '[]', NULL, ?)
        """,
        (source_id, _T1),
    )
    connection.execute(
        """
        INSERT INTO artifact (
            id, case_id, source_id, type, relative_path, sha256,
            size_bytes, created_by_run, created_at
        ) VALUES (?, ?, ?, 'source', ?, ?, 1, NULL, ?)
        """,
        (artifact_id, case_id, source_id, f"objects/00/00/{_HASH}", _HASH, _T1),
    )


def _add_transition(connection: sqlite3.Connection, case_id: str, transition_id: str) -> None:
    connection.execute(
        """
        INSERT INTO case_transition (
            id, case_id, prior_status, new_status, prior_version, new_version,
            actor, reason, transitioned_at
        ) VALUES (?, ?, 'intake', 'needs_review', 1, 2,
                  'reviewer', 'Synthetic transition', ?)
        """,
        (transition_id, case_id, _T1),
    )
    connection.execute(
        "UPDATE case_record SET status = 'needs_review', updated_at = ? WHERE id = ?",
        (_T1, case_id),
    )


def _add_agent(
    connection: sqlite3.Connection,
    case_id: str,
    run_id: str,
    stage_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO agent_run (
            id, case_id, status, version, current_stage, last_completed_stage,
            max_calls, max_input_tokens, max_output_tokens, max_total_tokens,
            max_cost_minor, cost_currency, used_calls, used_input_tokens,
            used_output_tokens, used_total_tokens, used_cost_minor,
            reserved_input_tokens, reserved_output_tokens, reserved_total_tokens,
            reserved_cost_minor, cancellation_requested, created_at, updated_at,
            completed_at
        ) VALUES (?, ?, 'checkpointed', 2, 'extract', 'extract',
                  14, 1000, 1000, 2000, 100, 'CNY', 1, 10, 5, 15, 1,
                  0, 0, 0, 0, 0, ?, ?, NULL)
        """,
        (run_id, case_id, _T0, _T1),
    )
    connection.execute(
        """
        INSERT INTO agent_stage_run (
            id, run_id, case_id, stage, stage_version, input_schema_version,
            output_schema_version, checkpoint_schema_version, input_scope_sha256,
            status, version, max_calls, max_input_tokens, max_output_tokens,
            max_total_tokens, max_cost_minor, cost_currency, used_calls,
            used_input_tokens, used_output_tokens, used_total_tokens, used_cost_minor,
            reserved_input_tokens, reserved_output_tokens, reserved_total_tokens,
            reserved_cost_minor, repair_count, provider, model_id, latency_ms,
            checkpoint_json, checkpoint_sha256, error_code, retryable,
            started_at, updated_at, completed_at
        ) VALUES (?, ?, ?, 'extract', 'extract.v1', 'input.v1', 'output.v1',
                  'checkpoint.v1', ?, 'checkpointed', 2, 2, 500, 500, 1000,
                  50, 'CNY', 1, 10, 5, 15, 1, 0, 0, 0, 0, 0,
                  'fake', 'synthetic-model', 2, '{}', ?, NULL, NULL, ?, ?, ?)
        """,
        (
            stage_id,
            run_id,
            case_id,
            _HASH,
            hashlib.sha256(b"{}").hexdigest(),
            _T0,
            _T1,
            _T1,
        ),
    )


def _snapshot(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    tables = (
        "case_record",
        "source",
        "source_segment",
        "source_extraction",
        "artifact",
        "case_transition",
        "claim",
        "claim_event",
        "agent_run",
        "agent_stage_run",
    )
    return tuple(
        tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))
        for table in tables
    )
