"""Transactional local Approval Gate for exact Action versions."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from lifecase.actions import (
    ActionIntegrityError,
    ActionRecord,
    ActionRiskLevel,
    ActionService,
    ActionServiceError,
    ActionStatus,
    ActionTransitionNotAllowedError,
)

from .models import (
    ApprovalDecisionCommand,
    ApprovalEvent,
    ApprovalEventType,
    ApprovalRecord,
    ApprovalStatus,
    ApprovalSummary,
    RequestApprovalCommand,
)


class ApprovalServiceError(RuntimeError):
    pass


class ApprovalNotFoundError(ApprovalServiceError):
    pass


class ApprovalActionNotFoundError(ApprovalServiceError):
    pass


class ApprovalCaseNotFoundError(ApprovalServiceError):
    pass


class ApprovalActionInvalidError(ApprovalServiceError):
    pass


class ApprovalAlreadyActiveError(ApprovalServiceError):
    pass


class ApprovalTransitionNotAllowedError(ApprovalServiceError):
    pass


class ApprovalVersionConflictError(ApprovalServiceError):
    def __init__(self, current_version: int) -> None:
        super().__init__("Approval version does not match the command")
        self.current_version = current_version


class ApprovalActionVersionConflictError(ApprovalServiceError):
    def __init__(self, current_version: int) -> None:
        super().__init__("Action version does not match the Approval command")
        self.current_version = current_version


class ApprovalIntegrityError(ApprovalServiceError):
    pass


class ApprovalPersistenceError(ApprovalServiceError):
    pass


class ApprovalService:
    """Own request, decision, invalidation-compatible reads, and history."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def request(self, command: RequestApprovalCommand) -> ApprovalRecord:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            action = self._load_action(command.action_id)
            ActionService(self._connection).require_current_analysis(action)
            if action.version != command.expected_action_version:
                raise ApprovalActionVersionConflictError(action.version)
            if action.payload.sha256 != command.payload_sha256:
                raise ApprovalActionInvalidError("Approval Action payload is stale")
            if action.status is not ActionStatus.READY or action.risk_level not in {
                ActionRiskLevel.B,
                ActionRiskLevel.C,
            }:
                raise ApprovalActionInvalidError("Action is not approval-eligible")
            active = self._connection.execute(
                """
                SELECT 1 FROM approval_request
                WHERE action_id = ? AND status IN ('pending', 'approved')
                """,
                (action.id,),
            ).fetchone()
            if active is not None:
                raise ApprovalAlreadyActiveError
            approval_id = _new_id("approval")
            occurred_at = _utc_now()
            self._connection.execute(
                """
                INSERT INTO approval_request (
                    id, action_id, case_id, action_version, payload_sha256,
                    summary_text, summary_sha256, status, version,
                    requested_by, requested_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    action.id,
                    action.case_id,
                    action.version,
                    action.payload.sha256,
                    command.summary.text,
                    command.summary.sha256,
                    command.actor,
                    command.reason,
                    occurred_at,
                    occurred_at,
                ),
            )
            self._insert_event(
                approval_id=approval_id,
                action_id=action.id,
                case_id=action.case_id,
                event_type=ApprovalEventType.REQUESTED,
                prior_status=None,
                new_status=ApprovalStatus.PENDING,
                prior_version=0,
                new_version=1,
                actor=command.actor,
                reason=command.reason,
                occurred_at=occurred_at,
            )
            self._connection.execute("COMMIT")
        except ApprovalServiceError:
            self._rollback()
            raise
        except ActionIntegrityError as error:
            self._rollback()
            raise ApprovalActionInvalidError("Action analysis is no longer current") from error
        except (ActionServiceError, sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ApprovalPersistenceError("Approval request was rolled back") from error
        return self.get_approval(approval_id)

    def approve(self, command: ApprovalDecisionCommand) -> tuple[ApprovalRecord, ActionRecord]:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            approval = self._load_approval(command.approval_id)
            action = self._validate_pending_decision(command, approval)
            occurred_at = _utc_now()
            ActionService(self._connection).approve_with_authority_in_transaction(
                action,
                actor=command.actor,
                reason=command.reason,
                occurred_at=occurred_at,
            )
            self._update_approval(
                approval,
                new_status=ApprovalStatus.APPROVED,
                event_type=ApprovalEventType.APPROVED,
                actor=command.actor,
                reason=command.reason,
                occurred_at=occurred_at,
            )
            self._connection.execute("COMMIT")
        except ApprovalServiceError:
            self._rollback()
            raise
        except (ActionTransitionNotAllowedError, ActionIntegrityError) as error:
            self._rollback()
            raise ApprovalActionInvalidError("Action is not approval-eligible") from error
        except (ActionServiceError, sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ApprovalPersistenceError("Approval decision was rolled back") from error
        return self.get_approval(approval.id), ActionService(self._connection).get_action(action.id)

    def reject(self, command: ApprovalDecisionCommand) -> ApprovalRecord:
        return self._terminal_decision(
            command,
            new_status=ApprovalStatus.REJECTED,
            event_type=ApprovalEventType.REJECTED,
        )

    def expire(self, command: ApprovalDecisionCommand) -> ApprovalRecord:
        return self._terminal_decision(
            command,
            new_status=ApprovalStatus.EXPIRED,
            event_type=ApprovalEventType.EXPIRED,
        )

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        try:
            self._require_idle()
            self._connection.execute("BEGIN")
            approval = self._load_approval(approval_id)
            self._validate_record(approval)
            self._connection.execute("COMMIT")
            return approval
        except ApprovalServiceError:
            self._rollback()
            raise
        except (ActionServiceError, sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ApprovalPersistenceError("Approval could not be read") from error

    def list_case_approvals(self, case_id: str) -> tuple[ApprovalRecord, ...]:
        try:
            self._require_idle()
            self._connection.execute("BEGIN")
            approvals = self.list_case_approvals_in_transaction(case_id)
            self._connection.execute("COMMIT")
            return approvals
        except ApprovalServiceError:
            self._rollback()
            raise
        except (ActionServiceError, sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ApprovalPersistenceError("Case Approvals could not be read") from error

    def list_case_approvals_in_transaction(self, case_id: str) -> tuple[ApprovalRecord, ...]:
        if not self._connection.in_transaction:
            raise ApprovalPersistenceError("Approval transaction read requires a transaction")
        exists = self._connection.execute(
            "SELECT 1 FROM case_record WHERE id = ?", (case_id,)
        ).fetchone()
        if exists is None:
            raise ApprovalCaseNotFoundError
        rows = self._connection.execute(
            "SELECT id FROM approval_request WHERE case_id = ? ORDER BY created_at, id",
            (case_id,),
        ).fetchall()
        approvals = tuple(self._load_approval(str(row["id"])) for row in rows)
        for approval in approvals:
            self._validate_record(approval)
        return approvals

    def list_approval_events_in_transaction(self, approval_id: str) -> tuple[ApprovalEvent, ...]:
        if not self._connection.in_transaction:
            raise ApprovalPersistenceError("Approval history requires a transaction")
        return self._read_events(approval_id)

    def invalidate_active_for_action_in_transaction(
        self,
        *,
        action_id: str,
        case_id: str,
        actor: str,
        reason: str,
        occurred_at: str,
    ) -> None:
        """Invalidate active bindings inside the Action command transaction."""

        if not self._connection.in_transaction:
            raise ApprovalPersistenceError("Approval invalidation requires a transaction")
        rows = self._connection.execute(
            """
            SELECT id FROM approval_request
            WHERE action_id = ? AND case_id = ? AND status IN ('pending', 'approved')
            ORDER BY created_at, id
            """,
            (action_id, case_id),
        ).fetchall()
        for row in rows:
            approval = self._load_approval(str(row["id"]))
            self._validate_record(approval)
            self._update_approval(
                approval,
                new_status=ApprovalStatus.INVALIDATED,
                event_type=ApprovalEventType.INVALIDATED,
                actor=actor,
                reason=reason,
                occurred_at=occurred_at,
            )

    def list_approval_events(self, approval_id: str) -> tuple[ApprovalEvent, ...]:
        try:
            self._require_idle()
            self._connection.execute("BEGIN")
            approval = self._load_approval(approval_id)
            self._validate_record(approval)
            events = self._read_events(approval.id)
            self._connection.execute("COMMIT")
            return events
        except ApprovalServiceError:
            self._rollback()
            raise
        except (ActionServiceError, sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ApprovalPersistenceError("Approval history could not be read") from error

    def _terminal_decision(
        self,
        command: ApprovalDecisionCommand,
        *,
        new_status: ApprovalStatus,
        event_type: ApprovalEventType,
    ) -> ApprovalRecord:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            approval = self._load_approval(command.approval_id)
            self._validate_pending_decision(command, approval)
            self._update_approval(
                approval,
                new_status=new_status,
                event_type=event_type,
                actor=command.actor,
                reason=command.reason,
                occurred_at=_utc_now(),
            )
            self._connection.execute("COMMIT")
        except ApprovalServiceError:
            self._rollback()
            raise
        except (ActionServiceError, sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ApprovalPersistenceError("Approval decision was rolled back") from error
        return self.get_approval(approval.id)

    def _validate_pending_decision(
        self,
        command: ApprovalDecisionCommand,
        approval: ApprovalRecord,
    ) -> ActionRecord:
        if approval.action_id != command.action_id:
            raise ApprovalActionInvalidError("Approval does not belong to the Action")
        if approval.version != command.expected_approval_version:
            raise ApprovalVersionConflictError(approval.version)
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalTransitionNotAllowedError
        action = self._load_action(command.action_id)
        if action.version != command.expected_action_version:
            raise ApprovalActionVersionConflictError(action.version)
        if (
            action.version != approval.action_version
            or action.payload.sha256 != approval.payload_sha256
            or action.status is not ActionStatus.READY
        ):
            raise ApprovalActionInvalidError("Approval Action binding is stale")
        return action

    def _validate_record(self, approval: ApprovalRecord) -> None:
        events = self._read_events(approval.id)
        if len(events) != approval.version or not events:
            raise ApprovalIntegrityError("Approval history is incomplete")
        for position, event in enumerate(events, start=1):
            if (
                event.action_id != approval.action_id
                or event.case_id != approval.case_id
                or event.new_version != position
                or event.prior_version != position - 1
                or (position == 1 and event.prior_status is not None)
                or (position > 1 and event.prior_status is not events[position - 2].new_status)
                or not _is_valid_approval_event_transition(event)
            ):
                raise ApprovalIntegrityError("Approval history is invalid")
        if events[-1].new_status is not approval.status:
            raise ApprovalIntegrityError("Approval history does not match authority")
        action = self._load_action(approval.action_id)
        if action.case_id != approval.case_id:
            raise ApprovalIntegrityError("Approval Action scope is invalid")
        if approval.status is ApprovalStatus.PENDING and (
            action.version != approval.action_version
            or action.payload.sha256 != approval.payload_sha256
            or action.status is not ActionStatus.READY
        ):
            raise ApprovalActionInvalidError("Pending Approval binding is stale")
        if approval.status is ApprovalStatus.APPROVED and (
            action.version < approval.action_version + 1
            or action.payload.sha256 != approval.payload_sha256
        ):
            raise ApprovalActionInvalidError("Approved Action binding is stale")
        if approval.status is ApprovalStatus.APPROVED:
            event = self._connection.execute(
                """
                SELECT event_type, new_status, payload_sha256
                FROM action_event
                WHERE action_id = ? AND new_version = ?
                """,
                (approval.action_id, approval.action_version + 1),
            ).fetchone()
            if event is None or tuple(event) != (
                "approved",
                "approved",
                approval.payload_sha256,
            ):
                raise ApprovalIntegrityError("Approved Action history is invalid")

    def _load_action(self, action_id: str) -> ActionRecord:
        row = self._connection.execute(
            "SELECT case_id FROM action WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            raise ApprovalActionNotFoundError
        records = ActionService(self._connection).list_case_actions_in_transaction(
            str(row["case_id"])
        )
        action = next((item for item in records if item.id == action_id), None)
        if action is None:
            raise ApprovalIntegrityError("Approval Action projection is incomplete")
        return action

    def _load_approval(self, approval_id: str) -> ApprovalRecord:
        row = self._connection.execute(
            """
            SELECT id, action_id, case_id, action_version, payload_sha256,
                   summary_text, summary_sha256, status, version,
                   requested_by, requested_reason, created_at, updated_at
            FROM approval_request WHERE id = ?
            """,
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ApprovalNotFoundError
        return ApprovalRecord(
            id=str(row["id"]),
            action_id=str(row["action_id"]),
            case_id=str(row["case_id"]),
            action_version=int(row["action_version"]),
            payload_sha256=str(row["payload_sha256"]),
            summary=ApprovalSummary(str(row["summary_text"]), str(row["summary_sha256"])),
            status=ApprovalStatus(str(row["status"])),
            version=int(row["version"]),
            requested_by=str(row["requested_by"]),
            requested_reason=str(row["requested_reason"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _update_approval(
        self,
        approval: ApprovalRecord,
        *,
        new_status: ApprovalStatus,
        event_type: ApprovalEventType,
        actor: str,
        reason: str,
        occurred_at: str,
    ) -> None:
        new_version = approval.version + 1
        cursor = self._connection.execute(
            """
            UPDATE approval_request SET status = ?, version = ?, updated_at = ?
            WHERE id = ? AND version = ?
            """,
            (new_status.value, new_version, occurred_at, approval.id, approval.version),
        )
        if cursor.rowcount != 1:
            raise ApprovalVersionConflictError(approval.version)
        self._insert_event(
            approval_id=approval.id,
            action_id=approval.action_id,
            case_id=approval.case_id,
            event_type=event_type,
            prior_status=approval.status,
            new_status=new_status,
            prior_version=approval.version,
            new_version=new_version,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )

    def _insert_event(
        self,
        *,
        approval_id: str,
        action_id: str,
        case_id: str,
        event_type: ApprovalEventType,
        prior_status: ApprovalStatus | None,
        new_status: ApprovalStatus,
        prior_version: int,
        new_version: int,
        actor: str,
        reason: str,
        occurred_at: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO approval_event (
                id, approval_id, action_id, case_id, event_type,
                prior_status, new_status, prior_version, new_version,
                actor, reason, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("approval_event"),
                approval_id,
                action_id,
                case_id,
                event_type.value,
                prior_status.value if prior_status else None,
                new_status.value,
                prior_version,
                new_version,
                actor,
                reason,
                occurred_at,
            ),
        )

    def _read_events(self, approval_id: str) -> tuple[ApprovalEvent, ...]:
        rows = self._connection.execute(
            """
            SELECT id, approval_id, action_id, case_id, event_type,
                   prior_status, new_status, prior_version, new_version,
                   actor, reason, occurred_at
            FROM approval_event WHERE approval_id = ? ORDER BY new_version, id
            """,
            (approval_id,),
        ).fetchall()
        return tuple(
            ApprovalEvent(
                id=str(row["id"]),
                approval_id=str(row["approval_id"]),
                action_id=str(row["action_id"]),
                case_id=str(row["case_id"]),
                event_type=ApprovalEventType(str(row["event_type"])),
                prior_status=(
                    ApprovalStatus(str(row["prior_status"]))
                    if row["prior_status"] is not None
                    else None
                ),
                new_status=ApprovalStatus(str(row["new_status"])),
                prior_version=int(row["prior_version"]),
                new_version=int(row["new_version"]),
                actor=str(row["actor"]),
                reason=str(row["reason"]),
                occurred_at=str(row["occurred_at"]),
            )
            for row in rows
        )

    def _require_idle(self) -> None:
        if self._connection.in_transaction:
            raise ApprovalPersistenceError("Approval read requires an idle connection")

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")


def _is_valid_approval_event_transition(event: ApprovalEvent) -> bool:
    allowed: dict[ApprovalEventType, set[tuple[ApprovalStatus | None, ApprovalStatus]]] = {
        ApprovalEventType.REQUESTED: {(None, ApprovalStatus.PENDING)},
        ApprovalEventType.APPROVED: {(ApprovalStatus.PENDING, ApprovalStatus.APPROVED)},
        ApprovalEventType.REJECTED: {(ApprovalStatus.PENDING, ApprovalStatus.REJECTED)},
        ApprovalEventType.EXPIRED: {(ApprovalStatus.PENDING, ApprovalStatus.EXPIRED)},
        ApprovalEventType.INVALIDATED: {
            (ApprovalStatus.PENDING, ApprovalStatus.INVALIDATED),
            (ApprovalStatus.APPROVED, ApprovalStatus.INVALIDATED),
        },
    }
    return (event.prior_status, event.new_status) in allowed[event.event_type]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
