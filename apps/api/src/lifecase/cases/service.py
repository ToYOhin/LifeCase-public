"""Deterministic Case aggregate creation, reads, and lifecycle transitions."""

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from lifecase.cases.models import (
    CaseActivityKind,
    CaseDeadlineRisk,
    CaseMissingEvidence,
    CaseNextActionSummary,
    CaseRecord,
    CaseStatus,
    CaseTransition,
    CaseTransitionCommand,
    CaseTransitionResult,
    CaseWorkbenchItem,
)
from lifecase.cases.state_machine import is_transition_allowed
from lifecase.templates.models import EvaluateTemplateCommand, TemplateEvaluation
from lifecase.templates.registry import (
    BUILT_IN_TEMPLATE_REGISTRY,
    TemplateNotFoundError,
    TemplateRegistryError,
)

_TODAY_STATUSES = frozenset(
    {
        CaseStatus.INTAKE,
        CaseStatus.NEEDS_REVIEW,
        CaseStatus.READY,
        CaseStatus.IN_PROGRESS,
        CaseStatus.WAITING_EXTERNAL,
        CaseStatus.BLOCKED,
    }
)


class CaseServiceError(RuntimeError):
    """Base class for safe Case service failures."""


class CaseNotFoundError(CaseServiceError):
    """Raised when a requested Case does not exist."""


class CaseValidationError(CaseServiceError):
    """Raised when Case input violates the M1 contract."""


class CasePersistenceError(CaseServiceError):
    """Raised when Case state cannot be read or committed."""


class CaseTemplateBindingError(CaseServiceError):
    """Raised when a Case cannot be matched to a reviewed template binding."""


class CaseTransitionNotAllowedError(CaseServiceError):
    """Raised when a lifecycle edge is not permitted."""


class CaseVersionConflictError(CaseServiceError):
    """Raised when a transition command targets a stale Case version."""

    def __init__(self, current_version: int) -> None:
        super().__init__("Case version does not match the transition command")
        self.current_version = current_version


class CaseService:
    """Create and retrieve Cases without transport dependencies."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create_case(self, *, title: str, goal: str, template_id: str) -> CaseRecord:
        """Create a Case from the legacy API selector, which is the business Case type."""

        clean_title = title.strip()
        clean_goal = goal.strip()
        if not clean_title or not clean_goal:
            raise CaseValidationError
        if len(clean_title) > 160 or len(clean_goal) > 1000:
            raise CaseValidationError

        try:
            template = BUILT_IN_TEMPLATE_REGISTRY.load_for(template_id, 1)
        except TemplateNotFoundError as error:
            raise CaseValidationError from error
        except TemplateRegistryError as error:
            raise CaseTemplateBindingError("Case template binding is invalid") from error

        case_id = _new_case_id()
        created_at = _utc_now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                INSERT INTO case_record (
                    id, type, template_id, template_version, title, goal, status,
                    priority, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'intake', 0, 1, ?, ?)
                """,
                (
                    case_id,
                    template_id,
                    template.id,
                    template.version,
                    clean_title,
                    clean_goal,
                    created_at,
                    created_at,
                ),
            )
            self._connection.execute("COMMIT")
        except sqlite3.Error as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise CasePersistenceError("Case transaction was rolled back") from error
        return self.get_case(case_id)

    def get_case(self, case_id: str) -> CaseRecord:
        try:
            row = self._connection.execute(
                """
                SELECT id, type, template_id, template_version, title, goal, status,
                       priority, version, created_at, updated_at, closed_at
                FROM case_record WHERE id = ?
                """,
                (case_id,),
            ).fetchone()
            if row is None:
                raise CaseNotFoundError
            record = _case_record(row)
            _validate_template_binding(record)
            return record
        except CaseServiceError:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise CasePersistenceError("Case lookup failed") from error

    def list_workbench_cases(self) -> tuple[CaseWorkbenchItem, ...]:
        """Return the deterministic read-only M4-01 list and Today projection."""

        from lifecase.actions import ActionService, ActionServiceError
        from lifecase.templates.service import TemplateService, TemplateServiceError

        try:
            if self._connection.in_transaction:
                raise CasePersistenceError("Case list requires an idle connection")
            self._connection.execute("BEGIN")
            rows = self._connection.execute(
                """
                SELECT id, type, template_id, template_version, title, goal, status,
                       priority, version, created_at, updated_at, closed_at
                FROM case_record
                """
            ).fetchall()
            records = tuple(_case_record(row) for row in rows)
            for record in records:
                _validate_template_binding(record)
            activities = self._latest_activities()
            templates = TemplateService(self._connection)
            actions = ActionService(self._connection)
            items = tuple(
                _workbench_item(
                    record,
                    activities[record.id],
                    templates.evaluate(EvaluateTemplateCommand(case_id=record.id)),
                    actions.list_case_actions_in_transaction(record.id),
                )
                for record in records
            )
            ordered = tuple(sorted(items, key=_workbench_order))
            self._connection.execute("COMMIT")
            return ordered
        except CaseServiceError:
            _rollback(self._connection)
            raise
        except (ActionServiceError, TemplateServiceError, sqlite3.Error, ValueError) as error:
            _rollback(self._connection)
            raise CasePersistenceError("Case list could not be read") from error

    def _latest_activities(self) -> dict[str, tuple[str, CaseActivityKind]]:
        rows = self._connection.execute(
            """
            SELECT case_id, activity_kind, occurred_at
            FROM (
                SELECT id AS case_id, 'case_updated' AS activity_kind,
                       updated_at AS occurred_at, 0 AS kind_rank, id AS activity_id
                FROM case_record
                UNION ALL
                SELECT case_id, 'source_imported', imported_at, 1, id
                FROM source
                UNION ALL
                SELECT source.case_id, 'extraction_updated', extraction.updated_at, 2,
                       extraction.source_id
                FROM source_extraction AS extraction
                JOIN source ON source.id = extraction.source_id
                UNION ALL
                SELECT case_id, 'claim_updated', updated_at, 3, id
                FROM claim
                UNION ALL
                SELECT case_id, 'agent_updated', updated_at, 4, id
                FROM agent_run
                UNION ALL
                SELECT case_id, 'action_updated', updated_at, 5, id
                FROM action
                UNION ALL
                SELECT case_id, 'action_updated', created_at, 6, id
                FROM action_progress
            )
            ORDER BY case_id, occurred_at DESC, kind_rank DESC, activity_id
            """
        ).fetchall()
        latest: dict[str, tuple[str, CaseActivityKind]] = {}
        for row in rows:
            latest.setdefault(
                str(row["case_id"]),
                (str(row["occurred_at"]), CaseActivityKind(str(row["activity_kind"]))),
            )
        return latest

    def transition_case(self, command: CaseTransitionCommand) -> CaseTransitionResult:
        """Validate and atomically commit one Case lifecycle transition."""

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """
                SELECT id, type, template_id, template_version, title, goal, status,
                       priority, version, created_at, updated_at, closed_at
                FROM case_record WHERE id = ?
                """,
                (command.case_id,),
            ).fetchone()
            if row is None:
                raise CaseNotFoundError
            current = _case_record(row)
            _validate_template_binding(current)
            if current.version != command.expected_version:
                raise CaseVersionConflictError(current.version)
            if not is_transition_allowed(current.status, command.target_status):
                raise CaseTransitionNotAllowedError

            transitioned_at = _utc_now()
            new_version = current.version + 1
            closed_at = current.closed_at
            if (
                command.target_status
                in {
                    CaseStatus.RESOLVED,
                    CaseStatus.ABANDONED,
                    CaseStatus.ARCHIVED,
                }
                and closed_at is None
            ):
                closed_at = transitioned_at

            updated = self._connection.execute(
                """
                UPDATE case_record
                SET status = ?, version = ?, updated_at = ?, closed_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    command.target_status.value,
                    new_version,
                    transitioned_at,
                    closed_at,
                    command.case_id,
                    current.version,
                ),
            )
            if updated.rowcount != 1:
                raise CasePersistenceError("Case transition update was not applied")

            transition = CaseTransition(
                id=_new_transition_id(),
                case_id=command.case_id,
                prior_status=current.status,
                new_status=command.target_status,
                prior_version=current.version,
                new_version=new_version,
                actor=command.actor,
                reason=command.reason,
                transitioned_at=transitioned_at,
            )
            self._connection.execute(
                """
                INSERT INTO case_transition (
                    id, case_id, prior_status, new_status,
                    prior_version, new_version, actor, reason, transitioned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transition.id,
                    transition.case_id,
                    transition.prior_status.value,
                    transition.new_status.value,
                    transition.prior_version,
                    transition.new_version,
                    transition.actor,
                    transition.reason,
                    transition.transitioned_at,
                ),
            )
            self._connection.execute("COMMIT")
        except CaseServiceError:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except (sqlite3.Error, ValueError) as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise CasePersistenceError("Case transition was rolled back") from error

        return CaseTransitionResult(
            case=CaseRecord(
                id=current.id,
                type=current.type,
                template_id=current.template_id,
                template_version=current.template_version,
                title=current.title,
                goal=current.goal,
                status=command.target_status,
                priority=current.priority,
                version=new_version,
                created_at=current.created_at,
                updated_at=transitioned_at,
                closed_at=closed_at,
            ),
            transition=transition,
        )

    def list_case_transitions(self, case_id: str) -> tuple[CaseTransition, ...]:
        """Return append-only Case history in deterministic version order."""

        clean_case_id = case_id.strip()
        if not 1 <= len(clean_case_id) <= 200:
            raise CaseValidationError
        try:
            exists = self._connection.execute(
                "SELECT 1 FROM case_record WHERE id = ?", (clean_case_id,)
            ).fetchone()
            if exists is None:
                raise CaseNotFoundError
            rows = self._connection.execute(
                """
                SELECT id, case_id, prior_status, new_status,
                       prior_version, new_version, actor, reason, transitioned_at
                FROM case_transition
                WHERE case_id = ?
                ORDER BY new_version, id
                """,
                (clean_case_id,),
            ).fetchall()
            return tuple(_case_transition(row) for row in rows)
        except CaseServiceError:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise CasePersistenceError("Case transition history could not be read") from error


def _new_case_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"case_{timestamp}_{uuid4().hex}"


def _new_transition_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"transition_{timestamp}_{uuid4().hex}"


def _case_record(row: sqlite3.Row) -> CaseRecord:
    return CaseRecord(
        id=str(row["id"]),
        type=str(row["type"]),
        template_id=str(row["template_id"]),
        template_version=int(row["template_version"]),
        title=str(row["title"]),
        goal=str(row["goal"]),
        status=CaseStatus(str(row["status"])),
        priority=int(row["priority"]),
        version=int(row["version"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        closed_at=str(row["closed_at"]) if row["closed_at"] is not None else None,
    )


def _validate_template_binding(record: CaseRecord) -> None:
    try:
        BUILT_IN_TEMPLATE_REGISTRY.load_exact(
            case_type=record.type,
            template_id=record.template_id,
            template_version=record.template_version,
        )
    except TemplateRegistryError as error:
        raise CaseTemplateBindingError("Case template binding is invalid") from error


def _case_transition(row: sqlite3.Row) -> CaseTransition:
    return CaseTransition(
        id=str(row["id"]),
        case_id=str(row["case_id"]),
        prior_status=CaseStatus(str(row["prior_status"])),
        new_status=CaseStatus(str(row["new_status"])),
        prior_version=int(row["prior_version"]),
        new_version=int(row["new_version"]),
        actor=str(row["actor"]),
        reason=str(row["reason"]),
        transitioned_at=str(row["transitioned_at"]),
    )


def _workbench_item(
    record: CaseRecord,
    activity: tuple[str, CaseActivityKind],
    evaluation: object,
    actions: object,
) -> CaseWorkbenchItem:
    from lifecase.actions import ActionRecord, ActionStatus

    if not isinstance(evaluation, TemplateEvaluation):
        raise ValueError("Case template evaluation is invalid")
    if not isinstance(actions, tuple) or not all(
        isinstance(item, ActionRecord) for item in actions
    ):
        raise ValueError("Case Action projection is invalid")
    next_action = next(
        (
            item
            for item in actions
            if item.status not in {ActionStatus.DONE, ActionStatus.CANCELLED}
            and item.analysis_basis_status not in {"changed", "unavailable"}
        ),
        None,
    )
    return CaseWorkbenchItem(
        case=record,
        is_today=record.status in _TODAY_STATUSES,
        next_action=(
            CaseNextActionSummary(
                id=next_action.id,
                kind=next_action.kind.value,
                status=next_action.status.value,
                risk_level=next_action.risk_level.value,
                blocker_count=len(next_action.blockers),
                version=next_action.version,
            )
            if next_action is not None
            else None
        ),
        deadline_risk=CaseDeadlineRisk.NOT_CALCULATED,
        missing_evidence=CaseMissingEvidence(
            required_claims=evaluation.missing_required_claims,
            evidence_requirements=tuple(
                requirement.key for requirement in evaluation.missing_evidence_requirements
            ),
        ),
        last_activity_at=activity[0],
        last_activity_kind=activity[1],
    )


def _workbench_order(item: CaseWorkbenchItem) -> tuple[object, ...]:
    timestamp = datetime.fromisoformat(item.last_activity_at.replace("Z", "+00:00"))
    return (not item.is_today, -item.case.priority, -timestamp.timestamp(), item.case.id)


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
