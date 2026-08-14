"""Deterministic read-only Timeline projection."""

import hashlib
import sqlite3
from datetime import datetime

from lifecase.actions import (
    ActionEventType,
    ActionPersistenceError,
    ActionService,
    ActionServiceError,
)
from lifecase.agent.models import AgentRunStatus, AgentStageName, AgentStageStatus
from lifecase.approvals import (
    ApprovalEventType,
    ApprovalPersistenceError,
    ApprovalService,
    ApprovalServiceError,
)
from lifecase.cases import (
    CaseNotFoundError,
    CasePersistenceError,
    CaseRecord,
    CaseService,
    CaseServiceError,
    CaseValidationError,
)
from lifecase.claims import (
    ClaimCreatedBy,
    ClaimEventType,
    ClaimPersistenceError,
    ClaimService,
    ClaimServiceError,
)
from lifecase.deadlines import (
    DeadlineEventType,
    DeadlinePersistenceError,
    DeadlineService,
    DeadlineServiceError,
)

from .errors import (
    TimelineCaseNotFoundError,
    TimelineError,
    TimelineIntegrityError,
    TimelinePersistenceError,
    TimelineValidationError,
)
from .models import (
    TimelineAuthorityType,
    TimelineEvent,
    TimelineEventKind,
    TimelineOrigin,
    TimelineResult,
)

_EVENT_KIND_BY_CLAIM_TYPE = {
    ClaimEventType.CREATED: TimelineEventKind.CLAIM_CREATED,
    ClaimEventType.SOURCE_ATTACHED: TimelineEventKind.CLAIM_SOURCE_ATTACHED,
    ClaimEventType.CONFIRMED: TimelineEventKind.CLAIM_CONFIRMED,
    ClaimEventType.CORRECTED: TimelineEventKind.CLAIM_CORRECTED,
    ClaimEventType.REJECTED: TimelineEventKind.CLAIM_REJECTED,
}

_EVENT_KIND_RANK = {kind: index for index, kind in enumerate(TimelineEventKind)}
_EVENT_KIND_BY_DEADLINE_TYPE = {
    DeadlineEventType.CREATED: TimelineEventKind.DEADLINE_CREATED,
    DeadlineEventType.CONFIRMED: TimelineEventKind.DEADLINE_CONFIRMED,
    DeadlineEventType.PASSED: TimelineEventKind.DEADLINE_PASSED,
    DeadlineEventType.COMPLETED: TimelineEventKind.DEADLINE_COMPLETED,
    DeadlineEventType.DISMISSED: TimelineEventKind.DEADLINE_DISMISSED,
}
_EVENT_KIND_BY_ACTION_TYPE = {
    ActionEventType.CREATED: TimelineEventKind.ACTION_CREATED,
    ActionEventType.REVISED: TimelineEventKind.ACTION_REVISED,
    ActionEventType.REVALIDATED: TimelineEventKind.ACTION_REVALIDATED,
    ActionEventType.STARTED: TimelineEventKind.ACTION_STARTED,
    ActionEventType.COMPLETED: TimelineEventKind.ACTION_COMPLETED,
    ActionEventType.FAILED: TimelineEventKind.ACTION_FAILED,
    ActionEventType.CANCELLED: TimelineEventKind.ACTION_CANCELLED,
    ActionEventType.APPROVED: TimelineEventKind.ACTION_APPROVED,
}
_EVENT_KIND_BY_APPROVAL_TYPE = {
    ApprovalEventType.REQUESTED: TimelineEventKind.APPROVAL_REQUESTED,
    ApprovalEventType.APPROVED: TimelineEventKind.APPROVAL_APPROVED,
    ApprovalEventType.REJECTED: TimelineEventKind.APPROVAL_REJECTED,
    ApprovalEventType.EXPIRED: TimelineEventKind.APPROVAL_EXPIRED,
    ApprovalEventType.INVALIDATED: TimelineEventKind.APPROVAL_INVALIDATED,
}
_ARTIFACT_TYPES = {
    "source",
    "derived_text",
    "thumbnail",
    "draft",
    "calendar",
    "pack",
    "manifest",
    "backup",
}


class TimelineService:
    """Build a stable Timeline without creating a second history store."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_case_timeline(self, case_id: str) -> TimelineResult:
        """Return all currently admitted authoritative events for one Case."""

        try:
            if self._connection.in_transaction:
                raise TimelinePersistenceError("Timeline requires an idle connection")
            self._connection.execute("BEGIN")
            result = self.list_case_timeline_in_transaction(case_id)
            self._connection.execute("COMMIT")
            return result
        except TimelineError:
            _rollback(self._connection)
            raise
        except CaseNotFoundError as error:
            _rollback(self._connection)
            raise TimelineCaseNotFoundError from error
        except CaseValidationError as error:
            _rollback(self._connection)
            raise TimelineValidationError("Timeline request is invalid") from error
        except (
            ActionPersistenceError,
            ApprovalPersistenceError,
            CasePersistenceError,
            ClaimPersistenceError,
            DeadlinePersistenceError,
        ) as error:
            _rollback(self._connection)
            raise TimelinePersistenceError("Timeline could not be read") from error
        except (
            ActionServiceError,
            ApprovalServiceError,
            CaseServiceError,
            ClaimServiceError,
            DeadlineServiceError,
        ) as error:
            _rollback(self._connection)
            raise TimelineIntegrityError("Timeline authority is invalid") from error
        except (sqlite3.Error, IndexError, TypeError, ValueError) as error:
            _rollback(self._connection)
            raise TimelinePersistenceError("Timeline could not be read") from error

    def list_case_timeline_in_transaction(self, case_id: str) -> TimelineResult:
        """Return one projection while the caller owns the stable SQLite snapshot."""

        if not self._connection.in_transaction:
            raise TimelinePersistenceError("Timeline requires an active transaction")
        case = CaseService(self._connection).get_case(case_id)
        events = [
            *self._source_events(case.id),
            *self._artifact_events(case.id),
            *self._transition_events(case),
            *self._claim_events(case.id),
            *self._agent_events(case.id),
            *self._deadline_events(case.id),
            *self._action_events(case.id),
            *self._approval_events(case.id),
        ]
        events.sort(key=_event_order)
        return TimelineResult(case_id=case.id, events=tuple(events))

    def _source_events(self, case_id: str) -> list[TimelineEvent]:
        rows = self._connection.execute(
            """
            SELECT s.id, s.case_id, s.original_name, s.sha256, s.imported_at,
                   e.extractor_version, e.status AS extraction_status,
                   e.segment_count, e.updated_at AS extraction_updated_at,
                   (SELECT COUNT(*) FROM source_segment AS ss
                    WHERE ss.source_id = s.id) AS authoritative_segment_count
            FROM source AS s
            LEFT JOIN source_extraction AS e ON e.source_id = s.id
            WHERE s.case_id = ?
            ORDER BY s.imported_at, s.id
            """,
            (case_id,),
        ).fetchall()
        events: list[TimelineEvent] = []
        for row in rows:
            source_id = _text(row, "id")
            _same_case(row, case_id)
            _sha256(row, "sha256")
            source_name = _bounded_text(row, "original_name", maximum=255)
            imported_at = _timestamp(row, "imported_at")
            events.append(
                _event(
                    case_id=case_id,
                    kind=TimelineEventKind.SOURCE_IMPORTED,
                    occurred_at=imported_at,
                    authority_type=TimelineAuthorityType.SOURCE,
                    authority_id=source_id,
                    subject_id=source_id,
                    origin=TimelineOrigin.LOCAL_IMPORT,
                    label=source_name,
                    status="imported",
                )
            )
            extraction_status = row["extraction_status"]
            if extraction_status is None:
                continue
            if int(row["segment_count"]) != int(row["authoritative_segment_count"]):
                raise TimelineIntegrityError("Timeline Source authority is invalid")
            events.append(
                _event(
                    case_id=case_id,
                    kind=TimelineEventKind.SOURCE_EXTRACTION_STATUS,
                    occurred_at=_timestamp(row, "extraction_updated_at"),
                    authority_type=TimelineAuthorityType.SOURCE_EXTRACTION,
                    authority_id=source_id,
                    subject_id=source_id,
                    origin=TimelineOrigin.EXTRACTOR,
                    label=_optional_bounded_text(row, "extractor_version", maximum=64),
                    status=_bounded_text(row, "extraction_status", maximum=64),
                )
            )
        return events

    def _artifact_events(self, case_id: str) -> list[TimelineEvent]:
        rows = self._connection.execute(
            """
            SELECT a.id, a.case_id, a.source_id, a.type, a.sha256, a.size_bytes,
                   a.created_by_run, a.created_at,
                   s.case_id AS source_case_id, r.case_id AS run_case_id
            FROM artifact AS a
            LEFT JOIN source AS s ON s.id = a.source_id
            LEFT JOIN agent_run AS r ON r.id = a.created_by_run
            WHERE a.case_id = ?
            ORDER BY a.created_at, a.id
            """,
            (case_id,),
        ).fetchall()
        events: list[TimelineEvent] = []
        for row in rows:
            _same_case(row, case_id)
            source_id = _optional_text(row, "source_id")
            run_id = _optional_text(row, "created_by_run")
            if source_id is not None and row["source_case_id"] != case_id:
                raise TimelineIntegrityError("Timeline Artifact authority is invalid")
            if run_id is not None and row["run_case_id"] != case_id:
                raise TimelineIntegrityError("Timeline Artifact authority is invalid")
            if int(row["size_bytes"]) < 0:
                raise TimelineIntegrityError("Timeline Artifact authority is invalid")
            _sha256(row, "sha256")
            artifact_id = _text(row, "id")
            artifact_type = _bounded_text(row, "type", maximum=64)
            if artifact_type not in _ARTIFACT_TYPES:
                raise TimelineIntegrityError("Timeline Artifact authority is invalid")
            events.append(
                _event(
                    case_id=case_id,
                    kind=TimelineEventKind.ARTIFACT_CREATED,
                    occurred_at=_timestamp(row, "created_at"),
                    authority_type=TimelineAuthorityType.ARTIFACT,
                    authority_id=artifact_id,
                    subject_id=artifact_id,
                    parent_id=source_id or run_id,
                    origin=(TimelineOrigin.AGENT if run_id else TimelineOrigin.ARTIFACT_SERVICE),
                    label=artifact_type,
                    status="created",
                )
            )
        return events

    def _transition_events(self, case: CaseRecord) -> list[TimelineEvent]:
        events: list[TimelineEvent] = []
        transitions = CaseService(self._connection).list_case_transitions(case.id)
        if tuple(transition.new_version for transition in transitions) != tuple(
            range(2, case.version + 1)
        ):
            raise TimelineIntegrityError("Timeline Case history is invalid")
        if transitions and transitions[-1].new_status is not case.status:
            raise TimelineIntegrityError("Timeline Case history is invalid")
        for transition in transitions:
            events.append(
                _event(
                    case_id=case.id,
                    kind=TimelineEventKind.CASE_TRANSITIONED,
                    occurred_at=_validated_timestamp(transition.transitioned_at),
                    authority_type=TimelineAuthorityType.CASE_TRANSITION,
                    authority_id=transition.id,
                    subject_id=case.id,
                    origin=TimelineOrigin.RECORDED_ACTOR,
                    actor=transition.actor,
                    status=transition.new_status.value,
                    prior_status=transition.prior_status.value,
                    version=transition.new_version,
                    reason=transition.reason,
                )
            )
        return events

    def _claim_events(self, case_id: str) -> list[TimelineEvent]:
        service = ClaimService(self._connection)
        events: list[TimelineEvent] = []
        for claim in service.list_case_claims(case_id):
            claim_events = service.list_claim_events(claim.id)
            if tuple(event.new_version for event in claim_events) != tuple(
                range(1, claim.version + 1)
            ):
                raise TimelineIntegrityError("Timeline Claim history is invalid")
            if (
                not claim_events
                or claim_events[-1].new_status is not claim.status
                or claim_events[-1].new_value != claim.value
            ):
                raise TimelineIntegrityError("Timeline Claim history is invalid")
            for claim_event in claim_events:
                events.append(
                    _event(
                        case_id=case_id,
                        kind=_EVENT_KIND_BY_CLAIM_TYPE[claim_event.event_type],
                        occurred_at=_validated_timestamp(claim_event.occurred_at),
                        authority_type=TimelineAuthorityType.CLAIM_EVENT,
                        authority_id=claim_event.id,
                        subject_id=claim.id,
                        origin=(
                            TimelineOrigin.AGENT
                            if claim_event.event_type is ClaimEventType.CREATED
                            and claim.created_by is ClaimCreatedBy.AGENT
                            else TimelineOrigin.RECORDED_ACTOR
                        ),
                        actor=claim_event.actor,
                        label=claim.field,
                        status=claim_event.new_status.value,
                        prior_status=(
                            claim_event.prior_status.value
                            if claim_event.prior_status is not None
                            else None
                        ),
                        version=claim_event.new_version,
                        reason=claim_event.reason,
                    )
                )
        return events

    def _agent_events(self, case_id: str) -> list[TimelineEvent]:
        run_rows = self._connection.execute(
            "SELECT * FROM agent_run WHERE case_id = ? ORDER BY created_at, id",
            (case_id,),
        ).fetchall()
        events: list[TimelineEvent] = []
        for row in run_rows:
            _same_case(row, case_id)
            run_id = _text(row, "id")
            run_status = _agent_run_status(_text(row, "status"))
            current_stage = _optional_text(row, "current_stage")
            last_completed_stage = _optional_text(row, "last_completed_stage")
            if current_stage is not None:
                _agent_stage_name(current_stage)
            if last_completed_stage is not None:
                _agent_stage_name(last_completed_stage)
            events.append(
                _event(
                    case_id=case_id,
                    kind=TimelineEventKind.AGENT_RUN_STATUS,
                    occurred_at=_timestamp(row, "updated_at"),
                    authority_type=TimelineAuthorityType.AGENT_RUN,
                    authority_id=run_id,
                    subject_id=run_id,
                    origin=TimelineOrigin.AGENT,
                    label=current_stage,
                    status=run_status.value,
                    version=_positive_int(row, "version"),
                )
            )
            stage_rows = self._connection.execute(
                """
                SELECT * FROM agent_stage_run
                WHERE run_id = ? AND case_id = ?
                ORDER BY updated_at, id
                """,
                (run_id, case_id),
            ).fetchall()
            for stage_row in stage_rows:
                _same_case(stage_row, case_id)
                stage = _agent_stage_name(_text(stage_row, "stage"))
                status = _agent_stage_status(_text(stage_row, "status"))
                checkpoint = stage_row["checkpoint_json"]
                checkpoint_hash = stage_row["checkpoint_sha256"]
                if (checkpoint is None) != (checkpoint_hash is None):
                    raise TimelineIntegrityError("Timeline Agent authority is invalid")
                if checkpoint is not None:
                    actual_hash = hashlib.sha256(str(checkpoint).encode()).hexdigest()
                    if actual_hash != str(checkpoint_hash):
                        raise TimelineIntegrityError("Timeline Agent authority is invalid")
                stage_id = _text(stage_row, "id")
                events.append(
                    _event(
                        case_id=case_id,
                        kind=TimelineEventKind.AGENT_STAGE_STATUS,
                        occurred_at=_timestamp(stage_row, "updated_at"),
                        authority_type=TimelineAuthorityType.AGENT_STAGE_RUN,
                        authority_id=stage_id,
                        subject_id=stage_id,
                        parent_id=run_id,
                        origin=TimelineOrigin.AGENT,
                        label=stage.value,
                        status=status.value,
                        version=_positive_int(stage_row, "version"),
                    )
                )
        return events

    def _deadline_events(self, case_id: str) -> list[TimelineEvent]:
        service = DeadlineService(self._connection)
        events: list[TimelineEvent] = []
        for deadline in service.list_case_deadlines_in_transaction(case_id):
            for item in service.list_deadline_events_in_transaction(deadline.id):
                events.append(
                    _event(
                        case_id=case_id,
                        kind=_EVENT_KIND_BY_DEADLINE_TYPE[item.event_type],
                        occurred_at=_validated_timestamp(item.occurred_at),
                        authority_type=TimelineAuthorityType.DEADLINE_EVENT,
                        authority_id=item.id,
                        subject_id=deadline.id,
                        parent_id=deadline.bases[0].claim_id if deadline.bases else None,
                        origin=TimelineOrigin.RECORDED_ACTOR,
                        actor=item.actor,
                        label=deadline.kind.value,
                        status=item.new_status.value,
                        prior_status=(item.prior_status.value if item.prior_status else None),
                        version=item.new_version,
                        reason=item.reason,
                    )
                )
        return events

    def _action_events(self, case_id: str) -> list[TimelineEvent]:
        service = ActionService(self._connection)
        events: list[TimelineEvent] = []
        for action in service.list_case_actions_in_transaction(case_id):
            for item in service.list_action_events_in_transaction(action.id):
                events.append(
                    _event(
                        case_id=case_id,
                        kind=_EVENT_KIND_BY_ACTION_TYPE[item.event_type],
                        occurred_at=_validated_timestamp(item.occurred_at),
                        authority_type=TimelineAuthorityType.ACTION_EVENT,
                        authority_id=item.id,
                        subject_id=action.id,
                        parent_id=(
                            action.dependencies[0].action_id if action.dependencies else None
                        ),
                        origin=TimelineOrigin.RECORDED_ACTOR,
                        actor=item.actor,
                        label=action.kind.value,
                        status=item.new_status.value,
                        prior_status=(item.prior_status.value if item.prior_status else None),
                        version=item.new_version,
                        reason=item.reason,
                    )
                )
        return events

    def _approval_events(self, case_id: str) -> list[TimelineEvent]:
        service = ApprovalService(self._connection)
        events: list[TimelineEvent] = []
        for approval in service.list_case_approvals_in_transaction(case_id):
            for item in service.list_approval_events_in_transaction(approval.id):
                events.append(
                    _event(
                        case_id=case_id,
                        kind=_EVENT_KIND_BY_APPROVAL_TYPE[item.event_type],
                        occurred_at=_validated_timestamp(item.occurred_at),
                        authority_type=TimelineAuthorityType.APPROVAL_EVENT,
                        authority_id=item.id,
                        subject_id=approval.id,
                        parent_id=approval.action_id,
                        origin=TimelineOrigin.RECORDED_ACTOR,
                        actor=item.actor,
                        label="local_approval",
                        status=item.new_status.value,
                        prior_status=(item.prior_status.value if item.prior_status else None),
                        version=item.new_version,
                        reason=item.reason,
                    )
                )
        return events


def _event(
    *,
    case_id: str,
    kind: TimelineEventKind,
    occurred_at: str,
    authority_type: TimelineAuthorityType,
    authority_id: str,
    subject_id: str,
    origin: TimelineOrigin,
    parent_id: str | None = None,
    actor: str | None = None,
    label: str | None = None,
    status: str | None = None,
    prior_status: str | None = None,
    version: int | None = None,
    reason: str | None = None,
) -> TimelineEvent:
    return TimelineEvent(
        event_id=f"{kind.value}:{authority_id}",
        case_id=case_id,
        kind=kind,
        occurred_at=occurred_at,
        authority_type=authority_type,
        authority_id=authority_id,
        subject_id=subject_id,
        parent_id=parent_id,
        origin=origin,
        actor=actor,
        label=label,
        status=status,
        prior_status=prior_status,
        version=version,
        reason=reason,
    )


def _event_order(event: TimelineEvent) -> tuple[object, ...]:
    return (
        datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00")),
        _EVENT_KIND_RANK[event.kind],
        event.authority_id,
    )


def _validated_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise TimelineIntegrityError("Timeline timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise TimelineIntegrityError("Timeline timestamp is invalid")
    return value


def _timestamp(row: sqlite3.Row, key: str) -> str:
    return _validated_timestamp(_text(row, key))


def _same_case(row: sqlite3.Row, case_id: str) -> None:
    if _text(row, "case_id") != case_id:
        raise TimelineIntegrityError("Timeline Case authority is invalid")


def _text(row: sqlite3.Row, key: str) -> str:
    value = row[key]
    if not isinstance(value, str) or not value:
        raise TimelineIntegrityError("Timeline authority is invalid")
    return value


def _optional_text(row: sqlite3.Row, key: str) -> str | None:
    return _text(row, key) if row[key] is not None else None


def _bounded_text(row: sqlite3.Row, key: str, *, maximum: int) -> str:
    value = _text(row, key)
    if len(value) > maximum:
        raise TimelineIntegrityError("Timeline authority is invalid")
    return value


def _optional_bounded_text(row: sqlite3.Row, key: str, *, maximum: int) -> str | None:
    return _bounded_text(row, key, maximum=maximum) if row[key] is not None else None


def _sha256(row: sqlite3.Row, key: str) -> str:
    value = _text(row, key)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise TimelineIntegrityError("Timeline authority is invalid")
    return value


def _positive_int(row: sqlite3.Row, key: str) -> int:
    value = int(row[key])
    if value <= 0:
        raise TimelineIntegrityError("Timeline authority is invalid")
    return value


def _agent_run_status(value: str) -> AgentRunStatus:
    try:
        return AgentRunStatus(value)
    except ValueError as error:
        raise TimelineIntegrityError("Timeline Agent authority is invalid") from error


def _agent_stage_name(value: str) -> AgentStageName:
    try:
        return AgentStageName(value)
    except ValueError as error:
        raise TimelineIntegrityError("Timeline Agent authority is invalid") from error


def _agent_stage_status(value: str) -> AgentStageStatus:
    try:
        return AgentStageStatus(value)
    except ValueError as error:
        raise TimelineIntegrityError("Timeline Agent authority is invalid") from error


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass
