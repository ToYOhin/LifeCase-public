"""Transactional, Case-scoped authority for M5-02 Actions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from lifecase.cases.models import CaseRecord
from lifecase.cases.service import CaseNotFoundError, CasePersistenceError, CaseService
from lifecase.claims.models import ClaimEventType, ClaimStatus, ClaimValue
from lifecase.claims.service import ClaimService, ClaimServiceError
from lifecase.deadlines.models import DeadlineRecord
from lifecase.deadlines.service import DeadlineService, DeadlineServiceError
from lifecase.templates.registry import BUILT_IN_TEMPLATE_REGISTRY, TemplateRegistryError

from .models import (
    ActionBlocker,
    ActionBlockerCode,
    ActionCompletionEvidence,
    ActionDeadline,
    ActionDependency,
    ActionEvent,
    ActionEventType,
    ActionKind,
    ActionPayload,
    ActionRecord,
    ActionRequirement,
    ActionRiskLevel,
    ActionStatus,
    ActionStatusCommand,
    CompleteActionCommand,
    CompletionAuthorityType,
    CompletionEvidenceInput,
    CreateActionCommand,
    ReviseActionCommand,
)

_AUTHORITATIVE_CLAIM_STATUSES = frozenset({ClaimStatus.CONFIRMED, ClaimStatus.CORRECTED})


class ActionServiceError(RuntimeError):
    """Base class for safe Action service failures."""


class ActionNotFoundError(ActionServiceError):
    pass


class ActionCaseNotFoundError(ActionServiceError):
    pass


class ActionRequirementInvalidError(ActionServiceError):
    pass


class ActionRequirementStaleError(ActionServiceError):
    pass


class ActionDependencyInvalidError(ActionServiceError):
    pass


class ActionDependencyStaleError(ActionServiceError):
    pass


class ActionDeadlineInvalidError(ActionServiceError):
    pass


class ActionTargetInvalidError(ActionServiceError):
    pass


class ActionIntegrityError(ActionServiceError):
    pass


class ActionVersionConflictError(ActionServiceError):
    def __init__(self, current_version: int) -> None:
        super().__init__("Action version does not match the command")
        self.current_version = current_version


class ActionTransitionNotAllowedError(ActionServiceError):
    pass


class ActionCompletionEvidenceInvalidError(ActionServiceError):
    pass


class ActionPersistenceError(ActionServiceError):
    pass


class ActionService:
    """Own Action creation, authoritative reads, graph order, and history."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @staticmethod
    def _analysis_bound(payload: ActionPayload) -> bool:
        return "analysis_run_id" in payload.to_dict()

    def require_current_analysis(self, action: ActionRecord) -> None:
        """Historical text stays readable; use of its authority must be current."""
        from .analysis_draft import AnalysisDraftStaleError, require_current_analysis_action

        try:
            require_current_analysis_action(self._connection, action.case_id, action.payload)
        except AnalysisDraftStaleError as error:
            raise ActionIntegrityError("Action analysis basis has changed") from error

    def _analysis_status(
        self, case_id: str, payload: ActionPayload
    ) -> Literal["current", "changed", "unavailable"] | None:
        from .analysis_draft import analysis_action_status

        return analysis_action_status(self._connection, case_id, payload)

    def create_action(self, command: CreateActionCommand) -> ActionRecord:
        """Create one Action and all owned authority rows atomically."""

        if self._analysis_bound(command.payload):
            raise ActionTransitionNotAllowedError("Use the explicit analysis draft save")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            action_id = self._create_action_in_transaction(command)
            self._connection.execute("COMMIT")
        except ActionServiceError:
            self._rollback()
            raise
        except (CaseNotFoundError, CasePersistenceError) as error:
            self._rollback()
            raise ActionCaseNotFoundError from error
        except TemplateRegistryError as error:
            self._rollback()
            raise ActionIntegrityError("Action template authority is invalid") from error
        except (DeadlineServiceError, sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ActionPersistenceError("Action creation was rolled back") from error
        return self.get_action(action_id)

    def _create_action_in_transaction(self, command: CreateActionCommand) -> str:
        """Share the existing atomic insert with the explicit analysis save."""
        if not self._connection.in_transaction:
            raise ActionPersistenceError("Action creation requires a transaction")
        case = self._require_case(command.case_id)
        template = BUILT_IN_TEMPLATE_REGISTRY.load_exact(
            case_type=case.type,
            template_id=case.template_id,
            template_version=case.template_version,
        )
        requirements = self._validate_requirements(command, template.required_claims)
        dependencies = self._validate_dependencies(command)
        deadline = self._validate_deadline(command)
        blockers = self._derive_create_blockers(
            command,
            requirements=requirements,
            dependencies=dependencies,
            required_fields=template.required_claims,
            evidence_keys=tuple(item.key for item in template.evidence_requirements),
        )
        blockers_json, blockers_sha256 = _canonical_blockers(blockers)
        status = ActionStatus.BLOCKED if blockers else ActionStatus.READY
        action_id = _new_id("action")
        occurred_at = _utc_now()
        self._connection.execute(
            """
            INSERT INTO action (
                id, case_id, kind, status, risk_level, payload_json,
                payload_sha256, blockers_json, blockers_sha256, version,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                action_id,
                command.case_id,
                command.kind.value,
                status.value,
                command.risk_level.value,
                command.payload.json_text,
                command.payload.sha256,
                blockers_json,
                blockers_sha256,
                occurred_at,
                occurred_at,
            ),
        )
        self._insert_requirements(action_id, command.case_id, requirements)
        self._insert_dependencies(action_id, command.case_id, command, dependencies)
        if deadline is not None and command.deadline is not None:
            self._connection.execute(
                """
                INSERT INTO action_deadline (
                    action_id, case_id, deadline_id, deadline_version,
                    definition_sha256
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    command.case_id,
                    deadline.deadline_id,
                    command.deadline.expected_deadline_version,
                    command.deadline.definition_sha256,
                ),
            )
        self._insert_event(
            action_id=action_id,
            case_id=command.case_id,
            event_type=ActionEventType.CREATED,
            prior_status=None,
            new_status=status,
            prior_version=0,
            new_version=1,
            payload_sha256=command.payload.sha256,
            blockers_sha256=blockers_sha256,
            actor=command.actor,
            reason=command.reason,
            occurred_at=occurred_at,
        )
        return action_id

    def get_action(self, action_id: str) -> ActionRecord:
        """Read and revalidate one Action in a stable transaction."""

        clean_action_id = _bounded_id(action_id)
        try:
            self._require_idle("Action read requires an idle connection")
            self._connection.execute("BEGIN")
            row = self._connection.execute(
                """
                SELECT id, case_id, kind, status, risk_level, payload_json,
                       payload_sha256, blockers_json, blockers_sha256, version,
                       created_at, updated_at
                FROM action WHERE id = ?
                """,
                (clean_action_id,),
            ).fetchone()
            if row is None:
                raise ActionNotFoundError
            result = self._read_one_action(row)
            self._connection.execute("COMMIT")
            return result
        except ActionServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ActionPersistenceError("Action could not be read") from error

    def list_case_actions(self, case_id: str) -> tuple[ActionRecord, ...]:
        """Return one Case graph in stable topological order."""

        clean_case_id = _bounded_id(case_id)
        try:
            self._require_idle("Action list requires an idle connection")
            self._connection.execute("BEGIN")
            records = self.list_case_actions_in_transaction(clean_case_id)
            self._connection.execute("COMMIT")
            return records
        except ActionServiceError:
            self._rollback()
            raise
        except (CaseNotFoundError, CasePersistenceError) as error:
            self._rollback()
            raise ActionCaseNotFoundError from error
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ActionPersistenceError("Case Actions could not be read") from error

    def list_case_actions_in_transaction(self, case_id: str) -> tuple[ActionRecord, ...]:
        """Read one validated Case graph inside a caller-owned transaction."""

        if not self._connection.in_transaction:
            raise ActionPersistenceError("Action transaction read requires a transaction")
        clean_case_id = _bounded_id(case_id)
        self._require_case(clean_case_id)
        return self._read_case_actions(clean_case_id)

    def list_action_events_in_transaction(self, action_id: str) -> tuple[ActionEvent, ...]:
        """Read immutable events inside a caller-owned stable transaction."""

        if not self._connection.in_transaction:
            raise ActionPersistenceError("Action history requires a transaction")
        return self._read_events(_bounded_id(action_id))

    def get_topological_position(self, action_id: str) -> int:
        """Return one stable graph position without projecting unrelated aggregates."""

        clean_action_id = _bounded_id(action_id)
        try:
            self._require_idle("Action graph read requires an idle connection")
            self._connection.execute("BEGIN")
            owner = self._connection.execute(
                "SELECT case_id FROM action WHERE id = ?", (clean_action_id,)
            ).fetchone()
            if owner is None:
                raise ActionNotFoundError
            rows = self._connection.execute(
                "SELECT id, created_at FROM action WHERE case_id = ? ORDER BY created_at, id",
                (str(owner["case_id"]),),
            ).fetchall()
            graph: dict[str, tuple[tuple[str, int], ...]] = {}
            for row in rows:
                current_id = str(row["id"])
                edges = self._connection.execute(
                    """
                    SELECT depends_on_action_id, depends_on_action_version
                    FROM action_dependency WHERE action_id = ? ORDER BY position
                    """,
                    (current_id,),
                ).fetchall()
                graph[current_id] = tuple((str(item[0]), int(item[1])) for item in edges)
            if any(edge[0] not in graph for edges in graph.values() for edge in edges):
                raise ActionIntegrityError("Action dependency scope is invalid")
            ordered = _topological_order(rows, graph)
            position = ordered.index(clean_action_id)
            self._connection.execute("COMMIT")
            return position
        except ActionServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ActionPersistenceError("Action graph could not be read") from error

    def get_action_case_id(self, action_id: str) -> str:
        """Resolve Action ownership without exposing storage logic to transport."""

        clean_action_id = _bounded_id(action_id)
        try:
            row = self._connection.execute(
                "SELECT case_id FROM action WHERE id = ?", (clean_action_id,)
            ).fetchone()
            if row is None:
                raise ActionNotFoundError
            return str(row["case_id"])
        except ActionServiceError:
            raise
        except sqlite3.Error as error:
            raise ActionPersistenceError("Action ownership could not be read") from error

    def list_action_events(self, action_id: str) -> tuple[ActionEvent, ...]:
        """Return contiguous immutable history after validating the aggregate."""

        clean_action_id = _bounded_id(action_id)
        try:
            self._require_idle("Action history read requires an idle connection")
            self._connection.execute("BEGIN")
            row = self._connection.execute(
                """
                SELECT id, case_id, kind, status, risk_level, payload_json,
                       payload_sha256, blockers_json, blockers_sha256, version,
                       created_at, updated_at
                FROM action WHERE id = ?
                """,
                (clean_action_id,),
            ).fetchone()
            if row is None:
                raise ActionNotFoundError
            record = self._read_one_action(row)
            events = self._read_events(record.id)
            self._connection.execute("COMMIT")
            return events
        except ActionServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ActionPersistenceError("Action history could not be read") from error

    def revise(self, command: ReviseActionCommand) -> ActionRecord:
        """Replace one mutable Action definition and append one exact version."""

        definition = command.definition
        try:
            current = self._begin_mutation_raw(command.action_id, command.expected_version)
            if self._analysis_bound(current.payload) or self._analysis_bound(definition.payload):
                raise ActionTransitionNotAllowedError("Use the explicit analysis draft save")
            self._revise_action_in_transaction(command, current)
            self._connection.execute("COMMIT")
        except ActionServiceError:
            self._rollback()
            raise
        except (
            TemplateRegistryError,
            DeadlineServiceError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as error:
            self._rollback()
            raise ActionPersistenceError("Action revision was rolled back") from error
        return self.get_action(command.action_id)

    def _revise_action_in_transaction(
        self, command: ReviseActionCommand, current: ActionRecord
    ) -> None:
        """Reuse the ordinary versioned revision after a scoped draft save check."""
        if not self._connection.in_transaction:
            raise ActionPersistenceError("Action revision requires a transaction")
        definition = command.definition
        if current.status not in {
            ActionStatus.PROPOSED,
            ActionStatus.BLOCKED,
            ActionStatus.READY,
            ActionStatus.APPROVED,
        }:
            raise ActionTransitionNotAllowedError
        if definition.case_id != current.case_id:
            raise ActionRequirementInvalidError("Action revision crossed Case scope")
        case = self._require_case(current.case_id)
        template = BUILT_IN_TEMPLATE_REGISTRY.load_exact(
            case_type=case.type,
            template_id=case.template_id,
            template_version=case.template_version,
        )
        requirements = self._validate_requirements(definition, template.required_claims)
        dependencies = self._validate_dependencies(definition)
        if any(item.action_id == current.id for item in dependencies):
            raise ActionDependencyInvalidError("Action dependency graph is invalid")
        self._validate_revised_graph(current.id, current.case_id, dependencies)
        deadline = self._validate_deadline(definition)
        blockers = self._derive_create_blockers(
            definition,
            requirements=requirements,
            dependencies=dependencies,
            required_fields=template.required_claims,
            evidence_keys=tuple(item.key for item in template.evidence_requirements),
        )
        blockers_json, blockers_sha256 = _canonical_blockers(blockers)
        new_status = ActionStatus.BLOCKED if blockers else ActionStatus.READY
        new_version = current.version + 1
        occurred_at = _utc_now()
        self._invalidate_active_approvals(
            current.id,
            current.case_id,
            definition.actor,
            definition.reason,
            occurred_at,
        )
        self._connection.execute(
            "DELETE FROM action_requirement_claim WHERE action_id = ?", (current.id,)
        )
        self._connection.execute("DELETE FROM action_dependency WHERE action_id = ?", (current.id,))
        self._connection.execute("DELETE FROM action_deadline WHERE action_id = ?", (current.id,))
        self._insert_requirements(current.id, current.case_id, requirements)
        self._insert_dependencies(current.id, current.case_id, definition, dependencies)
        if deadline is not None and definition.deadline is not None:
            self._connection.execute(
                """
                INSERT INTO action_deadline (
                    action_id, case_id, deadline_id, deadline_version, definition_sha256
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    current.id,
                    current.case_id,
                    deadline.deadline_id,
                    definition.deadline.expected_deadline_version,
                    definition.deadline.definition_sha256,
                ),
            )
        self._update_action(
            current=current,
            kind=definition.kind,
            status=new_status,
            risk_level=definition.risk_level,
            payload=definition.payload,
            blockers_json=blockers_json,
            blockers_sha256=blockers_sha256,
            new_version=new_version,
            updated_at=occurred_at,
        )
        self._insert_event(
            action_id=current.id,
            case_id=current.case_id,
            event_type=ActionEventType.REVISED,
            prior_status=current.status,
            new_status=new_status,
            prior_version=current.version,
            new_version=new_version,
            payload_sha256=definition.payload.sha256,
            blockers_sha256=blockers_sha256,
            actor=definition.actor,
            reason=definition.reason,
            occurred_at=occurred_at,
        )

    def revalidate(self, command: ActionStatusCommand) -> ActionRecord:
        """Refresh dependency versions and blockers against current authority."""

        try:
            current = self._begin_mutation_raw(command.action_id, command.expected_version)
            if current.status not in {
                ActionStatus.PROPOSED,
                ActionStatus.BLOCKED,
                ActionStatus.READY,
            }:
                raise ActionTransitionNotAllowedError
            case = self._require_case(current.case_id)
            template = BUILT_IN_TEMPLATE_REGISTRY.load_exact(
                case_type=case.type,
                template_id=case.template_id,
                template_version=case.template_version,
            )
            requirements = self._read_requirements(current.id, current.case_id)
            dependencies = self._read_current_dependencies(current.id, current.case_id)
            deadline_records = DeadlineService(self._connection).list_case_deadlines_in_transaction(
                current.case_id
            )
            self._read_deadline(current.id, {item.id: item for item in deadline_records})
            blockers = self._derive_revalidation_blockers(
                current,
                requirements=requirements,
                dependencies=dependencies,
                required_fields=template.required_claims,
            )
            blockers_json, blockers_sha256 = _canonical_blockers(blockers)
            new_status = ActionStatus.BLOCKED if blockers else ActionStatus.READY
            new_version = current.version + 1
            occurred_at = _utc_now()
            self._invalidate_active_approvals(
                current.id,
                current.case_id,
                command.actor,
                command.reason,
                occurred_at,
            )
            self._connection.executemany(
                """
                UPDATE action_dependency SET depends_on_action_version = ?
                WHERE action_id = ? AND depends_on_action_id = ?
                """,
                ((item.action_version, current.id, item.action_id) for item in dependencies),
            )
            self._update_action(
                current=current,
                kind=current.kind,
                status=new_status,
                risk_level=current.risk_level,
                payload=current.payload,
                blockers_json=blockers_json,
                blockers_sha256=blockers_sha256,
                new_version=new_version,
                updated_at=occurred_at,
            )
            self._insert_event(
                action_id=current.id,
                case_id=current.case_id,
                event_type=ActionEventType.REVALIDATED,
                prior_status=current.status,
                new_status=new_status,
                prior_version=current.version,
                new_version=new_version,
                payload_sha256=current.payload.sha256,
                blockers_sha256=blockers_sha256,
                actor=command.actor,
                reason=command.reason,
                occurred_at=occurred_at,
            )
            self._connection.execute("COMMIT")
        except ActionServiceError:
            self._rollback()
            raise
        except (
            TemplateRegistryError,
            DeadlineServiceError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as error:
            self._rollback()
            raise ActionPersistenceError("Action revalidation was rolled back") from error
        return self.get_action(command.action_id)

    def start(self, command: ActionStatusCommand) -> ActionRecord:
        """Start a ready Risk-A or explicitly approved Risk-B local Action."""

        current = self._mutating_record(command)
        allowed = (
            current.status is ActionStatus.READY and current.risk_level is ActionRiskLevel.A
        ) or (current.status is ActionStatus.APPROVED and current.risk_level is ActionRiskLevel.B)
        if not allowed:
            self._rollback()
            raise ActionTransitionNotAllowedError
        return self._commit_transition(
            current, command, ActionStatus.RUNNING, ActionEventType.STARTED
        )

    def fail(self, command: ActionStatusCommand) -> ActionRecord:
        current = self._mutating_record(command)
        if current.status is not ActionStatus.RUNNING:
            self._rollback()
            raise ActionTransitionNotAllowedError
        return self._commit_transition(
            current, command, ActionStatus.FAILED, ActionEventType.FAILED
        )

    def cancel(self, command: ActionStatusCommand) -> ActionRecord:
        current = self._mutating_record(command, allow_stale_analysis=True)
        if current.status not in {
            ActionStatus.PROPOSED,
            ActionStatus.BLOCKED,
            ActionStatus.READY,
            ActionStatus.RUNNING,
            ActionStatus.FAILED,
            ActionStatus.APPROVED,
        }:
            self._rollback()
            raise ActionTransitionNotAllowedError
        self._invalidate_active_approvals(
            current.id,
            current.case_id,
            command.actor,
            command.reason,
            _utc_now(),
        )
        return self._commit_transition(
            current, command, ActionStatus.CANCELLED, ActionEventType.CANCELLED
        )

    def complete(self, command: CompleteActionCommand) -> ActionRecord:
        """Complete one running Action with exact same-Case evidence authority."""

        status_command = ActionStatusCommand(
            command.action_id,
            command.expected_version,
            command.actor,
            command.reason,
        )
        current = self._mutating_record(status_command)
        if current.status is not ActionStatus.RUNNING:
            self._rollback()
            raise ActionTransitionNotAllowedError
        try:
            evidence = tuple(
                self._validate_completion_evidence(current.case_id, item)
                for item in command.evidence
            )
            if self._analysis_bound(current.payload):
                for item in command.evidence:
                    if item.authority_type is CompletionAuthorityType.CLAIM:
                        # The completed record must remain readable under the same
                        # immutable analysis snapshot checked before this commit.
                        self._historical_completion_claim(current.case_id, item, current.payload)
            self._connection.executemany(
                """
                INSERT INTO action_completion_evidence (
                    action_id, case_id, position, authority_type, authority_id,
                    authority_version, authority_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        current.id,
                        current.case_id,
                        position,
                        item.authority_type.value,
                        item.authority_id,
                        item.authority_version,
                        item.authority_sha256,
                    )
                    for position, item in enumerate(evidence)
                ),
            )
            return self._commit_transition(
                current,
                status_command,
                ActionStatus.DONE,
                ActionEventType.COMPLETED,
            )
        except ActionServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ActionPersistenceError("Action completion was rolled back") from error

    def _require_case(self, case_id: str) -> CaseRecord:
        try:
            return CaseService(self._connection).get_case(case_id)
        except CaseNotFoundError as error:
            raise ActionCaseNotFoundError from error
        except CasePersistenceError as error:
            raise ActionPersistenceError("Action Case authority could not be read") from error

    def _begin_mutation(
        self, action_id: str, expected_version: int, *, allow_stale_analysis: bool = False
    ) -> ActionRecord:
        self._require_idle("Action mutation requires an idle connection")
        self._connection.execute("BEGIN IMMEDIATE")
        row = self._connection.execute(
            """
            SELECT id, case_id, kind, status, risk_level, payload_json,
                   payload_sha256, blockers_json, blockers_sha256, version,
                   created_at, updated_at
            FROM action WHERE id = ?
            """,
            (action_id,),
        ).fetchone()
        if row is None:
            raise ActionNotFoundError
        current = self._read_one_action(row)
        if current.version != expected_version:
            raise ActionVersionConflictError(current.version)
        if not allow_stale_analysis:
            self.require_current_analysis(current)
        return current

    def _begin_mutation_raw(self, action_id: str, expected_version: int) -> ActionRecord:
        self._require_idle("Action mutation requires an idle connection")
        self._connection.execute("BEGIN IMMEDIATE")
        row = self._connection.execute(
            """
            SELECT id, case_id, kind, status, risk_level, payload_json,
                   payload_sha256, blockers_json, blockers_sha256, version,
                   created_at, updated_at
            FROM action WHERE id = ?
            """,
            (action_id,),
        ).fetchone()
        if row is None:
            raise ActionNotFoundError
        current = self._base_record(row)
        self._validate_history(
            action_id=current.id,
            case_id=current.case_id,
            status=current.status,
            version=current.version,
            payload_sha256=current.payload.sha256,
            blockers_sha256=current.blockers_sha256,
        )
        if current.version != expected_version:
            raise ActionVersionConflictError(current.version)
        self.require_current_analysis(current)
        return current

    def _base_record(self, row: sqlite3.Row) -> ActionRecord:
        payload = ActionPayload.from_storage(
            kind=ActionKind(str(row["kind"])),
            json_text=str(row["payload_json"]),
            sha256=str(row["payload_sha256"]),
        )
        blockers = _parse_blockers(str(row["blockers_json"]), str(row["blockers_sha256"]))
        return ActionRecord(
            id=str(row["id"]),
            case_id=str(row["case_id"]),
            kind=payload.kind,
            status=ActionStatus(str(row["status"])),
            risk_level=ActionRiskLevel(str(row["risk_level"])),
            payload=payload,
            blockers=blockers,
            blockers_sha256=str(row["blockers_sha256"]),
            requirements=(),
            dependencies=(),
            deadline=None,
            completion_evidence=(),
            communication_draft=None,
            version=int(row["version"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _mutating_record(
        self, command: ActionStatusCommand, *, allow_stale_analysis: bool = False
    ) -> ActionRecord:
        try:
            return self._begin_mutation(
                command.action_id,
                command.expected_version,
                allow_stale_analysis=allow_stale_analysis,
            )
        except ActionServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ActionPersistenceError("Action mutation could not start") from error

    def _commit_transition(
        self,
        current: ActionRecord,
        command: ActionStatusCommand,
        new_status: ActionStatus,
        event_type: ActionEventType,
    ) -> ActionRecord:
        try:
            occurred_at = _utc_now()
            new_version = current.version + 1
            self._update_action(
                current=current,
                kind=current.kind,
                status=new_status,
                risk_level=current.risk_level,
                payload=current.payload,
                blockers_json=_canonical_blockers(current.blockers)[0],
                blockers_sha256=current.blockers_sha256,
                new_version=new_version,
                updated_at=occurred_at,
            )
            self._insert_event(
                action_id=current.id,
                case_id=current.case_id,
                event_type=event_type,
                prior_status=current.status,
                new_status=new_status,
                prior_version=current.version,
                new_version=new_version,
                payload_sha256=current.payload.sha256,
                blockers_sha256=current.blockers_sha256,
                actor=command.actor,
                reason=command.reason,
                occurred_at=occurred_at,
            )
            self._connection.execute("COMMIT")
        except ActionServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            self._rollback()
            raise ActionPersistenceError("Action transition was rolled back") from error
        return self.get_action(current.id)

    def approve_with_authority_in_transaction(
        self,
        current: ActionRecord,
        *,
        actor: str,
        reason: str,
        occurred_at: str,
    ) -> None:
        """Commit the Action half of an ApprovalService-owned transaction."""

        if not self._connection.in_transaction:
            raise ActionPersistenceError("Action approval requires a transaction")
        self.require_current_analysis(current)
        if current.status is not ActionStatus.READY or current.risk_level not in {
            ActionRiskLevel.B,
            ActionRiskLevel.C,
        }:
            raise ActionTransitionNotAllowedError
        new_version = current.version + 1
        self._update_action(
            current=current,
            kind=current.kind,
            status=ActionStatus.APPROVED,
            risk_level=current.risk_level,
            payload=current.payload,
            blockers_json=_canonical_blockers(current.blockers)[0],
            blockers_sha256=current.blockers_sha256,
            new_version=new_version,
            updated_at=occurred_at,
        )
        self._insert_event(
            action_id=current.id,
            case_id=current.case_id,
            event_type=ActionEventType.APPROVED,
            prior_status=current.status,
            new_status=ActionStatus.APPROVED,
            prior_version=current.version,
            new_version=new_version,
            payload_sha256=current.payload.sha256,
            blockers_sha256=current.blockers_sha256,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )

    def _update_action(
        self,
        *,
        current: ActionRecord,
        kind: ActionKind,
        status: ActionStatus,
        risk_level: ActionRiskLevel,
        payload: ActionPayload,
        blockers_json: str,
        blockers_sha256: str,
        new_version: int,
        updated_at: str,
    ) -> None:
        cursor = self._connection.execute(
            """
            UPDATE action
            SET kind = ?, status = ?, risk_level = ?, payload_json = ?,
                payload_sha256 = ?, blockers_json = ?, blockers_sha256 = ?,
                version = ?, updated_at = ?
            WHERE id = ? AND case_id = ? AND version = ?
            """,
            (
                kind.value,
                status.value,
                risk_level.value,
                payload.json_text,
                payload.sha256,
                blockers_json,
                blockers_sha256,
                new_version,
                updated_at,
                current.id,
                current.case_id,
                current.version,
            ),
        )
        if cursor.rowcount != 1:
            raise ActionVersionConflictError(current.version)

    def _read_current_dependencies(
        self, action_id: str, case_id: str
    ) -> tuple[ActionDependency, ...]:
        rows = self._connection.execute(
            """
            SELECT d.depends_on_action_id, a.case_id, a.status, a.version
            FROM action_dependency AS d
            JOIN action AS a ON a.id = d.depends_on_action_id
            WHERE d.action_id = ? ORDER BY d.position
            """,
            (action_id,),
        ).fetchall()
        dependencies: list[ActionDependency] = []
        for row in rows:
            if str(row["case_id"]) != case_id:
                raise ActionIntegrityError("Action dependency scope is invalid")
            dependencies.append(
                ActionDependency(
                    action_id=str(row["depends_on_action_id"]),
                    action_version=int(row["version"]),
                    status=ActionStatus(str(row["status"])),
                )
            )
        return tuple(dependencies)

    def _derive_revalidation_blockers(
        self,
        current: ActionRecord,
        *,
        requirements: tuple[ActionRequirement, ...],
        dependencies: tuple[ActionDependency, ...],
        required_fields: tuple[str, ...],
    ) -> tuple[ActionBlocker, ...]:
        if current.kind is ActionKind.COLLECT_EVIDENCE:
            payload = current.payload.to_dict()
            if self._has_authoritative_claim(current.case_id, payload["requirement_key"]):
                return (ActionBlocker(ActionBlockerCode.COLLECTION_TARGET_SATISFIED),)
            return ()
        bound = frozenset(item.field for item in requirements)
        result = [
            ActionBlocker(ActionBlockerCode.MISSING_REQUIRED_CLAIM, field=field)
            for field in required_fields
            if field not in bound
        ]
        result.extend(
            ActionBlocker(
                ActionBlockerCode.DEPENDENCY_INCOMPLETE,
                authority_id=item.action_id,
            )
            for item in dependencies
            if item.status is not ActionStatus.DONE
        )
        return tuple(result)

    def _validate_revised_graph(
        self,
        action_id: str,
        case_id: str,
        dependencies: tuple[ActionDependency, ...],
    ) -> None:
        rows = self._connection.execute(
            "SELECT id, created_at FROM action WHERE case_id = ? ORDER BY created_at, id",
            (case_id,),
        ).fetchall()
        graph: dict[str, tuple[tuple[str, int], ...]] = {}
        for row in rows:
            current_id = str(row["id"])
            if current_id == action_id:
                graph[current_id] = tuple(
                    (item.action_id, item.action_version) for item in dependencies
                )
                continue
            edges = self._connection.execute(
                """
                SELECT depends_on_action_id, depends_on_action_version
                FROM action_dependency WHERE action_id = ? ORDER BY position
                """,
                (current_id,),
            ).fetchall()
            graph[current_id] = tuple((str(item[0]), int(item[1])) for item in edges)
        try:
            _topological_order(rows, graph)
        except ActionIntegrityError as error:
            raise ActionDependencyInvalidError("Action dependency graph is invalid") from error

    def _validate_completion_evidence(
        self, case_id: str, item: CompletionEvidenceInput
    ) -> ActionCompletionEvidence:
        if item.authority_type is CompletionAuthorityType.CLAIM:
            row = self._connection.execute(
                """
                SELECT case_id, value_json, value_sha256, comparison_sha256,
                       status, contradiction, version
                FROM claim WHERE id = ?
                """,
                (item.authority_id,),
            ).fetchone()
            if row is None or str(row["case_id"]) != case_id:
                raise ActionCompletionEvidenceInvalidError("Completion evidence is unavailable")
            value = ClaimValue.from_storage(
                str(row["value_json"]),
                str(row["value_sha256"]),
                str(row["comparison_sha256"]),
            )
            if (
                int(row["version"]) != item.expected_version
                or value.sha256 != item.authority_sha256
                or ClaimStatus(str(row["status"])) not in _AUTHORITATIVE_CLAIM_STATUSES
                or bool(row["contradiction"])
            ):
                raise ActionCompletionEvidenceInvalidError("Completion evidence is stale")
            return ActionCompletionEvidence(
                item.authority_type,
                item.authority_id,
                item.expected_version,
                item.authority_sha256,
            )
        table = "source" if item.authority_type is CompletionAuthorityType.SOURCE else "artifact"
        row = self._connection.execute(
            f"SELECT case_id, sha256 FROM {table} WHERE id = ?", (item.authority_id,)
        ).fetchone()
        if (
            row is None
            or str(row["case_id"]) != case_id
            or str(row["sha256"]) != item.authority_sha256
        ):
            raise ActionCompletionEvidenceInvalidError("Completion evidence is unavailable")
        return ActionCompletionEvidence(
            item.authority_type,
            item.authority_id,
            None,
            item.authority_sha256,
        )

    def _invalidate_active_approvals(
        self,
        action_id: str,
        case_id: str,
        actor: str,
        reason: str,
        occurred_at: str,
    ) -> None:
        from lifecase.approvals.service import ApprovalService, ApprovalServiceError

        try:
            ApprovalService(self._connection).invalidate_active_for_action_in_transaction(
                action_id=action_id,
                case_id=case_id,
                actor=actor,
                reason=reason,
                occurred_at=occurred_at,
            )
        except ApprovalServiceError as error:
            raise ActionIntegrityError("Action Approval invalidation failed") from error

    def _validate_requirements(
        self,
        command: CreateActionCommand,
        required_fields: tuple[str, ...],
    ) -> tuple[ActionRequirement, ...]:
        if command.kind is ActionKind.COLLECT_EVIDENCE:
            if command.requirements:
                raise ActionRequirementInvalidError(
                    "Collection Action cannot bind satisfied Claims"
                )
            return ()
        allowed = frozenset(required_fields)
        if any(item.field not in allowed for item in command.requirements):
            raise ActionRequirementInvalidError("Action Claim requirement is not template-required")
        result: list[ActionRequirement] = []
        for item in command.requirements:
            row = self._connection.execute(
                """
                SELECT case_id, field, value_json, value_sha256,
                       comparison_sha256, status, contradiction, version
                FROM claim WHERE id = ?
                """,
                (item.claim_id,),
            ).fetchone()
            if row is None or str(row["case_id"]) != command.case_id:
                raise ActionRequirementInvalidError("Action Claim requirement is unavailable")
            if int(row["version"]) != item.expected_claim_version:
                raise ActionRequirementStaleError("Action Claim requirement is stale")
            value = ClaimValue.from_storage(
                str(row["value_json"]),
                str(row["value_sha256"]),
                str(row["comparison_sha256"]),
            )
            if value.sha256 != item.value_sha256 or str(row["field"]) != item.field:
                raise ActionRequirementStaleError("Action Claim requirement is stale")
            if ClaimStatus(str(row["status"])) not in _AUTHORITATIVE_CLAIM_STATUSES or bool(
                row["contradiction"]
            ):
                raise ActionRequirementInvalidError("Action Claim requirement is not authoritative")
            result.append(
                ActionRequirement(
                    claim_id=item.claim_id,
                    claim_version=item.expected_claim_version,
                    value_sha256=item.value_sha256,
                    field=item.field,
                )
            )
        return tuple(result)

    def _validate_dependencies(self, command: CreateActionCommand) -> tuple[ActionDependency, ...]:
        result: list[ActionDependency] = []
        for item in command.dependencies:
            row = self._connection.execute(
                "SELECT case_id, status, version FROM action WHERE id = ?", (item.action_id,)
            ).fetchone()
            if row is None or str(row["case_id"]) != command.case_id:
                raise ActionDependencyInvalidError("Action dependency is unavailable")
            if int(row["version"]) != item.expected_action_version:
                raise ActionDependencyStaleError("Action dependency is stale")
            result.append(
                ActionDependency(
                    action_id=item.action_id,
                    action_version=item.expected_action_version,
                    status=ActionStatus(str(row["status"])),
                )
            )
        return tuple(result)

    def _validate_deadline(self, command: CreateActionCommand) -> ActionDeadline | None:
        if command.deadline is None:
            return None
        records = DeadlineService(self._connection).list_case_deadlines_in_transaction(
            command.case_id
        )
        record = next(
            (item for item in records if item.id == command.deadline.deadline_id),
            None,
        )
        if record is None:
            raise ActionDeadlineInvalidError("Action Deadline is unavailable")
        if (
            record.version != command.deadline.expected_deadline_version
            or record.definition_sha256 != command.deadline.definition_sha256
        ):
            raise ActionDeadlineInvalidError("Action Deadline is stale")
        return ActionDeadline(
            deadline_id=record.id,
            deadline_version=record.version,
            definition_sha256=record.definition_sha256,
            date_kind=record.due.kind.value,
            due_value=record.due.value,
            timezone=record.due.timezone,
            status=record.status.value,
        )

    def _derive_create_blockers(
        self,
        command: CreateActionCommand,
        *,
        requirements: tuple[ActionRequirement, ...],
        dependencies: tuple[ActionDependency, ...],
        required_fields: tuple[str, ...],
        evidence_keys: tuple[str, ...],
    ) -> tuple[ActionBlocker, ...]:
        if command.kind is ActionKind.COLLECT_EVIDENCE:
            payload = command.payload.to_dict()
            target_type = payload["requirement_type"]
            target = payload["requirement_key"]
            allowed = required_fields if target_type == "claim" else evidence_keys
            if target not in allowed or self._has_authoritative_claim(command.case_id, target):
                raise ActionTargetInvalidError("Collection Action target is not currently missing")
            return ()
        bound = frozenset(item.field for item in requirements)
        blockers = [
            ActionBlocker(ActionBlockerCode.MISSING_REQUIRED_CLAIM, field=field)
            for field in required_fields
            if field not in bound
        ]
        blockers.extend(
            ActionBlocker(
                ActionBlockerCode.DEPENDENCY_INCOMPLETE,
                authority_id=item.action_id,
            )
            for item in dependencies
            if item.status is not ActionStatus.DONE
        )
        return tuple(blockers)

    def _has_authoritative_claim(self, case_id: str, field: str) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM claim
            WHERE case_id = ? AND field = ?
              AND status IN ('confirmed', 'corrected') AND contradiction = 0
            LIMIT 1
            """,
            (case_id, field),
        ).fetchone()
        return row is not None

    def _insert_requirements(
        self,
        action_id: str,
        case_id: str,
        requirements: tuple[ActionRequirement, ...],
    ) -> None:
        self._connection.executemany(
            """
            INSERT INTO action_requirement_claim (
                action_id, case_id, position, claim_id, claim_version,
                value_sha256, field
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    action_id,
                    case_id,
                    position,
                    item.claim_id,
                    item.claim_version,
                    item.value_sha256,
                    item.field,
                )
                for position, item in enumerate(requirements)
            ),
        )

    def _insert_dependencies(
        self,
        action_id: str,
        case_id: str,
        command: CreateActionCommand,
        dependencies: tuple[ActionDependency, ...],
    ) -> None:
        expected_by_id = {
            item.action_id: item.expected_action_version for item in command.dependencies
        }
        self._connection.executemany(
            """
            INSERT INTO action_dependency (
                action_id, case_id, position, depends_on_action_id,
                depends_on_action_version
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                (
                    action_id,
                    case_id,
                    position,
                    item.action_id,
                    expected_by_id[item.action_id],
                )
                for position, item in enumerate(dependencies)
            ),
        )

    def _read_case_actions(self, case_id: str) -> tuple[ActionRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT id, case_id, kind, status, risk_level, payload_json,
                   payload_sha256, blockers_json, blockers_sha256, version,
                   created_at, updated_at
            FROM action WHERE case_id = ? ORDER BY created_at, id
            """,
            (case_id,),
        ).fetchall()
        deadline_records = DeadlineService(self._connection).list_case_deadlines_in_transaction(
            case_id
        )
        deadlines = {item.id: item for item in deadline_records}
        row_by_id = {str(row["id"]): row for row in rows}
        dependencies_by_id = self._read_dependency_rows(case_id, row_by_id)
        ordered_ids = _topological_order(rows, dependencies_by_id)
        records: dict[str, ActionRecord] = {}
        for action_id in ordered_ids:
            row = row_by_id[action_id]
            payload = ActionPayload.from_storage(
                kind=ActionKind(str(row["kind"])),
                json_text=str(row["payload_json"]),
                sha256=str(row["payload_sha256"]),
            )
            blockers = _parse_blockers(str(row["blockers_json"]), str(row["blockers_sha256"]))
            requirements = self._read_requirements(action_id, case_id, payload=payload)
            dependencies = tuple(
                ActionDependency(
                    action_id=dependency_id,
                    action_version=expected_version,
                    status=ActionStatus(str(row_by_id[dependency_id]["status"])),
                )
                for dependency_id, expected_version in dependencies_by_id[action_id]
            )
            deadline = self._read_deadline(action_id, deadlines)
            completion_evidence = self._read_completion_evidence(
                action_id,
                case_id,
                historical_payload=payload
                if str(row["status"]) == ActionStatus.DONE.value
                else None,
            )
            version = int(row["version"])
            status = ActionStatus(str(row["status"]))
            self._validate_history(
                action_id=action_id,
                case_id=case_id,
                status=status,
                version=version,
                payload_sha256=payload.sha256,
                blockers_sha256=str(row["blockers_sha256"]),
            )
            records[action_id] = ActionRecord(
                id=action_id,
                case_id=case_id,
                kind=payload.kind,
                status=status,
                risk_level=ActionRiskLevel(str(row["risk_level"])),
                payload=payload,
                blockers=blockers,
                blockers_sha256=str(row["blockers_sha256"]),
                requirements=requirements,
                dependencies=dependencies,
                deadline=deadline,
                completion_evidence=completion_evidence,
                communication_draft=self._communication_draft(payload, requirements),
                version=version,
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                analysis_basis_status=self._analysis_status(case_id, payload),
            )
        return tuple(records[action_id] for action_id in ordered_ids)

    def _read_one_action(self, row: sqlite3.Row) -> ActionRecord:
        action_id = str(row["id"])
        case_id = str(row["case_id"])
        all_rows = self._connection.execute(
            """
            SELECT id, case_id, kind, status, risk_level, payload_json,
                   payload_sha256, blockers_json, blockers_sha256, version,
                   created_at, updated_at
            FROM action WHERE case_id = ? ORDER BY created_at, id
            """,
            (case_id,),
        ).fetchall()
        graph: dict[str, tuple[tuple[str, int], ...]] = {}
        for graph_row in all_rows:
            graph_id = str(graph_row["id"])
            edges = self._connection.execute(
                """
                SELECT depends_on_action_id, depends_on_action_version
                FROM action_dependency WHERE action_id = ? ORDER BY position
                """,
                (graph_id,),
            ).fetchall()
            graph[graph_id] = tuple((str(item[0]), int(item[1])) for item in edges)
        if any(edge[0] not in graph for edges in graph.values() for edge in edges):
            raise ActionIntegrityError("Action dependency scope is invalid")
        _topological_order(all_rows, graph)

        current = self._base_record(row)
        requirements = self._read_requirements(action_id, case_id, payload=current.payload)
        dependency_rows = self._connection.execute(
            """
            SELECT d.depends_on_action_id, d.depends_on_action_version,
                   a.case_id, a.status, a.version
            FROM action_dependency AS d
            JOIN action AS a ON a.id = d.depends_on_action_id
            WHERE d.action_id = ? ORDER BY d.position
            """,
            (action_id,),
        ).fetchall()
        dependencies: list[ActionDependency] = []
        for dependency_row in dependency_rows:
            if str(dependency_row["case_id"]) != case_id:
                raise ActionIntegrityError("Action dependency scope is invalid")
            dependencies.append(
                ActionDependency(
                    action_id=str(dependency_row["depends_on_action_id"]),
                    action_version=int(dependency_row["depends_on_action_version"]),
                    status=ActionStatus(str(dependency_row["status"])),
                )
            )
        deadline_records = DeadlineService(self._connection).list_case_deadlines_in_transaction(
            case_id
        )
        deadline = self._read_deadline(action_id, {item.id: item for item in deadline_records})
        evidence = self._read_completion_evidence(
            action_id,
            case_id,
            historical_payload=current.payload if current.status is ActionStatus.DONE else None,
        )
        self._validate_history(
            action_id=action_id,
            case_id=case_id,
            status=current.status,
            version=current.version,
            payload_sha256=current.payload.sha256,
            blockers_sha256=current.blockers_sha256,
        )
        return ActionRecord(
            id=current.id,
            case_id=current.case_id,
            kind=current.kind,
            status=current.status,
            risk_level=current.risk_level,
            payload=current.payload,
            blockers=current.blockers,
            blockers_sha256=current.blockers_sha256,
            requirements=requirements,
            dependencies=tuple(dependencies),
            deadline=deadline,
            completion_evidence=evidence,
            communication_draft=self._communication_draft(current.payload, requirements),
            version=current.version,
            created_at=current.created_at,
            updated_at=current.updated_at,
            analysis_basis_status=self._analysis_status(case_id, current.payload),
        )

    def _read_dependency_rows(
        self,
        case_id: str,
        row_by_id: dict[str, sqlite3.Row],
    ) -> dict[str, tuple[tuple[str, int], ...]]:
        result: dict[str, tuple[tuple[str, int], ...]] = {}
        for action_id in row_by_id:
            rows = self._connection.execute(
                """
                SELECT case_id, depends_on_action_id, depends_on_action_version
                FROM action_dependency WHERE action_id = ? ORDER BY position
                """,
                (action_id,),
            ).fetchall()
            dependencies: list[tuple[str, int]] = []
            for row in rows:
                dependency_id = str(row["depends_on_action_id"])
                if str(row["case_id"]) != case_id or dependency_id not in row_by_id:
                    raise ActionIntegrityError("Action dependency scope is invalid")
                dependencies.append((dependency_id, int(row["depends_on_action_version"])))
            result[action_id] = tuple(dependencies)
        return result

    def _read_requirements(
        self, action_id: str, case_id: str, *, payload: ActionPayload | None = None
    ) -> tuple[ActionRequirement, ...]:
        if payload is not None and self._analysis_bound(payload):
            return self._read_analysis_requirements(action_id, case_id, payload)
        rows = self._connection.execute(
            """
            SELECT r.claim_id, r.claim_version, r.value_sha256, r.field,
                   c.case_id AS claim_case_id, c.field AS claim_field,
                   c.value_json, c.value_sha256 AS current_sha256,
                   c.comparison_sha256, c.status, c.contradiction, c.version
            FROM action_requirement_claim AS r
            JOIN claim AS c ON c.id = r.claim_id
            WHERE r.action_id = ? ORDER BY r.position
            """,
            (action_id,),
        ).fetchall()
        result: list[ActionRequirement] = []
        for row in rows:
            value = ClaimValue.from_storage(
                str(row["value_json"]),
                str(row["current_sha256"]),
                str(row["comparison_sha256"]),
            )
            if str(row["claim_case_id"]) != case_id:
                raise ActionIntegrityError("Action Claim scope is invalid")
            if (
                int(row["version"]) != int(row["claim_version"])
                or value.sha256 != str(row["value_sha256"])
                or str(row["claim_field"]) != str(row["field"])
            ):
                raise ActionRequirementStaleError("Action Claim requirement is stale")
            if ClaimStatus(str(row["status"])) not in _AUTHORITATIVE_CLAIM_STATUSES or bool(
                row["contradiction"]
            ):
                raise ActionRequirementInvalidError("Action Claim requirement is not authoritative")
            result.append(
                ActionRequirement(
                    claim_id=str(row["claim_id"]),
                    claim_version=int(row["claim_version"]),
                    value_sha256=str(row["value_sha256"]),
                    field=str(row["field"]),
                )
            )
        return tuple(result)

    def _read_analysis_requirements(
        self, action_id: str, case_id: str, payload: ActionPayload
    ) -> tuple[ActionRequirement, ...]:
        from .analysis_draft import analysis_action_facts

        facts = analysis_action_facts(self._connection, case_id, payload)
        assert facts is not None
        by_id = {str(item["claim_id"]): item for item in facts}
        result: list[ActionRequirement] = []
        for row in self._connection.execute(
            "SELECT r.*, c.case_id AS claim_case_id FROM action_requirement_claim r "
            "JOIN claim c ON c.id = r.claim_id WHERE r.action_id = ? ORDER BY r.position",
            (action_id,),
        ).fetchall():
            fact = by_id.get(str(row["claim_id"]))
            if (
                row["case_id"] != case_id
                or row["claim_case_id"] != case_id
                or fact is None
                or fact["usable_as_fact"] is not True
                or fact["field"] != row["field"]
                or fact["version"] != row["claim_version"]
                or fact["value_sha256"] != row["value_sha256"]
            ):
                raise ActionIntegrityError("Action historical Claim binding is invalid")
            result.append(
                ActionRequirement(
                    claim_id=str(row["claim_id"]),
                    claim_version=int(row["claim_version"]),
                    value_sha256=str(row["value_sha256"]),
                    field=str(row["field"]),
                )
            )
        return tuple(result)

    def _read_deadline(
        self, action_id: str, deadlines: Mapping[str, DeadlineRecord]
    ) -> ActionDeadline | None:
        row = self._connection.execute(
            """
            SELECT deadline_id, deadline_version, definition_sha256
            FROM action_deadline WHERE action_id = ?
            """,
            (action_id,),
        ).fetchone()
        if row is None:
            return None
        record = deadlines.get(str(row["deadline_id"]))
        if record is None:
            raise ActionDeadlineInvalidError("Action Deadline is unavailable")
        if record.version != int(row["deadline_version"]) or record.definition_sha256 != str(
            row["definition_sha256"]
        ):
            raise ActionDeadlineInvalidError("Action Deadline is stale")
        return ActionDeadline(
            deadline_id=record.id,
            deadline_version=record.version,
            definition_sha256=record.definition_sha256,
            date_kind=record.due.kind.value,
            due_value=record.due.value,
            timezone=record.due.timezone,
            status=record.status.value,
        )

    def _read_completion_evidence(
        self, action_id: str, case_id: str, *, historical_payload: ActionPayload | None = None
    ) -> tuple[ActionCompletionEvidence, ...]:
        rows = self._connection.execute(
            """
            SELECT authority_type, authority_id, authority_version, authority_sha256
            FROM action_completion_evidence
            WHERE action_id = ? AND case_id = ? ORDER BY position
            """,
            (action_id, case_id),
        ).fetchall()
        result: list[ActionCompletionEvidence] = []
        for row in rows:
            item = CompletionEvidenceInput(
                authority_type=CompletionAuthorityType(str(row["authority_type"])),
                authority_id=str(row["authority_id"]),
                expected_version=(
                    int(row["authority_version"]) if row["authority_version"] is not None else None
                ),
                authority_sha256=str(row["authority_sha256"]),
            )
            if (
                historical_payload is not None
                and self._analysis_bound(historical_payload)
                and item.authority_type is CompletionAuthorityType.CLAIM
            ):
                result.append(self._historical_completion_claim(case_id, item, historical_payload))
            else:
                result.append(self._validate_completion_evidence(case_id, item))
        return tuple(result)

    def _historical_completion_claim(
        self, case_id: str, item: CompletionEvidenceInput, payload: ActionPayload
    ) -> ActionCompletionEvidence:
        """Read a completed analysis Action's exact Claim snapshot without rebasing it."""
        from .analysis_draft import analysis_action_facts

        facts = analysis_action_facts(self._connection, case_id, payload)
        fact = next((fact for fact in facts or () if fact["claim_id"] == item.authority_id), None)
        if (
            fact is None
            or fact["usable_as_fact"] is not True
            or fact["version"] != item.expected_version
            or fact["value_sha256"] != item.authority_sha256
        ):
            raise ActionIntegrityError("Historical completion Claim is not in the analysis basis")
        try:
            service = ClaimService(self._connection)
            claim = service.get_claim(item.authority_id)
            events = service.list_claim_events(claim.id)
            if claim.case_id != case_id or len(events) != claim.version or not events:
                raise ActionIntegrityError(
                    "Historical completion Claim scope or history is invalid"
                )
            for position, event in enumerate(events):
                prior = events[position - 1] if position else None
                if (
                    event.prior_version != position
                    or event.new_version != position + 1
                    or event.prior_status != (prior.new_status if prior else None)
                    or event.prior_value != (prior.new_value if prior else None)
                    or (position == 0) != (event.event_type is ClaimEventType.CREATED)
                ):
                    raise ActionIntegrityError("Historical completion Claim chain is invalid")
                if (
                    event.event_type is ClaimEventType.CONFIRMED
                    and event.new_status is not ClaimStatus.CONFIRMED
                    or event.event_type is ClaimEventType.CORRECTED
                    and event.new_status is not ClaimStatus.CORRECTED
                    or event.event_type is ClaimEventType.REJECTED
                    and event.new_status is not ClaimStatus.REJECTED
                    or event.event_type is ClaimEventType.SOURCE_ATTACHED
                    and (
                        event.new_status != event.prior_status
                        or event.new_value != event.prior_value
                    )
                ):
                    raise ActionIntegrityError("Historical completion Claim event is invalid")
            last = events[-1]
            version = item.expected_version
            assert version is not None
            if (
                last.new_value != claim.value
                or last.new_status is not claim.status
                or version > len(events)
                or events[version - 1].new_value.sha256 != item.authority_sha256
                or events[version - 1].new_status.value != fact["status"]
                or events[version - 1].new_status not in _AUTHORITATIVE_CLAIM_STATUSES
            ):
                raise ActionIntegrityError("Historical completion Claim version is inconsistent")
            confirmation = fact["confirmation"]
            if fact["confirmation_basis"] == "correction":
                matching = next(
                    (
                        event
                        for event in reversed(events[:version])
                        if event.event_type is ClaimEventType.CORRECTED
                        and event.new_value.sha256 == item.authority_sha256
                    ),
                    None,
                )
                metadata = (
                    None
                    if matching is None
                    else {
                        "actor": matching.actor,
                        "reason": matching.reason,
                        "occurred_at": matching.occurred_at,
                    }
                )
            else:
                recorded = next(
                    (
                        confirmation
                        for confirmation in reversed(service.list_claim_confirmations(claim.id))
                        if confirmation.claim_version <= version
                        and confirmation.value_sha256 == item.authority_sha256
                        and confirmation.basis.value == fact["confirmation_basis"]
                    ),
                    None,
                )
                metadata = (
                    None
                    if recorded is None
                    else {
                        "actor": recorded.actor,
                        "reason": recorded.reason,
                        "occurred_at": recorded.confirmed_at,
                    }
                )
            if confirmation != metadata or metadata is None:
                raise ActionIntegrityError("Historical completion confirmation is inconsistent")
            citations = fact["citations"]
            if not isinstance(citations, list):
                raise ActionIntegrityError("Historical completion sources are invalid")
            for citation in citations:
                if (
                    not isinstance(citation, dict)
                    or self._connection.execute(
                        "SELECT 1 FROM source s JOIN source_segment ss ON ss.source_id = s.id "
                        "WHERE s.case_id = ? AND s.id = ? AND ss.id = ?",
                        (case_id, citation["source_id"], citation["segment_id"]),
                    ).fetchone()
                    is None
                ):
                    raise ActionIntegrityError("Historical completion source scope is invalid")
        except ClaimServiceError as error:
            raise ActionIntegrityError("Historical completion Claim cannot be read") from error
        return ActionCompletionEvidence(
            item.authority_type, item.authority_id, item.expected_version, item.authority_sha256
        )

    def _communication_draft(
        self,
        payload: ActionPayload,
        requirements: tuple[ActionRequirement, ...],
    ) -> str | None:
        if payload.kind is not ActionKind.PREPARE_COMMUNICATION:
            return None
        metadata = payload.to_dict()
        if "draft_text" in metadata:
            return metadata["draft_text"]
        lines = [
            f"Intent: {metadata['intent']}",
            f"Recipient role: {metadata['recipient_role']}",
            "Authoritative case facts:",
        ]
        for requirement in requirements:
            row = self._connection.execute(
                "SELECT value_json FROM claim WHERE id = ?", (requirement.claim_id,)
            ).fetchone()
            if row is None:
                raise ActionIntegrityError("Action Claim requirement is unavailable")
            lines.append(f"- {requirement.field}: {str(row['value_json'])}")
        return "\n".join(lines)

    def _validate_history(
        self,
        *,
        action_id: str,
        case_id: str,
        status: ActionStatus,
        version: int,
        payload_sha256: str,
        blockers_sha256: str,
    ) -> None:
        events = self._read_events(action_id)
        if len(events) != version or not events:
            raise ActionIntegrityError("Action history is incomplete")
        for position, event in enumerate(events, start=1):
            if (
                event.case_id != case_id
                or event.new_version != position
                or event.prior_version != position - 1
                or (position == 1 and event.prior_status is not None)
                or (position > 1 and event.prior_status is not events[position - 2].new_status)
                or not _is_valid_action_event_transition(event)
            ):
                raise ActionIntegrityError("Action history is invalid")
        last = events[-1]
        if (
            last.new_status is not status
            or last.payload_sha256 != payload_sha256
            or last.blockers_sha256 != blockers_sha256
        ):
            raise ActionIntegrityError("Action history does not match authority")

    def _read_events(self, action_id: str) -> tuple[ActionEvent, ...]:
        rows = self._connection.execute(
            """
            SELECT id, action_id, case_id, event_type, prior_status, new_status,
                   prior_version, new_version, payload_sha256, blockers_sha256,
                   actor, reason, occurred_at
            FROM action_event WHERE action_id = ? ORDER BY new_version, id
            """,
            (action_id,),
        ).fetchall()
        return tuple(
            ActionEvent(
                id=str(row["id"]),
                action_id=str(row["action_id"]),
                case_id=str(row["case_id"]),
                event_type=ActionEventType(str(row["event_type"])),
                prior_status=(
                    ActionStatus(str(row["prior_status"]))
                    if row["prior_status"] is not None
                    else None
                ),
                new_status=ActionStatus(str(row["new_status"])),
                prior_version=int(row["prior_version"]),
                new_version=int(row["new_version"]),
                payload_sha256=str(row["payload_sha256"]),
                blockers_sha256=str(row["blockers_sha256"]),
                actor=str(row["actor"]),
                reason=str(row["reason"]),
                occurred_at=str(row["occurred_at"]),
            )
            for row in rows
        )

    def _insert_event(
        self,
        *,
        action_id: str,
        case_id: str,
        event_type: ActionEventType,
        prior_status: ActionStatus | None,
        new_status: ActionStatus,
        prior_version: int,
        new_version: int,
        payload_sha256: str,
        blockers_sha256: str,
        actor: str,
        reason: str,
        occurred_at: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO action_event (
                id, action_id, case_id, event_type, prior_status, new_status,
                prior_version, new_version, payload_sha256, blockers_sha256,
                actor, reason, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("action_event"),
                action_id,
                case_id,
                event_type.value,
                prior_status.value if prior_status is not None else None,
                new_status.value,
                prior_version,
                new_version,
                payload_sha256,
                blockers_sha256,
                actor,
                reason,
                occurred_at,
            ),
        )

    def _require_idle(self, message: str) -> None:
        if self._connection.in_transaction:
            raise ActionPersistenceError(message)

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")


def _is_valid_action_event_transition(event: ActionEvent) -> bool:
    allowed: dict[ActionEventType, set[tuple[ActionStatus | None, ActionStatus]]] = {
        ActionEventType.CREATED: {
            (None, ActionStatus.BLOCKED),
            (None, ActionStatus.READY),
        },
        ActionEventType.REVISED: {
            (prior, new)
            for prior in {
                ActionStatus.PROPOSED,
                ActionStatus.BLOCKED,
                ActionStatus.READY,
                ActionStatus.APPROVED,
            }
            for new in {ActionStatus.BLOCKED, ActionStatus.READY}
        },
        ActionEventType.REVALIDATED: {
            (prior, new)
            for prior in {ActionStatus.PROPOSED, ActionStatus.BLOCKED, ActionStatus.READY}
            for new in {ActionStatus.BLOCKED, ActionStatus.READY}
        },
        ActionEventType.STARTED: {
            (ActionStatus.READY, ActionStatus.RUNNING),
            (ActionStatus.APPROVED, ActionStatus.RUNNING),
        },
        ActionEventType.COMPLETED: {(ActionStatus.RUNNING, ActionStatus.DONE)},
        ActionEventType.FAILED: {(ActionStatus.RUNNING, ActionStatus.FAILED)},
        ActionEventType.CANCELLED: {
            (prior, ActionStatus.CANCELLED)
            for prior in {
                ActionStatus.PROPOSED,
                ActionStatus.BLOCKED,
                ActionStatus.READY,
                ActionStatus.RUNNING,
                ActionStatus.FAILED,
                ActionStatus.APPROVED,
            }
        },
        ActionEventType.APPROVED: {(ActionStatus.READY, ActionStatus.APPROVED)},
    }
    return (event.prior_status, event.new_status) in allowed[event.event_type]


def _canonical_blockers(blockers: Iterable[ActionBlocker]) -> tuple[str, str]:
    values = [
        {
            "authority_id": blocker.authority_id,
            "code": blocker.code.value,
            "field": blocker.field,
        }
        for blocker in blockers
    ]
    text = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text.encode("utf-8")) > 4096:
        raise ActionIntegrityError("Action blockers are too large")
    return text, hashlib.sha256(text.encode()).hexdigest()


def _parse_blockers(text: str, expected_sha256: str) -> tuple[ActionBlocker, ...]:
    if hashlib.sha256(text.encode()).hexdigest() != expected_sha256:
        raise ActionIntegrityError("Action blockers are invalid")
    try:
        values = json.loads(text)
        if not isinstance(values, list):
            raise ValueError
        blockers = tuple(
            ActionBlocker(
                code=ActionBlockerCode(item["code"]),
                field=item["field"],
                authority_id=item["authority_id"],
            )
            for item in values
            if isinstance(item, dict)
            and set(item) == {"authority_id", "code", "field"}
            and (item["field"] is None or isinstance(item["field"], str))
            and (item["authority_id"] is None or isinstance(item["authority_id"], str))
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ActionIntegrityError("Action blockers are invalid") from error
    if len(blockers) != len(values) or _canonical_blockers(blockers)[0] != text:
        raise ActionIntegrityError("Action blockers are invalid")
    return blockers


def _topological_order(
    rows: list[sqlite3.Row],
    dependencies: dict[str, tuple[tuple[str, int], ...]],
) -> tuple[str, ...]:
    stable_ids = [str(row["id"]) for row in rows]
    remaining = {
        action_id: {item[0] for item in dependencies[action_id]} for action_id in stable_ids
    }
    result: list[str] = []
    while remaining:
        ready = [
            action_id
            for action_id in stable_ids
            if action_id in remaining and not remaining[action_id]
        ]
        if not ready:
            raise ActionIntegrityError("Action dependency graph contains a cycle")
        for action_id in ready:
            result.append(action_id)
            del remaining[action_id]
            for values in remaining.values():
                values.discard(action_id)
    return tuple(result)


def _bounded_id(value: object) -> str:
    if not isinstance(value, str):
        raise ActionNotFoundError
    clean = value.strip()
    if not 1 <= len(clean) <= 200:
        raise ActionNotFoundError
    return clean


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
