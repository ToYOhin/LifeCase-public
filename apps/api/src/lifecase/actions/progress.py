"""Append-only human observations without changing Action or Approval authority."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from .models import ActionCommandValidationError, ActionKind, ActionRecord, ActionStatus
from .service import (
    ActionIntegrityError,
    ActionNotFoundError,
    ActionPersistenceError,
    ActionService,
    ActionServiceError,
    ActionTransitionNotAllowedError,
    ActionVersionConflictError,
)

ProgressKind = Literal["progress", "result"]


class ActionProgressConflictError(ActionServiceError):
    """Another observation was saved after the client last read the history."""


def validate_progress_text(value: object, maximum: int = 2000) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or "\0" in value
        or len(value.encode("utf-8")) > maximum * 4
    ):
        raise ActionCommandValidationError("Human progress text is invalid")
    return value


@dataclass(frozen=True, slots=True)
class RecordActionProgressCommand:
    case_id: str
    action_id: str
    expected_version: int
    expected_last_progress_id: str | None
    kind: ProgressKind
    text: str
    actor: str
    reason: str

    def __post_init__(self) -> None:
        validate_progress_text(self.case_id, 200)
        validate_progress_text(self.action_id, 200)
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise ActionCommandValidationError("Action version is invalid")
        if self.expected_last_progress_id is not None:
            validate_progress_text(self.expected_last_progress_id, 200)
        if self.kind not in {"progress", "result"}:
            raise ActionCommandValidationError("Human progress kind is invalid")
        validate_progress_text(self.text)
        validate_progress_text(self.actor, 120)
        validate_progress_text(self.reason, 1000)


@dataclass(frozen=True, slots=True)
class ActionProgressEntry:
    id: str
    action_id: str
    case_id: str
    action_version: int
    kind: ProgressKind
    text: str
    actor: str
    reason: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ActionProgressView:
    action: ActionRecord
    position: int
    entries: tuple[ActionProgressEntry, ...]


class ActionProgressService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def read(self, case_id: str, action_id: str) -> ActionProgressView:
        self._require_idle()
        try:
            self._connection.execute("BEGIN")
            view = self._view(case_id, action_id)
            self._connection.execute("COMMIT")
            return view
        except ActionServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ActionPersistenceError("Human progress could not be read") from error

    def append(self, command: RecordActionProgressCommand) -> ActionProgressView:
        self._require_idle()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            view = self._view(command.case_id, command.action_id)
            action = view.action
            if action.version != command.expected_version:
                raise ActionVersionConflictError(action.version)
            last_id = view.entries[-1].id if view.entries else None
            if last_id != command.expected_last_progress_id:
                raise ActionProgressConflictError("Human progress changed; read it again")
            if action.status not in {
                ActionStatus.BLOCKED,
                ActionStatus.READY,
                ActionStatus.APPROVED,
                ActionStatus.RUNNING,
                ActionStatus.FAILED,
            }:
                raise ActionTransitionNotAllowedError("Closed Action progress is read-only")
            ActionService(self._connection).require_current_analysis(action)
            entry = ActionProgressEntry(
                id=f"action_progress_{uuid4().hex}",
                action_id=action.id,
                case_id=action.case_id,
                action_version=action.version,
                kind=command.kind,
                text=command.text,
                actor=command.actor,
                reason=command.reason,
                created_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            )
            self._connection.execute(
                """
                INSERT INTO action_progress (
                    id, action_id, case_id, position, action_version, kind,
                    text, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.action_id,
                    entry.case_id,
                    len(view.entries),
                    entry.action_version,
                    entry.kind,
                    entry.text,
                    entry.actor,
                    entry.reason,
                    entry.created_at,
                ),
            )
            result = ActionProgressView(action, view.position, (*view.entries, entry))
            self._connection.execute("COMMIT")
            return result
        except ActionServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ActionPersistenceError("Human progress append was rolled back") from error

    def list_case_entries_in_transaction(self, case_id: str) -> tuple[ActionProgressEntry, ...]:
        if not self._connection.in_transaction:
            raise ActionPersistenceError("Human progress requires an active read transaction")
        rows = self._connection.execute(
            "SELECT * FROM action_progress WHERE case_id = ? "
            "ORDER BY created_at, action_id, position",
            (case_id,),
        ).fetchall()
        return tuple(self._entry(row, case_id) for row in rows)

    def _view(self, case_id: str, action_id: str) -> ActionProgressView:
        records = ActionService(self._connection).list_case_actions_in_transaction(case_id)
        found = next(
            ((index, item) for index, item in enumerate(records) if item.id == action_id), None
        )
        if found is None:
            raise ActionNotFoundError
        position, action = found
        if (
            action.kind is not ActionKind.PREPARE_COMMUNICATION
            or "analysis_run_id" not in action.payload.to_dict()
        ):
            raise ActionTransitionNotAllowedError(
                "Human progress requires an analysis-bound repair Action"
            )
        rows = self._connection.execute(
            "SELECT * FROM action_progress WHERE action_id = ? ORDER BY position", (action_id,)
        ).fetchall()
        if [int(row["position"]) for row in rows] != list(range(len(rows))):
            raise ActionIntegrityError("Human progress history is incomplete")
        return ActionProgressView(
            action, position, tuple(self._entry(row, case_id) for row in rows)
        )

    def _entry(self, row: sqlite3.Row, case_id: str) -> ActionProgressEntry:
        if str(row["case_id"]) != case_id:
            raise ActionIntegrityError("Human progress Case scope is invalid")
        command = RecordActionProgressCommand(
            case_id=case_id,
            action_id=str(row["action_id"]),
            expected_version=int(row["action_version"]),
            expected_last_progress_id=None,
            kind=row["kind"],
            text=str(row["text"]),
            actor=str(row["actor"]),
            reason=str(row["reason"]),
        )
        authority = self._connection.execute(
            "SELECT 1 FROM action_event WHERE action_id = ? AND case_id = ? AND new_version = ?",
            (command.action_id, case_id, command.expected_version),
        ).fetchone()
        if authority is None:
            raise ActionIntegrityError("Human progress Action version is unavailable")
        created_at = str(row["created_at"])
        if datetime.fromisoformat(created_at.replace("Z", "+00:00")).tzinfo is None:
            raise ActionIntegrityError("Human progress timestamp is invalid")
        return ActionProgressEntry(
            id=validate_progress_text(str(row["id"]), 200),
            action_id=command.action_id,
            case_id=command.case_id,
            action_version=command.expected_version,
            kind=command.kind,
            text=command.text,
            actor=command.actor,
            reason=command.reason,
            created_at=created_at,
        )

    def _require_idle(self) -> None:
        if self._connection.in_transaction:
            raise ActionPersistenceError("Human progress requires an idle connection")

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")
