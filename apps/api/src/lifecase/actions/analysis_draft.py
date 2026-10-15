"""A bounded, explicitly reviewed repair draft linked to an existing question Run."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Literal, cast

from lifecase.agent.demo import PortfolioAgentDemoService
from lifecase.agent.errors import AgentIntegrityError, AgentServiceError
from lifecase.agent.models import AgentRunRecord, AgentRunStatus, AgentStageName, AgentStageStatus
from lifecase.agent.question_binding import QUESTION_RUN_PREFIX, validate_question_binding
from lifecase.agent.question_context import binding_status, require_current_binding
from lifecase.agent.rag import (
    RagEvidenceSegment,
    _parse_answer,
    _validate_authority,
)
from lifecase.cases.service import CaseService
from lifecase.templates.registry import BUILT_IN_TEMPLATE_REGISTRY

from .models import (
    ActionKind,
    ActionPayload,
    ActionRecord,
    ActionRiskLevel,
    ClaimRequirementInput,
    CreateActionCommand,
    ReviseActionCommand,
    validate_analysis_draft_text,
)
from .service import ActionIntegrityError, ActionService, ActionServiceError


class AnalysisDraftStaleError(ActionServiceError):
    """The requested question or its supporting authority is no longer current."""


class AnalysisDraftConflictError(ActionServiceError):
    """An explicit save no longer matches the existing Action version."""


@dataclass(frozen=True, slots=True)
class AnalysisDraftSaveCommand:
    case_id: str
    expected_run_id: str
    expected_run_version: int
    basis_sha256: str
    answer_sha256: str
    draft_text: str
    actor: str
    reason: str
    expected_action_id: str | None = None
    expected_action_version: int | None = None


@dataclass(frozen=True, slots=True)
class _Analysis:
    run: AgentRunRecord
    binding: dict[str, object]
    answer: dict[str, object]
    answer_sha256: str

    @property
    def facts(self) -> list[dict[str, object]]:
        basis = cast(dict[str, object], self.binding["basis"])
        return cast(list[dict[str, object]], cast(dict[str, object], basis["facts"])["items"])


def _analysis(connection: sqlite3.Connection, case_id: str, run_id: str) -> _Analysis:
    if not run_id.startswith(QUESTION_RUN_PREFIX):
        raise AnalysisDraftStaleError("An answered question Run is required")
    reader = PortfolioAgentDemoService(connection, enabled=False)._reader(run_id)
    run = reader.get_run(case_id, run_id)
    if (
        run.status is not AgentRunStatus.COMPLETED
        or run.cancellation_requested
        or run.input_binding_json is None
        or len(run.stages) != 1
        or run.stages[0].stage is not AgentStageName.VALIDATE
        or run.stages[0].status is not AgentStageStatus.CHECKPOINTED
        or run.stages[0].checkpoint is None
    ):
        raise AnalysisDraftStaleError("A completed question answer is required")
    binding = validate_question_binding(run.input_binding_json, case_id)
    checkpoint = run.stages[0].checkpoint
    answer = json.loads(checkpoint.payload_json)
    if (
        not isinstance(answer, dict)
        or answer.get("schema_version") != "evidence_backed_answer.v2"
        or answer.get("status") != "answered"
        or not isinstance(answer.get("statements"), list)
        or not answer["statements"]
    ):
        raise AnalysisDraftStaleError("The question has no usable answer")
    return _Analysis(run, binding, answer, checkpoint.payload_sha256)


def _is_latest(connection: sqlite3.Connection, case_id: str, run_id: str) -> bool:
    row = connection.execute(
        "SELECT id FROM agent_run WHERE case_id = ? AND id LIKE 'portfolio-live-%' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (case_id,),
    ).fetchone()
    return row is not None and str(row["id"]) == run_id


def _current(connection: sqlite3.Connection, case_id: str, run_id: str) -> _Analysis:
    context = _analysis(connection, case_id, run_id)
    if CaseService(connection).get_case(case_id).type != "warranty" or not _is_latest(
        connection, case_id, run_id
    ):
        raise AnalysisDraftStaleError("The displayed analysis is no longer current")
    try:
        require_current_binding(connection, cast(str, context.run.input_binding_json), case_id)
    except AgentIntegrityError as error:
        raise AnalysisDraftStaleError("Analysis basis changed or is unavailable") from error
    # The stored checkpoint was validated by RAG at commit. Check its exact
    # citations again without replaying a retrieval service that owns BEGIN.
    evidence: dict[str, RagEvidenceSegment] = {}
    for statement in cast(list[object], context.answer["statements"]):
        if not isinstance(statement, dict) or not isinstance(statement.get("source_refs"), list):
            raise ActionIntegrityError("Stored analysis statement is invalid")
        for reference in statement["source_refs"]:
            if not isinstance(reference, dict):
                raise ActionIntegrityError("Stored analysis citation is invalid")
            row = connection.execute(
                "SELECT s.id AS source_id, ss.id AS segment_id, ss.sequence, ss.page, "
                "ss.text, ss.text_sha256 FROM source s "
                "JOIN source_segment ss ON ss.source_id = s.id "
                "WHERE s.case_id = ? AND s.id = ? AND ss.id = ?",
                (case_id, reference.get("source_id"), reference.get("segment_id")),
            ).fetchone()
            if row is None:
                raise ActionIntegrityError("Stored analysis citation is unavailable")
            evidence[str(row["segment_id"])] = RagEvidenceSegment(
                case_id=case_id,
                source_id=str(row["source_id"]),
                segment_id=str(row["segment_id"]),
                sequence=int(row["sequence"]),
                page=row["page"],
                text=str(row["text"]),
                text_sha256=str(row["text_sha256"]),
            )
    segments = tuple(evidence.values())
    _validate_authority(connection, case_id, segments)
    checkpoint = context.run.stages[0].checkpoint
    assert checkpoint is not None
    _parse_answer(
        checkpoint.payload_json,
        case_id,
        str(cast(dict[str, object], context.answer["tool"])["context_sha256"]),
        segments,
        cast(dict[str, object], cast(dict[str, object], context.binding["basis"])["facts"]),
    )
    return context


def _bound_analysis(
    connection: sqlite3.Connection, case_id: str, payload: ActionPayload
) -> _Analysis:
    metadata = payload.to_dict()
    context = _analysis(connection, case_id, metadata["analysis_run_id"])
    if (
        context.binding["basis_sha256"] != metadata["analysis_basis_sha256"]
        or context.answer_sha256 != metadata["analysis_answer_sha256"]
    ):
        raise ActionIntegrityError("Action analysis binding is inconsistent")
    return context


def analysis_action_status(
    connection: sqlite3.Connection, case_id: str, payload: ActionPayload
) -> Literal["current", "changed", "unavailable"] | None:
    if "analysis_run_id" not in payload.to_dict():
        return None
    try:
        context = _bound_analysis(connection, case_id, payload)
        if not _is_latest(connection, case_id, context.run.id):
            return "changed"
        return binding_status(connection, cast(str, context.run.input_binding_json), case_id)
    except (ActionServiceError, AgentServiceError, sqlite3.Error, ValueError, KeyError, TypeError):
        return "unavailable"


def analysis_action_facts(
    connection: sqlite3.Connection, case_id: str, payload: ActionPayload
) -> list[dict[str, object]] | None:
    if "analysis_run_id" not in payload.to_dict():
        return None
    try:
        return _bound_analysis(connection, case_id, payload).facts
    except (AgentServiceError, ValueError, KeyError, TypeError) as error:
        raise ActionIntegrityError("Action historical analysis is unavailable") from error


def require_current_analysis_action(
    connection: sqlite3.Connection, case_id: str, payload: ActionPayload
) -> None:
    if "analysis_run_id" not in payload.to_dict():
        return
    try:
        context = _bound_analysis(connection, case_id, payload)
        _current(connection, case_id, context.run.id)
    except (AgentServiceError, ValueError, KeyError, TypeError) as error:
        raise ActionIntegrityError("Action analysis is unavailable") from error


class AnalysisActionDraftService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def preview(self, case_id: str, run_id: str) -> dict[str, object]:
        try:
            self._connection.execute("BEGIN")
            context = _current(self._connection, case_id, run_id)
            result = self._response(context, self._saved(context))
            self._connection.execute("COMMIT")
            return result
        finally:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")

    def save(self, command: AnalysisDraftSaveCommand) -> dict[str, object]:
        validate_analysis_draft_text(command.draft_text)
        if (command.expected_action_id is None) != (command.expected_action_version is None):
            raise ValueError("Action identity and version must be supplied together")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            context = _current(self._connection, command.case_id, command.expected_run_id)
            if (
                type(command.expected_run_version) is not int
                or command.expected_run_version != context.run.version
                or command.basis_sha256 != context.binding["basis_sha256"]
                or command.answer_sha256 != context.answer_sha256
            ):
                raise AnalysisDraftStaleError("The reviewed analysis version has changed")
            definition = self._definition(context, command)
            saved = self._saved(context)
            if command.expected_action_id is None and saved is not None:
                events = ActionService(self._connection).list_action_events_in_transaction(saved.id)
                if (
                    saved.version != 1
                    or len(events) != 1
                    or events[0].actor != definition.actor
                    or events[0].reason != definition.reason
                    or saved.payload.to_dict()["draft_text"] != command.draft_text
                ):
                    raise AnalysisDraftConflictError("This analysis already has a reviewed draft")
                result = self._response(context, saved)
                self._connection.execute("COMMIT")
                return result
            if command.expected_action_id is not None and (
                saved is None
                or saved.id != command.expected_action_id
                or type(command.expected_action_version) is not int
                or saved.version != command.expected_action_version
            ):
                raise AnalysisDraftConflictError("The saved Action version has changed")
            service = ActionService(self._connection)
            if saved is None:
                action_id = service._create_action_in_transaction(definition)
            else:
                service._revise_action_in_transaction(
                    ReviseActionCommand(saved.id, saved.version, definition), saved
                )
                action_id = saved.id
            row = self._connection.execute(
                "SELECT * FROM action WHERE id = ?", (action_id,)
            ).fetchone()
            assert row is not None
            result = self._response(context, service._read_one_action(row))
            self._connection.execute("COMMIT")
            return result
        finally:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")

    def _saved(self, context: _Analysis) -> ActionRecord | None:
        matched: list[sqlite3.Row] = []
        for row in self._connection.execute(
            "SELECT * FROM action WHERE case_id = ? AND kind = 'prepare_communication'",
            (context.run.case_id,),
        ).fetchall():
            payload = ActionPayload.from_storage(
                kind=ActionKind.PREPARE_COMMUNICATION,
                json_text=str(row["payload_json"]),
                sha256=str(row["payload_sha256"]),
            )
            if payload.to_dict().get("analysis_run_id") == context.run.id:
                matched.append(row)
        if len(matched) > 1:
            raise ActionIntegrityError("Analysis has multiple saved drafts")
        return ActionService(self._connection)._read_one_action(matched[0]) if matched else None

    def _definition(
        self, context: _Analysis, command: AnalysisDraftSaveCommand
    ) -> CreateActionCommand:
        case = CaseService(self._connection).get_case(context.run.case_id)
        template = BUILT_IN_TEMPLATE_REGISTRY.load_exact(
            case_type=case.type,
            template_id=case.template_id,
            template_version=case.template_version,
        )
        representative: dict[str, dict[str, object]] = {}
        for fact in context.facts:
            if fact["usable_as_fact"] is True and fact["field"] in template.required_claims:
                representative.setdefault(str(fact["field"]), fact)
        requirements = tuple(
            ClaimRequirementInput(
                claim_id=str(item["claim_id"]),
                expected_claim_version=cast(int, item["version"]),
                value_sha256=str(item["value_sha256"]),
                field=str(item["field"]),
            )
            for item in representative.values()
        )
        return CreateActionCommand(
            case_id=context.run.case_id,
            kind=ActionKind.PREPARE_COMMUNICATION,
            risk_level=ActionRiskLevel.B,
            payload=_payload(context, command.draft_text),
            requirements=requirements,
            dependencies=(),
            deadline=None,
            actor=command.actor,
            reason=command.reason,
        )

    def _response(self, context: _Analysis, saved: ActionRecord | None) -> dict[str, object]:
        draft_text = (
            _default_text(context) if saved is None else saved.payload.to_dict()["draft_text"]
        )
        return {
            "case_id": context.run.case_id,
            "run_id": context.run.id,
            "run_version": context.run.version,
            "question": context.binding["question"],
            "basis_sha256": context.binding["basis_sha256"],
            "answer_sha256": context.answer_sha256,
            "draft_text": draft_text,
            "saved_action": None
            if saved is None
            else {
                "id": saved.id,
                "version": saved.version,
                "draft_text": draft_text,
                "status": saved.status.value,
            },
        }


def _payload(context: _Analysis, text: str) -> ActionPayload:
    return ActionPayload.from_input(
        ActionKind.PREPARE_COMMUNICATION,
        {
            "intent": "request_remedy",
            "recipient_role": "merchant",
            "draft_text": text,
            "analysis_run_id": context.run.id,
            "analysis_basis_sha256": context.binding["basis_sha256"],
            "analysis_answer_sha256": context.answer_sha256,
        },
    )


def _default_text(context: _Analysis) -> str:
    labels = {
        "product_name": "商品",
        "merchant_or_manufacturer": "商家或厂家",
        "purchase_date": "购买日期",
        "order_number": "订单号",
        "desired_outcome": "已核实诉求",
    }
    lines = [
        "报修行动草案（请审阅）",
        "本次问题：" + _excerpt(str(context.binding["question"]), 60),
    ]
    for field, label in labels.items():
        fact = next(
            (
                item
                for item in context.facts
                if item["field"] == field and item["usable_as_fact"] is True
            ),
            None,
        )
        if fact is not None:
            value = fact["value"]
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            lines.append(label + "：" + _excerpt(text, 36))
    statements = cast(list[dict[str, object]], context.answer["statements"])
    lines.append("分析摘录：" + _excerpt(str(statements[0]["text"]), 70))
    lines.append("请协助核对报修流程及仍需补充的材料。")
    result = "\n".join(lines)
    _payload(context, result)
    return result


def _excerpt(text: str, maximum: int) -> str:
    return text if len(text) <= maximum else text[: maximum - 1] + "…"
