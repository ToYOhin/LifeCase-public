"""M5-03 local Approval Gate authority and recovery coverage."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifecase.api.approvals as approvals_api
from lifecase.actions import (
    ActionKind,
    ActionPayload,
    ActionRiskLevel,
    ActionService,
    ActionStatus,
    ActionStatusCommand,
    ActionTransitionNotAllowedError,
    CreateActionCommand,
    DependencyInput,
    ReviseActionCommand,
)
from lifecase.approvals import (
    ApprovalActionInvalidError,
    ApprovalActionVersionConflictError,
    ApprovalAlreadyActiveError,
    ApprovalDecisionCommand,
    ApprovalIntegrityError,
    ApprovalPersistenceError,
    ApprovalService,
    ApprovalStatus,
    ApprovalSummary,
    ApprovalTransitionNotAllowedError,
    ApprovalVersionConflictError,
    RequestApprovalCommand,
)
from lifecase.cases import CaseService
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.storage import apply_migrations, connect_database


def test_request_and_approve_bind_exact_action_summary_and_history(tmp_path: Path) -> None:
    path = tmp_path / "lifecase.sqlite3"
    connection, case_id = _database(path)
    action = _action(connection, case_id, ActionRiskLevel.B)
    service = ApprovalService(connection)
    summary = ApprovalSummary.from_input("Approve this exact synthetic local Action")

    pending = service.request(_request(action, summary))
    assert (pending.status, pending.version, pending.action_version) == (
        ApprovalStatus.PENDING,
        1,
        action.version,
    )
    assert pending.payload_sha256 == action.payload.sha256
    assert pending.summary == summary
    assert ActionService(connection).get_action(action.id).version == action.version

    approved, approved_action = service.approve(
        _decision(pending, action.id, action.version, "Approve exact local Action")
    )
    assert (approved.status, approved.version) == (ApprovalStatus.APPROVED, 2)
    assert (approved_action.status, approved_action.version) == (ActionStatus.APPROVED, 2)
    assert [event.new_version for event in service.list_approval_events(approved.id)] == [1, 2]
    assert (
        ActionService(connection).list_action_events(action.id)[-1].event_type.value == "approved"
    )

    running = ActionService(connection).start(
        ActionStatusCommand(
            action.id,
            approved_action.version,
            "local_user",
            "Run approved Risk B Action locally",
        )
    )
    assert (running.status, running.version) == (ActionStatus.RUNNING, 3)
    assert service.get_approval(approved.id) == approved
    connection.close()

    restarted = connect_database(path)
    try:
        assert ApprovalService(restarted).get_approval(approved.id) == approved
        assert ActionService(restarted).get_action(action.id) == running
    finally:
        restarted.close()


def test_approval_history_rejects_semantically_false_event_type(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    action = _action(connection, case_id, ActionRiskLevel.B)
    service = ApprovalService(connection)
    pending = service.request(
        _request(action, ApprovalSummary.from_input("Review exact purchase-proof action"))
    )
    rejected = service.reject(
        _decision(pending, action.id, action.version, "Evidence is already available")
    )
    connection.execute(
        """
        UPDATE approval_event SET event_type = 'expired'
        WHERE approval_id = ? AND new_version = 2
        """,
        (rejected.id,),
    )

    try:
        with pytest.raises(ApprovalIntegrityError, match="history is invalid"):
            service.get_approval(rejected.id)
    finally:
        connection.close()


def test_risk_a_request_and_risk_c_execution_fail_closed(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    risk_a = _action(connection, case_id, ActionRiskLevel.A, target="purchase_date")
    with pytest.raises(ApprovalActionInvalidError):
        ApprovalService(connection).request(
            _request(risk_a, ApprovalSummary.from_input("Risk A needs no gate"))
        )

    risk_c = _action(connection, case_id, ActionRiskLevel.C, target="purchase_proof")
    approval = ApprovalService(connection).request(
        _request(risk_c, ApprovalSummary.from_input("Record Risk C review only"))
    )
    _, approved = ApprovalService(connection).approve(
        _decision(approval, risk_c.id, risk_c.version, "Approve record-only Risk C")
    )
    try:
        with pytest.raises(ActionTransitionNotAllowedError):
            ActionService(connection).start(
                ActionStatusCommand(
                    approved.id,
                    approved.version,
                    "local_user",
                    "Risk C must not execute",
                )
            )
    finally:
        connection.close()


def test_request_rejects_payload_version_duplicates_and_wrong_action(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    first = _action(connection, case_id, ActionRiskLevel.B, target="purchase_date")
    second = _action(connection, case_id, ActionRiskLevel.B, target="purchase_proof")
    service = ApprovalService(connection)
    summary = ApprovalSummary.from_input("Synthetic exact summary")
    try:
        with pytest.raises(ApprovalActionVersionConflictError):
            service.request(
                RequestApprovalCommand(
                    first.id,
                    first.version + 1,
                    first.payload.sha256,
                    summary,
                    "local_label",
                    "Stale Action version",
                )
            )
        with pytest.raises(ApprovalActionInvalidError):
            service.request(
                RequestApprovalCommand(
                    first.id,
                    first.version,
                    "0" * 64,
                    summary,
                    "local_label",
                    "Stale payload hash",
                )
            )
        pending = service.request(_request(first, summary))
        with pytest.raises(ApprovalAlreadyActiveError):
            service.request(_request(first, summary))
        with pytest.raises(ApprovalActionInvalidError):
            service.approve(_decision(pending, second.id, second.version, "Wrong Action scope"))
    finally:
        connection.close()


def test_reject_expire_and_competing_decisions_are_optimistic(tmp_path: Path) -> None:
    path = tmp_path / "lifecase.sqlite3"
    connection, case_id = _database(path)
    action = _action(connection, case_id, ActionRiskLevel.B)
    pending = ApprovalService(connection).request(
        _request(action, ApprovalSummary.from_input("Competing local decision"))
    )
    connection.close()

    def decide(operation: str) -> str:
        competing = connect_database(path)
        try:
            service = ApprovalService(competing)
            command = _decision(pending, action.id, action.version, operation)
            if operation == "reject":
                service.reject(command)
            else:
                service.expire(command)
            return "success"
        except (ApprovalVersionConflictError, ApprovalTransitionNotAllowedError):
            return "conflict"
        finally:
            competing.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(decide, ("reject", "expire")))
    assert outcomes.count("success") == 1
    assert outcomes.count("conflict") == 1

    reopened = connect_database(path)
    try:
        current = ApprovalService(reopened).get_approval(pending.id)
        assert current.status in {ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED}
        assert current.version == 2
        assert ActionService(reopened).get_action(action.id) == action
    finally:
        reopened.close()


def test_revision_invalidates_pending_or_approved_authority_in_same_transaction(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    service = ApprovalService(connection)
    pending_action = _action(connection, case_id, ActionRiskLevel.B, target="purchase_date")
    pending = service.request(
        _request(pending_action, ApprovalSummary.from_input("Pending revision binding"))
    )
    revised = ActionService(connection).revise(
        ReviseActionCommand(
            pending_action.id,
            pending_action.version,
            _definition(case_id, ActionRiskLevel.B, "purchase_proof"),
        )
    )
    assert revised.version == pending_action.version + 1
    assert service.get_approval(pending.id).status is ApprovalStatus.INVALIDATED

    approved_action = _action(connection, case_id, ActionRiskLevel.B, target="defect_media")
    approval = service.request(
        _request(approved_action, ApprovalSummary.from_input("Approved revision binding"))
    )
    approved, approved_action = service.approve(
        _decision(approval, approved_action.id, approved_action.version, "Approve before revise")
    )
    revised_approved = ActionService(connection).revise(
        ReviseActionCommand(
            approved_action.id,
            approved_action.version,
            _definition(case_id, ActionRiskLevel.B, "defect_description"),
        )
    )
    try:
        invalidated = service.get_approval(approved.id)
        assert (invalidated.status, invalidated.version) == (ApprovalStatus.INVALIDATED, 3)
        assert (revised_approved.status, revised_approved.version) == (ActionStatus.READY, 3)
        assert service.list_approval_events(approved.id)[-1].event_type.value == "invalidated"
    finally:
        connection.close()


def test_revalidate_and_cancel_invalidate_pending_authority(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    approval_service = ApprovalService(connection)

    revalidated_action = _action(connection, case_id, ActionRiskLevel.B, target="purchase_date")
    revalidated_approval = approval_service.request(
        _request(revalidated_action, ApprovalSummary.from_input("Pending revalidation binding"))
    )
    revalidated = ActionService(connection).revalidate(
        ActionStatusCommand(
            revalidated_action.id,
            revalidated_action.version,
            "local_user",
            "Refresh exact authorities",
        )
    )
    assert revalidated.version == revalidated_action.version + 1
    assert (
        approval_service.get_approval(revalidated_approval.id).status is ApprovalStatus.INVALIDATED
    )

    cancelled_action = _action(connection, case_id, ActionRiskLevel.C, target="defect_media")
    cancelled_approval = approval_service.request(
        _request(cancelled_action, ApprovalSummary.from_input("Pending cancellation binding"))
    )
    cancelled = ActionService(connection).cancel(
        ActionStatusCommand(
            cancelled_action.id,
            cancelled_action.version,
            "local_user",
            "Cancel exact Action",
        )
    )
    assert cancelled.status is ActionStatus.CANCELLED
    assert approval_service.get_approval(cancelled_approval.id).status is ApprovalStatus.INVALIDATED
    connection.close()


def test_approval_and_action_writes_roll_back_together_on_action_history_failure(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    action = _action(connection, case_id, ActionRiskLevel.B)
    pending = ApprovalService(connection).request(
        _request(action, ApprovalSummary.from_input("Atomic approval decision"))
    )
    connection.execute(
        """
        CREATE TRIGGER synthetic_approval_action_failure
        BEFORE INSERT ON action_event
        WHEN NEW.event_type = 'approved'
        BEGIN
            SELECT RAISE(ABORT, 'synthetic private SQL body');
        END
        """
    )
    try:
        with pytest.raises(ApprovalPersistenceError, match="rolled back") as captured:
            ApprovalService(connection).approve(
                _decision(pending, action.id, action.version, "Trigger atomic rollback")
            )
        assert "private SQL body" not in str(captured.value)
        assert ApprovalService(connection).get_approval(pending.id) == pending
        assert ActionService(connection).get_action(action.id) == action
    finally:
        connection.close()


def test_approval_api_exposes_local_actor_binding_and_safe_decision(tmp_path: Path) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config.database_path)
    action = _action(connection, case_id, ActionRiskLevel.B)
    connection.close()

    with TestClient(create_app(config)) as client:
        requested = client.post(
            f"/api/v1/actions/{action.id}/approval-requests",
            json={
                "expected_action_version": action.version,
                "payload_sha256": action.payload.sha256,
                "summary": "Approve exact synthetic Action shown locally",
                "actor": "local_audit_label",
                "reason": "Synthetic local review request",
            },
        )
        assert requested.status_code == 201
        approval = requested.json()
        assert (approval["status"], approval["requested_by"]) == (
            "pending",
            "local_audit_label",
        )
        assert approval["summary_sha256"]
        listed = client.get(f"/api/v1/cases/{case_id}/approvals")
        assert listed.status_code == 200 and listed.json() == [approval]

        approved = client.post(
            f"/api/v1/actions/{action.id}/approve",
            json={
                "approval_id": approval["id"],
                "expected_approval_version": approval["version"],
                "expected_action_version": action.version,
                "actor": "another_local_label",
                "reason": "Synthetic explicit local approval",
            },
        )
        assert approved.status_code == 200
        assert approved.json()["approval"]["status"] == "approved"
        assert approved.json()["action"]["status"] == "approved"


def test_approval_with_blocked_downstream_dependency_returns_committed_success(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config.database_path)
    actions = ActionService(connection)
    parent = _action(connection, case_id, ActionRiskLevel.B, target="purchase_proof")
    child = actions.create_action(
        CreateActionCommand(
            case_id=case_id,
            kind=ActionKind.FOLLOW_UP,
            risk_level=ActionRiskLevel.A,
            payload=ActionPayload.from_input(
                ActionKind.FOLLOW_UP,
                {"purpose": "Track synthetic downstream warranty response"},
            ),
            requirements=(),
            dependencies=(DependencyInput(parent.id, parent.version),),
            deadline=None,
            actor="local_label",
            reason="Create blocked downstream Action",
        )
    )
    assert (parent.status, child.status) == (ActionStatus.READY, ActionStatus.BLOCKED)
    pending = ApprovalService(connection).request(
        _request(parent, ApprovalSummary.from_input("Approve exact upstream Action"))
    )
    connection.close()

    with TestClient(create_app(config)) as client:
        approved = client.post(
            f"/api/v1/actions/{parent.id}/approve",
            json={
                "approval_id": pending.id,
                "expected_approval_version": pending.version,
                "expected_action_version": parent.version,
                "actor": "local_label",
                "reason": "Approve upstream while downstream remains blocked",
            },
        )
        assert approved.status_code == 200
        approved_body = approved.json()
        assert (approved_body["approval"]["status"], approved_body["approval"]["version"]) == (
            "approved",
            pending.version + 1,
        )
        assert (approved_body["action"]["status"], approved_body["action"]["version"]) == (
            "approved",
            parent.version + 1,
        )

        listed = client.get(f"/api/v1/cases/{case_id}/actions")
        assert listed.status_code == 200
        stale_child = next(item for item in listed.json() if item["id"] == child.id)
        assert stale_child["status"] == "blocked"
        assert stale_child["dependencies"] == [
            {
                "action_id": parent.id,
                "action_version": parent.version,
                "status": "approved",
            }
        ]

        revalidated = client.post(
            f"/api/v1/actions/{child.id}/revalidate",
            json={
                "expected_version": child.version,
                "actor": "local_label",
                "reason": "Refresh exact upstream authority binding",
            },
        )
        assert (revalidated.status_code, revalidated.json()["status"]) == (200, "blocked")
        assert revalidated.json()["version"] == child.version + 1
        assert revalidated.json()["dependencies"][0]["action_version"] == parent.version + 1

        timeline = client.get(f"/api/v1/cases/{case_id}/timeline")
        assert timeline.status_code == 200
        relevant_kinds = [
            event["kind"]
            for event in timeline.json()["events"]
            if event["kind"] in {"action_approved", "approval_approved", "action_revalidated"}
        ]
        assert relevant_kinds == [
            "action_approved",
            "approval_approved",
            "action_revalidated",
        ]

    with TestClient(create_app(config)) as restarted_client:
        recovered_actions = restarted_client.get(f"/api/v1/cases/{case_id}/actions")
        recovered_approvals = restarted_client.get(f"/api/v1/cases/{case_id}/approvals")
    assert recovered_actions.status_code == 200
    recovered_child = next(item for item in recovered_actions.json() if item["id"] == child.id)
    assert (recovered_child["status"], recovered_child["version"]) == (
        "blocked",
        child.version + 1,
    )
    assert recovered_child["dependencies"][0]["action_version"] == parent.version + 1
    assert recovered_approvals.status_code == 200
    assert recovered_approvals.json()[0]["status"] == "approved"


def test_approval_api_failures_are_typed_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config.database_path)
    action = _action(connection, case_id, ActionRiskLevel.A)
    connection.close()
    with TestClient(create_app(config)) as client:
        invalid = client.post(
            f"/api/v1/actions/{action.id}/approval-requests",
            json={
                "expected_action_version": action.version,
                "payload_sha256": action.payload.sha256,
                "summary": "Risk A request is invalid",
                "actor": "local_label",
                "reason": "Reject unnecessary gate",
            },
        )
        missing = client.get("/api/v1/cases/case_missing/approvals")
    assert (invalid.status_code, invalid.json()["error"]["code"]) == (
        409,
        "approval_action_invalid",
    )
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "case_not_found")

    def fail_list(*_args: object, **_kwargs: object) -> None:
        raise ApprovalPersistenceError("SELECT secret FROM private_table PRIVATE_PATH_MARKER")

    monkeypatch.setattr(approvals_api.ApprovalService, "list_case_approvals", fail_list)
    with TestClient(create_app(config)) as client:
        failed = client.get(f"/api/v1/cases/{case_id}/approvals")
    assert (failed.status_code, failed.json()["error"]["code"]) == (
        503,
        "approval_storage_failed",
    )
    assert all(
        value not in failed.text for value in ("secret", "private_table", "PRIVATE_PATH_MARKER")
    )


def _database(path: Path) -> tuple[sqlite3.Connection, str]:
    connection = connect_database(path)
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic Approval Case",
        goal="Test exact local Approval authority",
        template_id="warranty",
    )
    return connection, case.id


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _definition(
    case_id: str,
    risk: ActionRiskLevel,
    target: str,
) -> CreateActionCommand:
    requirement_type = (
        "evidence"
        if target
        in {
            "purchase_proof",
            "defect_description",
            "defect_media",
        }
        else "claim"
    )
    return CreateActionCommand(
        case_id=case_id,
        kind=ActionKind.COLLECT_EVIDENCE,
        risk_level=risk,
        payload=ActionPayload.from_input(
            ActionKind.COLLECT_EVIDENCE,
            {"requirement_key": target, "requirement_type": requirement_type},
        ),
        requirements=(),
        dependencies=(),
        deadline=None,
        actor="local_label",
        reason="Synthetic Approval Action definition",
    )


def _action(
    connection: sqlite3.Connection,
    case_id: str,
    risk: ActionRiskLevel,
    *,
    target: str = "purchase_date",
):
    return ActionService(connection).create_action(_definition(case_id, risk, target))


def _request(action, summary: ApprovalSummary) -> RequestApprovalCommand:
    return RequestApprovalCommand(
        action.id,
        action.version,
        action.payload.sha256,
        summary,
        "local_label",
        "Request synthetic local review",
    )


def _decision(
    approval,
    action_id: str,
    action_version: int,
    reason: str,
) -> ApprovalDecisionCommand:
    return ApprovalDecisionCommand(
        approval.id,
        action_id,
        approval.version,
        action_version,
        "local_label",
        reason,
    )
