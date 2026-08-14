"""Transactional Case-scoped Deadline authority."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from lifecase.claims.models import ClaimStatus, ClaimValue

from .models import (
    CreateCalculatedDeadlineCommand,
    CreateExplicitDeadlineCommand,
    CreateSuggestedDeadlineCommand,
    DeadlineBasis,
    DeadlineBasisInput,
    DeadlineDate,
    DeadlineDateKind,
    DeadlineEvent,
    DeadlineEventType,
    DeadlineKind,
    DeadlineRecord,
    DeadlineRuleParameters,
    DeadlineStatus,
    DeadlineStatusCommand,
    DeadlineUncertainty,
)
from .rules import (
    DeadlineRuleError,
    DeadlineRuleNotFoundError,
    calculate_deadline,
)

_AUTHORITATIVE_CLAIM_STATUSES = frozenset({ClaimStatus.CONFIRMED, ClaimStatus.CORRECTED})


class DeadlineServiceError(RuntimeError):
    """Base class for safe Deadline failures."""


class DeadlineNotFoundError(DeadlineServiceError):
    pass


class DeadlineCaseNotFoundError(DeadlineServiceError):
    pass


class DeadlineBasisClaimNotFoundError(DeadlineServiceError):
    pass


class DeadlineBasisInvalidError(DeadlineServiceError):
    pass


class DeadlineBasisStaleError(DeadlineServiceError):
    pass


class DeadlineRuleInvalidError(DeadlineServiceError):
    pass


class DeadlineTransitionNotAllowedError(DeadlineServiceError):
    pass


class DeadlineVersionConflictError(DeadlineServiceError):
    def __init__(self, current_version: int) -> None:
        super().__init__("Deadline version does not match the command")
        self.current_version = current_version


class DeadlineIntegrityError(DeadlineServiceError):
    pass


class DeadlinePersistenceError(DeadlineServiceError):
    pass


class DeadlineService:
    """Own Deadline creation, transitions, provenance checks, and history."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create_explicit(self, command: CreateExplicitDeadlineCommand) -> DeadlineRecord:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._require_case(command.case_id)
            basis, value = self._load_basis(command.case_id, command.basis, command.timezone)
            record_id = self._insert_deadline(
                case_id=command.case_id,
                kind=DeadlineKind.EXPLICIT,
                due=value,
                uncertainty=DeadlineUncertainty.EVIDENCE_BACKED,
                confidence=self._claim_confidence(basis.claim_id),
                rule_id=None,
                rule_version=None,
                rule_parameters=None,
                bases=((basis, command.basis),),
                actor=command.actor,
                reason=command.reason,
            )
            self._connection.execute("COMMIT")
        except DeadlineServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise DeadlinePersistenceError("Deadline creation was rolled back") from error
        return self.get_deadline(record_id)

    def create_calculated(self, command: CreateCalculatedDeadlineCommand) -> DeadlineRecord:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._require_case(command.case_id)
            loaded = tuple(
                (basis, self._load_basis(command.case_id, basis, command.timezone))
                for basis in command.bases
            )
            try:
                calculation = calculate_deadline(
                    rule_id=command.rule_id,
                    rule_version=command.rule_version,
                    bases=tuple(item[1][1] for item in loaded),
                    timezone=command.timezone,
                    parameters=command.rule_parameters,
                )
            except DeadlineRuleError as error:
                raise DeadlineRuleInvalidError("Deadline rule input is invalid") from error
            record_id = self._insert_deadline(
                case_id=command.case_id,
                kind=DeadlineKind.CALCULATED,
                due=calculation.due,
                uncertainty=DeadlineUncertainty.RULE_DERIVED,
                confidence=None,
                rule_id=command.rule_id,
                rule_version=command.rule_version,
                rule_parameters=calculation.parameters,
                bases=tuple((item[1][0], item[0]) for item in loaded),
                actor=command.actor,
                reason=command.reason,
            )
            self._connection.execute("COMMIT")
        except DeadlineServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise DeadlinePersistenceError("Deadline creation was rolled back") from error
        return self.get_deadline(record_id)

    def create_suggested(self, command: CreateSuggestedDeadlineCommand) -> DeadlineRecord:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._require_case(command.case_id)
            record_id = self._insert_deadline(
                case_id=command.case_id,
                kind=DeadlineKind.SUGGESTED,
                due=command.due,
                uncertainty=DeadlineUncertainty.PERSONAL_PREFERENCE,
                confidence=None,
                rule_id=None,
                rule_version=None,
                rule_parameters=None,
                bases=(),
                actor=command.actor,
                reason=command.reason,
            )
            self._connection.execute("COMMIT")
        except DeadlineServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise DeadlinePersistenceError("Deadline creation was rolled back") from error
        return self.get_deadline(record_id)

    def get_deadline(self, deadline_id: str) -> DeadlineRecord:
        try:
            if self._connection.in_transaction:
                raise DeadlinePersistenceError("Deadline read requires an idle connection")
            self._connection.execute("BEGIN")
            result = self._load_deadline(deadline_id)
            self._validate_history(result)
            self._connection.execute("COMMIT")
            return result
        except DeadlineServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise DeadlinePersistenceError("Deadline could not be read") from error

    def list_case_deadlines(self, case_id: str) -> tuple[DeadlineRecord, ...]:
        try:
            if self._connection.in_transaction:
                raise DeadlinePersistenceError("Deadline list requires an idle connection")
            self._connection.execute("BEGIN")
            records = self.list_case_deadlines_in_transaction(case_id)
            self._connection.execute("COMMIT")
            return records
        except DeadlineServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise DeadlinePersistenceError("Deadline list could not be read") from error

    def list_case_deadlines_in_transaction(self, case_id: str) -> tuple[DeadlineRecord, ...]:
        """Read validated records inside a caller-owned stable transaction."""

        if not self._connection.in_transaction:
            raise DeadlinePersistenceError("Deadline transaction read requires a transaction")
        self._require_case(case_id)
        rows = self._connection.execute(
            "SELECT id FROM deadline WHERE case_id = ? ORDER BY created_at, id", (case_id,)
        ).fetchall()
        records = tuple(self._load_deadline(str(row["id"])) for row in rows)
        for record in records:
            self._validate_history(record)
        return records

    def list_deadline_events(self, deadline_id: str) -> tuple[DeadlineEvent, ...]:
        try:
            if self._connection.in_transaction:
                raise DeadlinePersistenceError("Deadline history requires an idle connection")
            self._connection.execute("BEGIN")
            record = self._load_deadline(deadline_id)
            self._validate_history(record)
            events = self._read_events(record.id)
            self._connection.execute("COMMIT")
            return events
        except DeadlineServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise DeadlinePersistenceError("Deadline history could not be read") from error

    def list_deadline_events_in_transaction(self, deadline_id: str) -> tuple[DeadlineEvent, ...]:
        """Read immutable events inside a caller-owned stable transaction."""

        if not self._connection.in_transaction:
            raise DeadlinePersistenceError("Deadline transaction read requires a transaction")
        return self._read_events(deadline_id)

    def confirm(self, command: DeadlineStatusCommand) -> DeadlineRecord:
        return self._transition(command, DeadlineStatus.CONFIRMED, {DeadlineStatus.PROPOSED})

    def mark_passed(self, command: DeadlineStatusCommand) -> DeadlineRecord:
        return self._transition(command, DeadlineStatus.PASSED, {DeadlineStatus.CONFIRMED})

    def complete(self, command: DeadlineStatusCommand) -> DeadlineRecord:
        return self._transition(
            command,
            DeadlineStatus.COMPLETED,
            {DeadlineStatus.CONFIRMED, DeadlineStatus.PASSED},
        )

    def dismiss(self, command: DeadlineStatusCommand) -> DeadlineRecord:
        return self._transition(
            command,
            DeadlineStatus.DISMISSED,
            {DeadlineStatus.PROPOSED, DeadlineStatus.CONFIRMED, DeadlineStatus.PASSED},
        )

    def _transition(
        self,
        command: DeadlineStatusCommand,
        target: DeadlineStatus,
        allowed: set[DeadlineStatus],
    ) -> DeadlineRecord:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._load_deadline(command.deadline_id)
            self._validate_history(current)
            if current.version != command.expected_version:
                raise DeadlineVersionConflictError(current.version)
            if current.status not in allowed:
                raise DeadlineTransitionNotAllowedError
            occurred_at = _utc_now()
            new_version = current.version + 1
            updated = self._connection.execute(
                """
                UPDATE deadline SET status = ?, version = ?, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (target.value, new_version, occurred_at, current.id, current.version),
            )
            if updated.rowcount != 1:
                raise DeadlinePersistenceError("Deadline update was not applied")
            self._insert_event(
                deadline_id=current.id,
                case_id=current.case_id,
                event_type=DeadlineEventType(target.value),
                prior_status=current.status,
                new_status=target,
                prior_version=current.version,
                new_version=new_version,
                definition_sha256=current.definition_sha256,
                actor=command.actor,
                reason=command.reason,
                occurred_at=occurred_at,
            )
            self._connection.execute("COMMIT")
        except DeadlineServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise DeadlinePersistenceError("Deadline transition was rolled back") from error
        return self.get_deadline(command.deadline_id)

    def _insert_deadline(
        self,
        *,
        case_id: str,
        kind: DeadlineKind,
        due: DeadlineDate,
        uncertainty: DeadlineUncertainty,
        confidence: float | None,
        rule_id: str | None,
        rule_version: int | None,
        rule_parameters: DeadlineRuleParameters | None,
        bases: tuple[tuple[DeadlineBasis, DeadlineBasisInput], ...],
        actor: str,
        reason: str,
    ) -> str:
        deadline_id = _new_id("deadline")
        occurred_at = _utc_now()
        projected_bases = tuple(item[0] for item in bases)
        definition_sha256 = _definition_hash(
            kind=kind,
            due=due,
            rule_id=rule_id,
            rule_version=rule_version,
            rule_parameters=rule_parameters,
            bases=projected_bases,
        )
        self._connection.execute(
            """
            INSERT INTO deadline (
                id, case_id, kind, date_kind, due_value, timezone, status,
                uncertainty, confidence, rule_id, rule_version,
                rule_parameters_json, rule_parameters_sha256, version,
                definition_sha256, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                deadline_id,
                case_id,
                kind.value,
                due.kind.value,
                due.value,
                due.timezone,
                uncertainty.value,
                confidence,
                rule_id,
                rule_version,
                rule_parameters.json_text if rule_parameters else None,
                rule_parameters.sha256 if rule_parameters else None,
                definition_sha256,
                occurred_at,
                occurred_at,
            ),
        )
        for position, (basis, _basis_input) in enumerate(bases):
            self._connection.execute(
                """
                INSERT INTO deadline_basis_claim (
                    deadline_id, case_id, position, claim_id, claim_version,
                    value_sha256, date_kind
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deadline_id,
                    case_id,
                    position,
                    basis.claim_id,
                    basis.claim_version,
                    basis.value_sha256,
                    basis.date_kind.value,
                ),
            )
        self._insert_event(
            deadline_id=deadline_id,
            case_id=case_id,
            event_type=DeadlineEventType.CREATED,
            prior_status=None,
            new_status=DeadlineStatus.PROPOSED,
            prior_version=0,
            new_version=1,
            definition_sha256=definition_sha256,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )
        return deadline_id

    def _load_deadline(self, deadline_id: str) -> DeadlineRecord:
        row = self._connection.execute(
            "SELECT * FROM deadline WHERE id = ?", (deadline_id,)
        ).fetchone()
        if row is None:
            raise DeadlineNotFoundError
        kind = DeadlineKind(str(row["kind"]))
        due = DeadlineDate.from_storage(
            kind=_date_kind(str(row["date_kind"])),
            value=str(row["due_value"]),
            timezone=str(row["timezone"]),
        )
        rule_parameters = None
        if row["rule_parameters_json"] is not None:
            rule_parameters = DeadlineRuleParameters(
                str(row["rule_parameters_json"]), str(row["rule_parameters_sha256"])
            )
        basis_rows = self._connection.execute(
            """
            SELECT position, claim_id, claim_version, value_sha256, date_kind
            FROM deadline_basis_claim WHERE deadline_id = ? ORDER BY position
            """,
            (deadline_id,),
        ).fetchall()
        if tuple(int(item["position"]) for item in basis_rows) != tuple(range(len(basis_rows))):
            raise DeadlineIntegrityError("Deadline basis order is invalid")
        bases: list[DeadlineBasis] = []
        basis_dates: list[DeadlineDate] = []
        for basis_row in basis_rows:
            input_value = DeadlineBasisInput(
                claim_id=str(basis_row["claim_id"]),
                expected_claim_version=int(basis_row["claim_version"]),
                value_sha256=str(basis_row["value_sha256"]),
                date_kind=_date_kind(str(basis_row["date_kind"])),
            )
            basis, basis_date = self._load_basis(str(row["case_id"]), input_value, due.timezone)
            bases.append(basis)
            basis_dates.append(basis_date)
        if kind is DeadlineKind.EXPLICIT:
            if len(bases) != 1 or rule_parameters is not None or basis_dates[0] != due:
                raise DeadlineIntegrityError("Explicit Deadline authority is invalid")
        elif kind is DeadlineKind.CALCULATED:
            if rule_parameters is None or row["rule_id"] is None or row["rule_version"] is None:
                raise DeadlineIntegrityError("Calculated Deadline authority is invalid")
            try:
                expected = calculate_deadline(
                    rule_id=str(row["rule_id"]),
                    rule_version=int(row["rule_version"]),
                    bases=tuple(basis_dates),
                    timezone=due.timezone,
                    parameters=rule_parameters,
                ).due
            except DeadlineRuleNotFoundError as error:
                raise DeadlineRuleInvalidError("Stored Deadline rule is unavailable") from error
            except DeadlineRuleError as error:
                raise DeadlineIntegrityError("Calculated Deadline authority is invalid") from error
            if expected != due:
                raise DeadlineIntegrityError("Calculated Deadline result is invalid")
        elif bases or rule_parameters is not None:
            raise DeadlineIntegrityError("Suggested Deadline authority is invalid")
        result = DeadlineRecord(
            id=str(row["id"]),
            case_id=str(row["case_id"]),
            kind=kind,
            due=due,
            status=DeadlineStatus(str(row["status"])),
            uncertainty=DeadlineUncertainty(str(row["uncertainty"])),
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            rule_id=str(row["rule_id"]) if row["rule_id"] is not None else None,
            rule_version=int(row["rule_version"]) if row["rule_version"] is not None else None,
            rule_parameters=rule_parameters,
            bases=tuple(bases),
            version=int(row["version"]),
            definition_sha256=str(row["definition_sha256"]),
            created_at=_timestamp(str(row["created_at"])),
            updated_at=_timestamp(str(row["updated_at"])),
        )
        expected_uncertainty = {
            DeadlineKind.EXPLICIT: DeadlineUncertainty.EVIDENCE_BACKED,
            DeadlineKind.CALCULATED: DeadlineUncertainty.RULE_DERIVED,
            DeadlineKind.SUGGESTED: DeadlineUncertainty.PERSONAL_PREFERENCE,
        }[result.kind]
        if result.uncertainty is not expected_uncertainty:
            raise DeadlineIntegrityError("Deadline kind semantics are invalid")
        if result.kind is DeadlineKind.EXPLICIT:
            expected_confidence = self._claim_confidence(result.bases[0].claim_id)
            if result.confidence != expected_confidence:
                raise DeadlineIntegrityError("Explicit Deadline confidence is invalid")
        elif result.confidence is not None:
            raise DeadlineIntegrityError("Deadline confidence semantics are invalid")
        expected_hash = _definition_hash(
            kind=result.kind,
            due=result.due,
            rule_id=result.rule_id,
            rule_version=result.rule_version,
            rule_parameters=result.rule_parameters,
            bases=result.bases,
        )
        if result.definition_sha256 != expected_hash:
            raise DeadlineIntegrityError("Deadline definition hash is invalid")
        return result

    def _load_basis(
        self,
        case_id: str,
        basis: DeadlineBasisInput,
        timezone: str,
    ) -> tuple[DeadlineBasis, DeadlineDate]:
        row = self._connection.execute(
            """
            SELECT id, case_id, field, value_json, value_sha256, comparison_sha256,
                   status, contradiction, version
            FROM claim WHERE id = ?
            """,
            (basis.claim_id,),
        ).fetchone()
        if row is None:
            raise DeadlineBasisClaimNotFoundError
        if str(row["case_id"]) != case_id:
            raise DeadlineBasisInvalidError("Basis Claim belongs to another Case")
        if (
            int(row["version"]) != basis.expected_claim_version
            or str(row["value_sha256"]) != basis.value_sha256
        ):
            raise DeadlineBasisStaleError("Basis Claim version or value has changed")
        try:
            status = ClaimStatus(str(row["status"]))
            value = ClaimValue.from_storage(
                str(row["value_json"]),
                str(row["value_sha256"]),
                str(row["comparison_sha256"]),
            )
        except ValueError as error:
            raise DeadlineBasisInvalidError("Basis Claim authority is invalid") from error
        if status not in _AUTHORITATIVE_CLAIM_STATUSES or bool(row["contradiction"]):
            raise DeadlineBasisInvalidError("Basis Claim is not authoritative")
        raw = value.to_python()
        if not isinstance(raw, str):
            raise DeadlineBasisInvalidError("Basis Claim is not a date string")
        try:
            due = DeadlineDate(kind=basis.date_kind, value=raw, timezone=timezone)
        except ValueError as error:
            raise DeadlineBasisInvalidError("Basis Claim date is invalid") from error
        return (
            DeadlineBasis(
                claim_id=str(row["id"]),
                claim_version=int(row["version"]),
                value_sha256=str(row["value_sha256"]),
                field=str(row["field"]),
                date_kind=basis.date_kind,
            ),
            due,
        )

    def _claim_confidence(self, claim_id: str) -> float | None:
        row = self._connection.execute(
            "SELECT confidence FROM claim WHERE id = ?", (claim_id,)
        ).fetchone()
        return float(row[0]) if row is not None and row[0] is not None else None

    def _validate_history(self, record: DeadlineRecord) -> None:
        events = self._read_events(record.id)
        if tuple(item.new_version for item in events) != tuple(range(1, record.version + 1)):
            raise DeadlineIntegrityError("Deadline history is incomplete")
        if (
            not events
            or events[0].event_type is not DeadlineEventType.CREATED
            or events[-1].new_status is not record.status
            or any(item.definition_sha256 != record.definition_sha256 for item in events)
            or any(
                item.deadline_id != record.id
                or item.case_id != record.case_id
                or (
                    item.event_type is not DeadlineEventType.CREATED
                    and item.event_type.value != item.new_status.value
                )
                for item in events
            )
        ):
            raise DeadlineIntegrityError("Deadline history is inconsistent")
        for previous, current in zip(events, events[1:], strict=False):
            if current.prior_status is not previous.new_status:
                raise DeadlineIntegrityError("Deadline history is inconsistent")

    def _read_events(self, deadline_id: str) -> tuple[DeadlineEvent, ...]:
        rows = self._connection.execute(
            "SELECT * FROM deadline_event WHERE deadline_id = ? ORDER BY new_version, id",
            (deadline_id,),
        ).fetchall()
        return tuple(
            DeadlineEvent(
                id=str(row["id"]),
                deadline_id=str(row["deadline_id"]),
                case_id=str(row["case_id"]),
                event_type=DeadlineEventType(str(row["event_type"])),
                prior_status=(
                    DeadlineStatus(str(row["prior_status"]))
                    if row["prior_status"] is not None
                    else None
                ),
                new_status=DeadlineStatus(str(row["new_status"])),
                prior_version=int(row["prior_version"]),
                new_version=int(row["new_version"]),
                definition_sha256=str(row["definition_sha256"]),
                actor=str(row["actor"]),
                reason=str(row["reason"]),
                occurred_at=_timestamp(str(row["occurred_at"])),
            )
            for row in rows
        )

    def _insert_event(
        self,
        *,
        deadline_id: str,
        case_id: str,
        event_type: DeadlineEventType,
        prior_status: DeadlineStatus | None,
        new_status: DeadlineStatus,
        prior_version: int,
        new_version: int,
        definition_sha256: str,
        actor: str,
        reason: str,
        occurred_at: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO deadline_event (
                id, deadline_id, case_id, event_type, prior_status, new_status,
                prior_version, new_version, definition_sha256, actor, reason, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("deadline_event"),
                deadline_id,
                case_id,
                event_type.value,
                prior_status.value if prior_status else None,
                new_status.value,
                prior_version,
                new_version,
                definition_sha256,
                actor,
                reason,
                occurred_at,
            ),
        )

    def _require_case(self, case_id: str) -> None:
        if (
            self._connection.execute(
                "SELECT 1 FROM case_record WHERE id = ?", (case_id,)
            ).fetchone()
            is None
        ):
            raise DeadlineCaseNotFoundError

    def _rollback(self) -> None:
        try:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def _definition_hash(
    *,
    kind: DeadlineKind,
    due: DeadlineDate,
    rule_id: str | None,
    rule_version: int | None,
    rule_parameters: DeadlineRuleParameters | None,
    bases: tuple[DeadlineBasis, ...],
) -> str:
    payload = {
        "kind": kind.value,
        "date_kind": due.kind.value,
        "due_value": due.value,
        "timezone": due.timezone,
        "rule_id": rule_id,
        "rule_version": rule_version,
        "rule_parameters_sha256": rule_parameters.sha256 if rule_parameters else None,
        "bases": [
            {
                "claim_id": item.claim_id,
                "claim_version": item.claim_version,
                "value_sha256": item.value_sha256,
                "date_kind": item.date_kind.value,
            }
            for item in bases
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _date_kind(value: str) -> DeadlineDateKind:
    return DeadlineDateKind(value)


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise DeadlineIntegrityError("Deadline timestamp is invalid")
    return value


def _new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}_{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
