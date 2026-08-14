"""M5-02 Action Planner authority and recovery coverage."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifecase.api.actions as actions_api
from lifecase.actions import (
    ActionCommandValidationError,
    ActionCompletionEvidenceInvalidError,
    ActionDependencyInvalidError,
    ActionEvent,
    ActionIntegrityError,
    ActionKind,
    ActionPayload,
    ActionPersistenceError,
    ActionRequirementInvalidError,
    ActionRequirementStaleError,
    ActionRiskLevel,
    ActionService,
    ActionStatus,
    ActionStatusCommand,
    ActionTargetInvalidError,
    ActionTransitionNotAllowedError,
    ActionVersionConflictError,
    ClaimRequirementInput,
    CompleteActionCommand,
    CompletionAuthorityType,
    CompletionEvidenceInput,
    CreateActionCommand,
    DeadlineLinkInput,
    DependencyInput,
    ReviseActionCommand,
)
from lifecase.cases import CaseService
from lifecase.claims import (
    ClaimConfirmationBasis,
    ClaimCreatedBy,
    ClaimRecord,
    ClaimService,
    ClaimStatus,
    ClaimValue,
    ConfirmClaimCommand,
    CreateClaimCommand,
)
from lifecase.config import ApplicationConfig
from lifecase.deadlines import (
    CreateSuggestedDeadlineCommand,
    DeadlineDate,
    DeadlineDateKind,
    DeadlineService,
)
from lifecase.main import create_app
from lifecase.storage import apply_migrations, connect_database

_HASH = "a" * 64


def test_action_types_and_m5_statuses_are_closed() -> None:
    assert {item.value for item in ActionKind} == {
        "collect_evidence",
        "prepare_communication",
        "follow_up",
        "record_outcome",
    }
    assert {item.value for item in ActionStatus} == {
        "proposed",
        "blocked",
        "ready",
        "running",
        "done",
        "failed",
        "cancelled",
        "approved",
    }
    assert {item.value for item in ActionRiskLevel} == {"A", "B", "C", "D"}


def test_action_history_rejects_semantically_false_event_type(tmp_path: Path) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    service = ActionService(connection)
    action = service.create_action(
        _create_command(
            case_id,
            ActionKind.COLLECT_EVIDENCE,
            {"requirement_key": "purchase_proof", "requirement_type": "evidence"},
        )
    )
    action = service.revalidate(
        ActionStatusCommand(action.id, action.version, "local_user", "Refresh authority")
    )
    connection.execute(
        "UPDATE action_event SET event_type = 'cancelled' WHERE action_id = ? AND new_version = 2",
        (action.id,),
    )

    try:
        with pytest.raises(ActionIntegrityError, match="history is invalid"):
            service.get_action(action.id)
    finally:
        connection.close()


def test_action_history_is_read_in_one_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    service = ActionService(connection)
    action = service.create_action(
        _create_command(
            case_id,
            ActionKind.COLLECT_EVIDENCE,
            {"requirement_key": "purchase_proof", "requirement_type": "evidence"},
        )
    )
    original = ActionService._read_events
    transaction_states: list[bool] = []

    def traced_read_events(self: ActionService, action_id: str) -> tuple[ActionEvent, ...]:
        transaction_states.append(self._connection.in_transaction)
        return original(self, action_id)

    monkeypatch.setattr(ActionService, "_read_events", traced_read_events)
    try:
        assert service.list_action_events(action.id)
        assert transaction_states and all(transaction_states)
    finally:
        connection.close()


def test_action_payloads_are_kind_specific_canonical_and_bounded() -> None:
    collect = ActionPayload.from_input(
        ActionKind.COLLECT_EVIDENCE,
        {"requirement_type": "claim", "requirement_key": "purchase_date"},
    )
    assert collect.json_text == ('{"requirement_key":"purchase_date","requirement_type":"claim"}')
    assert collect.to_dict()["requirement_key"] == "purchase_date"

    communication = ActionPayload.from_input(
        ActionKind.PREPARE_COMMUNICATION,
        {"intent": "request_remedy", "recipient_role": "merchant"},
    )
    assert communication.to_dict() == {
        "intent": "request_remedy",
        "recipient_role": "merchant",
    }

    with pytest.raises(ActionCommandValidationError):
        ActionPayload.from_input(ActionKind.FOLLOW_UP, {"unknown": "value"})
    with pytest.raises(ActionCommandValidationError):
        ActionPayload.from_input(
            ActionKind.RECORD_OUTCOME,
            {"outcome": "x" * 501},
        )


def test_action_command_rejects_risk_d_duplicates_and_invalid_metadata() -> None:
    requirement = ClaimRequirementInput("claim_one", 2, _HASH, "purchase_date")
    dependency = DependencyInput("action_parent", 3)
    deadline = DeadlineLinkInput("deadline_one", 4, _HASH)

    command = CreateActionCommand(
        case_id="case_one",
        kind=ActionKind.FOLLOW_UP,
        risk_level=ActionRiskLevel.B,
        payload=ActionPayload.from_input(ActionKind.FOLLOW_UP, {"purpose": "Check status"}),
        requirements=(requirement,),
        dependencies=(dependency,),
        deadline=deadline,
        actor="local_user",
        reason="Synthetic follow-up",
    )
    assert command.requirements == (requirement,)
    assert command.dependencies == (dependency,)
    assert command.deadline == deadline

    with pytest.raises(ActionCommandValidationError):
        CreateActionCommand(
            case_id="case_one",
            kind=ActionKind.FOLLOW_UP,
            risk_level=ActionRiskLevel.D,
            payload=command.payload,
            requirements=(requirement,),
            dependencies=(),
            deadline=None,
            actor="local_user",
            reason="Risk D is prohibited",
        )
    with pytest.raises(ActionCommandValidationError):
        CreateActionCommand(
            case_id="case_one",
            kind=ActionKind.FOLLOW_UP,
            risk_level=ActionRiskLevel.B,
            payload=command.payload,
            requirements=(requirement, requirement),
            dependencies=(),
            deadline=None,
            actor="local_user",
            reason="Duplicate Claim",
        )
    with pytest.raises(ActionCommandValidationError):
        CreateActionCommand(
            case_id="case_one",
            kind=ActionKind.FOLLOW_UP,
            risk_level=ActionRiskLevel.B,
            payload=command.payload,
            requirements=(),
            dependencies=(),
            deadline=None,
            actor=" ",
            reason="Invalid actor",
        )


def test_collect_evidence_is_ready_only_for_a_current_missing_template_target(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lifecase.sqlite3"
    connection, case_id = _database(path)
    service = ActionService(connection)
    action = service.create_action(
        _create_command(
            case_id,
            ActionKind.COLLECT_EVIDENCE,
            {"requirement_key": "purchase_date", "requirement_type": "claim"},
        )
    )

    assert action.status is ActionStatus.READY
    assert action.requirements == () and action.blockers == ()
    assert action.communication_draft is None
    assert service.list_action_events(action.id)[0].new_version == 1
    connection.close()

    restarted = connect_database(path)
    try:
        assert ActionService(restarted).get_action(action.id) == action
    finally:
        restarted.close()


def test_non_collection_action_binds_exact_claim_authority_and_derives_blockers(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    service = ActionService(connection)
    blocked = service.create_action(
        _create_command(
            case_id,
            ActionKind.FOLLOW_UP,
            {"purpose": "Check synthetic warranty status"},
        )
    )
    assert blocked.status is ActionStatus.BLOCKED
    assert [item.field for item in blocked.blockers] == list(_REQUIRED_FIELDS)

    claims = tuple(
        _confirmed_claim(connection, case_id, field, f"synthetic {field}")
        for field in _REQUIRED_FIELDS
    )
    ready = service.create_action(
        _create_command(
            case_id,
            ActionKind.PREPARE_COMMUNICATION,
            {"intent": "request_remedy", "recipient_role": "merchant"},
            requirements=tuple(_requirement(claim) for claim in claims),
        )
    )
    try:
        assert ready.status is ActionStatus.READY and ready.blockers == ()
        assert ready.communication_draft is not None
        assert ready.communication_draft.startswith(
            "Intent: request_remedy\nRecipient role: merchant\nAuthoritative case facts:"
        )
        assert [item.field for item in ready.requirements] == list(_REQUIRED_FIELDS)
    finally:
        connection.close()


def test_collection_rejects_satisfied_target_and_deadline_keeps_original_date_semantics(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    claim = _confirmed_claim(connection, case_id, "purchase_date", "2026-08-03")
    service = ActionService(connection)
    with pytest.raises(ActionTargetInvalidError):
        service.create_action(
            _create_command(
                case_id,
                ActionKind.COLLECT_EVIDENCE,
                {"requirement_key": "purchase_date", "requirement_type": "claim"},
            )
        )

    deadline = DeadlineService(connection).create_suggested(
        CreateSuggestedDeadlineCommand(
            case_id=case_id,
            due=DeadlineDate(
                DeadlineDateKind.LOCAL_DATE,
                "2026-11-01",
                "America/New_York",
            ),
            actor="local_user",
            reason="Synthetic local-date follow-up preference",
        )
    )
    action = service.create_action(
        _create_command(
            case_id,
            ActionKind.RECORD_OUTCOME,
            {"outcome": "Record the synthetic result"},
            requirements=(_requirement(claim),),
            deadline=DeadlineLinkInput(
                deadline.id,
                deadline.version,
                deadline.definition_sha256,
            ),
        )
    )
    try:
        assert action.deadline is not None
        assert (
            action.deadline.date_kind,
            action.deadline.due_value,
            action.deadline.timezone,
        ) == ("local_date", "2026-11-01", "America/New_York")
    finally:
        connection.close()


def test_claim_and_dependency_bindings_fail_closed_across_case_and_version(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    other = CaseService(connection).create_case(
        title="Other synthetic Action Case",
        goal="Prove Action Case isolation",
        template_id="warranty",
    )
    candidate = _candidate_claim(connection, case_id, "purchase_date", "2026-08-03")
    cross = _confirmed_claim(connection, other.id, "purchase_date", "2026-08-03")
    parent = ActionService(connection).create_action(
        _create_command(
            case_id,
            ActionKind.COLLECT_EVIDENCE,
            {"requirement_key": "purchase_proof", "requirement_type": "evidence"},
        )
    )
    cross_parent = ActionService(connection).create_action(
        _create_command(
            other.id,
            ActionKind.COLLECT_EVIDENCE,
            {"requirement_key": "purchase_proof", "requirement_type": "evidence"},
        )
    )
    service = ActionService(connection)
    try:
        with pytest.raises(ActionRequirementInvalidError):
            service.create_action(
                _create_command(
                    case_id,
                    ActionKind.FOLLOW_UP,
                    {"purpose": "Candidate authority must fail"},
                    requirements=(_requirement(candidate),),
                )
            )
        with pytest.raises(ActionRequirementInvalidError):
            service.create_action(
                _create_command(
                    case_id,
                    ActionKind.FOLLOW_UP,
                    {"purpose": "Cross-Case authority must fail"},
                    requirements=(_requirement(cross),),
                )
            )
        with pytest.raises(ActionRequirementStaleError):
            service.create_action(
                _create_command(
                    case_id,
                    ActionKind.FOLLOW_UP,
                    {"purpose": "Stale Claim authority must fail"},
                    requirements=(
                        ClaimRequirementInput(
                            candidate.id,
                            candidate.version + 1,
                            candidate.value.sha256,
                            candidate.field,
                        ),
                    ),
                )
            )
        with pytest.raises(ActionDependencyInvalidError):
            service.create_action(
                _create_command(
                    case_id,
                    ActionKind.FOLLOW_UP,
                    {"purpose": "Cross-Case dependency must fail"},
                    dependencies=(DependencyInput(cross_parent.id, cross_parent.version),),
                )
            )

        child = service.create_action(
            _create_command(
                case_id,
                ActionKind.FOLLOW_UP,
                {"purpose": "Wait for parent completion"},
                dependencies=(DependencyInput(parent.id, parent.version),),
            )
        )
        ordered = service.list_case_actions(case_id)
        assert [item.id for item in ordered].index(parent.id) < [item.id for item in ordered].index(
            child.id
        )
        assert child.blockers[-1].authority_id == parent.id
    finally:
        connection.close()


def test_action_creation_rolls_back_aggregate_and_joins_when_history_write_fails(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    connection.execute(
        """
        CREATE TRIGGER synthetic_action_event_failure
        BEFORE INSERT ON action_event
        BEGIN
            SELECT RAISE(ABORT, 'synthetic private SQL body');
        END
        """
    )
    before = connection.execute("SELECT COUNT(*) FROM action").fetchone()[0]
    try:
        with pytest.raises(ActionPersistenceError, match="rolled back") as captured:
            ActionService(connection).create_action(
                _create_command(
                    case_id,
                    ActionKind.COLLECT_EVIDENCE,
                    {"requirement_key": "purchase_date", "requirement_type": "claim"},
                )
            )
        assert "private SQL body" not in str(captured.value)
        assert connection.execute("SELECT COUNT(*) FROM action").fetchone()[0] == before
        assert connection.execute("SELECT COUNT(*) FROM action_event").fetchone()[0] == 0
    finally:
        connection.close()


def test_lifecycle_uses_optimistic_versions_and_exact_completion_evidence(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    claims = tuple(
        _confirmed_claim(connection, case_id, field, f"synthetic {field}")
        for field in _REQUIRED_FIELDS
    )
    service = ActionService(connection)
    action = service.create_action(
        _create_command(
            case_id,
            ActionKind.FOLLOW_UP,
            {"purpose": "Run a synthetic local follow-up"},
            requirements=tuple(_requirement(claim) for claim in claims),
            risk_level=ActionRiskLevel.A,
        )
    )
    started = service.start(_status_command(action.id, action.version, "Start locally"))
    assert (started.status, started.version) == (ActionStatus.RUNNING, action.version + 1)

    with pytest.raises(ActionVersionConflictError) as stale:
        service.start(_status_command(action.id, action.version, "Competing stale start"))
    assert stale.value.current_version == started.version

    evidence_claim = claims[0]
    completed = service.complete(
        CompleteActionCommand(
            action_id=started.id,
            expected_version=started.version,
            evidence=(
                CompletionEvidenceInput(
                    CompletionAuthorityType.CLAIM,
                    evidence_claim.id,
                    evidence_claim.version,
                    evidence_claim.value.sha256,
                ),
            ),
            actor="local_user",
            reason="Record exact synthetic completion authority",
        )
    )
    try:
        assert (completed.status, completed.version) == (
            ActionStatus.DONE,
            started.version + 1,
        )
        assert completed.completion_evidence[0].authority_id == evidence_claim.id
        assert [event.new_version for event in service.list_action_events(action.id)] == [1, 2, 3]
    finally:
        connection.close()


def test_competing_start_commands_commit_at_most_once_and_increment_exactly_one(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lifecase.sqlite3"
    connection, case_id = _database(path)
    claims = tuple(
        _confirmed_claim(connection, case_id, field, f"synthetic {field}")
        for field in _REQUIRED_FIELDS
    )
    action = ActionService(connection).create_action(
        _create_command(
            case_id,
            ActionKind.FOLLOW_UP,
            {"purpose": "Compete on one synthetic Action"},
            requirements=tuple(_requirement(claim) for claim in claims),
            risk_level=ActionRiskLevel.A,
        )
    )
    connection.close()

    def attempt(reason: str) -> tuple[str, int]:
        competing = connect_database(path)
        try:
            result = ActionService(competing).start(
                _status_command(action.id, action.version, reason)
            )
            return "success", result.version
        except ActionVersionConflictError as error:
            return "conflict", error.current_version
        finally:
            competing.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(attempt, ("First competing command", "Second competing command"))
        )
    assert sum(result[0] == "success" for result in results) == 1
    assert {result[1] for result in results} == {action.version + 1}

    reopened = connect_database(path)
    try:
        current = ActionService(reopened).get_action(action.id)
        assert (current.status, current.version) == (ActionStatus.RUNNING, action.version + 1)
    finally:
        reopened.close()


def test_risk_b_cannot_start_before_approval_authority_and_bad_evidence_rolls_back(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    claims = tuple(
        _confirmed_claim(connection, case_id, field, f"synthetic {field}")
        for field in _REQUIRED_FIELDS
    )
    service = ActionService(connection)
    risk_b = service.create_action(
        _create_command(
            case_id,
            ActionKind.RECORD_OUTCOME,
            {"outcome": "Synthetic reviewed outcome"},
            requirements=tuple(_requirement(claim) for claim in claims),
            risk_level=ActionRiskLevel.B,
        )
    )
    with pytest.raises(ActionTransitionNotAllowedError):
        service.start(_status_command(risk_b.id, risk_b.version, "Approval is unavailable"))

    risk_a = service.create_action(
        _create_command(
            case_id,
            ActionKind.RECORD_OUTCOME,
            {"outcome": "Synthetic local outcome"},
            requirements=tuple(_requirement(claim) for claim in claims),
            risk_level=ActionRiskLevel.A,
        )
    )
    running = service.start(_status_command(risk_a.id, risk_a.version, "Start local action"))
    try:
        with pytest.raises(ActionCompletionEvidenceInvalidError):
            service.complete(
                CompleteActionCommand(
                    action_id=running.id,
                    expected_version=running.version,
                    evidence=(
                        CompletionEvidenceInput(
                            CompletionAuthorityType.CLAIM,
                            claims[0].id,
                            claims[0].version,
                            "0" * 64,
                        ),
                    ),
                    actor="local_user",
                    reason="Reject stale synthetic evidence",
                )
            )
        recovered = service.get_action(running.id)
        assert (recovered.status, recovered.version, recovered.completion_evidence) == (
            ActionStatus.RUNNING,
            running.version,
            (),
        )
    finally:
        connection.close()


def test_dependency_revalidation_refreshes_exact_version_after_parent_completion(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    claims = tuple(
        _confirmed_claim(connection, case_id, field, f"synthetic {field}")
        for field in _REQUIRED_FIELDS
    )
    service = ActionService(connection)
    parent = service.create_action(
        _create_command(
            case_id,
            ActionKind.COLLECT_EVIDENCE,
            {"requirement_key": "purchase_proof", "requirement_type": "evidence"},
            risk_level=ActionRiskLevel.A,
        )
    )
    child = service.create_action(
        _create_command(
            case_id,
            ActionKind.FOLLOW_UP,
            {"purpose": "Wait for evidence collection"},
            requirements=tuple(_requirement(claim) for claim in claims),
            dependencies=(DependencyInput(parent.id, parent.version),),
            risk_level=ActionRiskLevel.A,
        )
    )
    assert child.status is ActionStatus.BLOCKED
    parent = service.start(_status_command(parent.id, parent.version, "Start collection"))
    parent = service.complete(
        CompleteActionCommand(
            parent.id,
            parent.version,
            (
                CompletionEvidenceInput(
                    CompletionAuthorityType.CLAIM,
                    claims[0].id,
                    claims[0].version,
                    claims[0].value.sha256,
                ),
            ),
            "local_user",
            "Complete synthetic collection",
        )
    )
    stale_child = next(item for item in service.list_case_actions(case_id) if item.id == child.id)
    assert stale_child.status is ActionStatus.BLOCKED
    assert stale_child.dependencies[0].action_version < parent.version

    child = service.revalidate(
        _status_command(child.id, child.version, "Refresh exact dependency authority")
    )
    try:
        assert (child.status, child.version) == (ActionStatus.READY, 2)
        assert child.dependencies[0].action_version == parent.version
        assert child.blockers == ()
    finally:
        connection.close()


def test_revision_rejects_cycles_and_commits_definition_with_one_version(
    tmp_path: Path,
) -> None:
    connection, case_id = _database(tmp_path / "lifecase.sqlite3")
    service = ActionService(connection)
    first = service.create_action(
        _create_command(
            case_id,
            ActionKind.COLLECT_EVIDENCE,
            {"requirement_key": "purchase_proof", "requirement_type": "evidence"},
        )
    )
    second = service.create_action(
        _create_command(
            case_id,
            ActionKind.COLLECT_EVIDENCE,
            {"requirement_key": "defect_media", "requirement_type": "evidence"},
            dependencies=(DependencyInput(first.id, first.version),),
        )
    )
    with pytest.raises(ActionDependencyInvalidError):
        service.revise(
            ReviseActionCommand(
                first.id,
                first.version,
                _create_command(
                    case_id,
                    ActionKind.COLLECT_EVIDENCE,
                    {
                        "requirement_key": "defect_description",
                        "requirement_type": "evidence",
                    },
                    dependencies=(DependencyInput(second.id, second.version),),
                ),
            )
        )

    revised = service.revise(
        ReviseActionCommand(
            first.id,
            first.version,
            _create_command(
                case_id,
                ActionKind.COLLECT_EVIDENCE,
                {"requirement_key": "defect_description", "requirement_type": "evidence"},
            ),
        )
    )
    try:
        assert revised.version == first.version + 1
        assert revised.payload.to_dict()["requirement_key"] == "defect_description"
        assert service.list_action_events(first.id)[-1].event_type.value == "revised"
    finally:
        connection.close()


def test_action_api_is_thin_case_scoped_and_exposes_visible_semantics(tmp_path: Path) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config.database_path)
    evidence_claim = _confirmed_claim(
        connection,
        case_id,
        "purchase_date",
        "2026-08-03",
    )
    connection.close()

    with TestClient(create_app(config)) as client:
        created = client.post(
            f"/api/v1/cases/{case_id}/actions",
            json={
                "kind": "collect_evidence",
                "risk_level": "A",
                "payload": {
                    "requirement_key": "purchase_proof",
                    "requirement_type": "evidence",
                },
                "requirements": [],
                "dependencies": [],
                "deadline": None,
                "actor": "local_user",
                "reason": "Synthetic API collection",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert (body["kind"], body["status"], body["risk_level"]) == (
            "collect_evidence",
            "ready",
            "A",
        )
        assert body["topological_position"] == 0
        assert body["payload_sha256"] and body["blockers_sha256"]

        started = client.post(
            f"/api/v1/actions/{body['id']}/start",
            json={
                "expected_version": body["version"],
                "actor": "local_user",
                "reason": "Start synthetic API Action",
            },
        )
        assert (started.status_code, started.json()["status"], started.json()["version"]) == (
            200,
            "running",
            2,
        )
        completed = client.post(
            f"/api/v1/actions/{body['id']}/complete",
            json={
                "expected_version": started.json()["version"],
                "evidence": [
                    {
                        "authority_type": "claim",
                        "authority_id": evidence_claim.id,
                        "expected_version": evidence_claim.version,
                        "authority_sha256": evidence_claim.value.sha256,
                    }
                ],
                "actor": "local_user",
                "reason": "Complete synthetic API Action",
            },
        )
        assert (completed.status_code, completed.json()["status"]) == (200, "done")
        listed = client.get(f"/api/v1/cases/{case_id}/actions")
        assert listed.status_code == 200 and listed.json() == [completed.json()]

        routes = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/actions/{action_id}/approve" in routes


def test_action_api_failures_are_typed_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    connection, case_id = _database(config.database_path)
    connection.close()
    with TestClient(create_app(config)) as client:
        invalid = client.post(
            f"/api/v1/cases/{case_id}/actions",
            json={
                "kind": "follow_up",
                "risk_level": "D",
                "payload": {"purpose": "Prohibited synthetic risk"},
                "requirements": [],
                "dependencies": [],
                "actor": "local_user",
                "reason": "Reject Risk D",
            },
        )
        missing = client.get("/api/v1/cases/case_missing/actions")
    assert (invalid.status_code, invalid.json()["error"]["code"]) == (422, "action_invalid")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "case_not_found")

    def fail_list(*_args: object, **_kwargs: object) -> None:
        raise ActionPersistenceError("SELECT secret FROM private_table PRIVATE_PATH_MARKER")

    monkeypatch.setattr(actions_api.ActionService, "list_case_actions", fail_list)
    with TestClient(create_app(config)) as client:
        failed = client.get(f"/api/v1/cases/{case_id}/actions")
    assert (failed.status_code, failed.json()["error"]["code"]) == (
        503,
        "action_storage_failed",
    )
    assert failed.json()["error"]["retryable"] is True
    assert all(
        value not in failed.text for value in ("secret", "private_table", "PRIVATE_PATH_MARKER")
    )


_REQUIRED_FIELDS = (
    "merchant_or_manufacturer",
    "product_name",
    "purchase_date",
    "order_number",
    "desired_outcome",
)


def _database(path: Path) -> tuple[sqlite3.Connection, str]:
    connection = connect_database(path)
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic Action Case",
        goal="Test deterministic Action authority",
        template_id="warranty",
    )
    return connection, case.id


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _candidate_claim(
    connection: sqlite3.Connection, case_id: str, field: str, value: str
) -> ClaimRecord:
    return ClaimService(connection).create_claim(
        CreateClaimCommand(
            case_id=case_id,
            field=field,
            value=ClaimValue.from_input(value),
            status=ClaimStatus.CANDIDATE,
            confidence=0.9,
            created_by=ClaimCreatedBy.USER,
            actor="local_user",
            reason="Synthetic Action test Claim",
        )
    )


def _confirmed_claim(
    connection: sqlite3.Connection, case_id: str, field: str, value: str
) -> ClaimRecord:
    candidate = _candidate_claim(connection, case_id, field, value)
    return ClaimService(connection).confirm_claim(
        ConfirmClaimCommand(
            claim_id=candidate.id,
            expected_version=candidate.version,
            basis=ClaimConfirmationBasis.USER,
            actor="local_user",
            reason="Synthetic explicit confirmation",
        )
    )


def _requirement(claim: ClaimRecord) -> ClaimRequirementInput:
    return ClaimRequirementInput(
        claim.id,
        claim.version,
        claim.value.sha256,
        claim.field,
    )


def _create_command(
    case_id: str,
    kind: ActionKind,
    payload: object,
    *,
    requirements: tuple[ClaimRequirementInput, ...] = (),
    dependencies: tuple[DependencyInput, ...] = (),
    deadline: DeadlineLinkInput | None = None,
    risk_level: ActionRiskLevel = ActionRiskLevel.B,
) -> CreateActionCommand:
    return CreateActionCommand(
        case_id=case_id,
        kind=kind,
        risk_level=risk_level,
        payload=ActionPayload.from_input(kind, payload),
        requirements=requirements,
        dependencies=dependencies,
        deadline=deadline,
        actor="local_user",
        reason="Synthetic Action command",
    )


def _status_command(action_id: str, version: int, reason: str) -> ActionStatusCommand:
    return ActionStatusCommand(action_id, version, "local_user", reason)
