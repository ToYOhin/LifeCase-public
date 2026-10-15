"""Immutable M5-02 Action values and create-command contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ActionCommandValidationError(ValueError):
    """Raised when an Action value or command violates its boundary."""


class ActionKind(StrEnum):
    COLLECT_EVIDENCE = "collect_evidence"
    PREPARE_COMMUNICATION = "prepare_communication"
    FOLLOW_UP = "follow_up"
    RECORD_OUTCOME = "record_outcome"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    BLOCKED = "blocked"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    APPROVED = "approved"


class ActionRiskLevel(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class ActionEventType(StrEnum):
    CREATED = "created"
    REVISED = "revised"
    REVALIDATED = "revalidated"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    APPROVED = "approved"


class ActionBlockerCode(StrEnum):
    MISSING_REQUIRED_CLAIM = "missing_required_claim"
    DEPENDENCY_INCOMPLETE = "dependency_incomplete"
    COLLECTION_TARGET_SATISFIED = "collection_target_satisfied"


class CompletionAuthorityType(StrEnum):
    CLAIM = "claim"
    SOURCE = "source"
    ARTIFACT = "artifact"


@dataclass(frozen=True, slots=True)
class ActionPayload:
    kind: ActionKind
    json_text: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActionKind):
            raise ActionCommandValidationError("Action kind is invalid")
        try:
            value = json.loads(self.json_text)
        except (TypeError, json.JSONDecodeError) as error:
            raise ActionCommandValidationError("Action payload is invalid") from error
        canonical = _canonical_payload(self.kind, value)
        if (
            canonical != self.json_text
            or len(canonical.encode("utf-8")) > 2048
            or hashlib.sha256(canonical.encode()).hexdigest() != self.sha256
        ):
            raise ActionCommandValidationError("Action payload is invalid")

    @classmethod
    def from_input(cls, kind: ActionKind, value: object) -> ActionPayload:
        canonical = _canonical_payload(kind, value)
        return cls(kind, canonical, hashlib.sha256(canonical.encode()).hexdigest())

    @classmethod
    def from_storage(
        cls,
        *,
        kind: ActionKind,
        json_text: str,
        sha256: str,
    ) -> ActionPayload:
        return cls(kind, json_text, sha256)

    def to_dict(self) -> dict[str, str]:
        value = json.loads(self.json_text)
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise ActionCommandValidationError("Action payload is invalid")
        return value


@dataclass(frozen=True, slots=True)
class ClaimRequirementInput:
    claim_id: str
    expected_claim_version: int
    value_sha256: str
    field: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _bounded_text(self.claim_id, 200, "Claim is invalid"))
        _positive_version(self.expected_claim_version)
        _hash(self.value_sha256, "Claim is invalid")
        object.__setattr__(self, "field", _identifier(self.field, "Claim field is invalid"))


@dataclass(frozen=True, slots=True)
class DependencyInput:
    action_id: str
    expected_action_version: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_id",
            _bounded_text(self.action_id, 200, "Action dependency is invalid"),
        )
        _positive_version(self.expected_action_version)


@dataclass(frozen=True, slots=True)
class DeadlineLinkInput:
    deadline_id: str
    expected_deadline_version: int
    definition_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "deadline_id",
            _bounded_text(self.deadline_id, 200, "Action Deadline is invalid"),
        )
        _positive_version(self.expected_deadline_version)
        _hash(self.definition_sha256, "Action Deadline is invalid")


@dataclass(frozen=True, slots=True)
class CreateActionCommand:
    case_id: str
    kind: ActionKind
    risk_level: ActionRiskLevel
    payload: ActionPayload
    requirements: tuple[ClaimRequirementInput, ...]
    dependencies: tuple[DependencyInput, ...]
    deadline: DeadlineLinkInput | None
    actor: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _bounded_text(self.case_id, 200, "Case ID is invalid"))
        if not isinstance(self.kind, ActionKind):
            raise ActionCommandValidationError("Action kind is invalid")
        if not isinstance(self.risk_level, ActionRiskLevel) or self.risk_level is ActionRiskLevel.D:
            raise ActionCommandValidationError("Action risk is prohibited")
        if not isinstance(self.payload, ActionPayload) or self.payload.kind is not self.kind:
            raise ActionCommandValidationError("Action payload is invalid")
        _typed_tuple(
            self.requirements,
            ClaimRequirementInput,
            32,
            "Action requirements are invalid",
        )
        _typed_tuple(self.dependencies, DependencyInput, 32, "Action dependencies are invalid")
        if len({item.claim_id for item in self.requirements}) != len(self.requirements):
            raise ActionCommandValidationError("Action requirements are invalid")
        if len({item.field for item in self.requirements}) != len(self.requirements):
            raise ActionCommandValidationError("Action requirements are invalid")
        if len({item.action_id for item in self.dependencies}) != len(self.dependencies):
            raise ActionCommandValidationError("Action dependencies are invalid")
        if self.deadline is not None and not isinstance(self.deadline, DeadlineLinkInput):
            raise ActionCommandValidationError("Action Deadline is invalid")
        object.__setattr__(
            self,
            "actor",
            _bounded_text(self.actor, 120, "Action metadata is invalid"),
        )
        object.__setattr__(
            self,
            "reason",
            _bounded_text(self.reason, 1000, "Action metadata is invalid"),
        )


@dataclass(frozen=True, slots=True)
class ReviseActionCommand:
    action_id: str
    expected_version: int
    definition: CreateActionCommand

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_id",
            _bounded_text(self.action_id, 200, "Action ID is invalid"),
        )
        _positive_version(self.expected_version)
        if not isinstance(self.definition, CreateActionCommand):
            raise ActionCommandValidationError("Action definition is invalid")


@dataclass(frozen=True, slots=True)
class ActionStatusCommand:
    action_id: str
    expected_version: int
    actor: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_id",
            _bounded_text(self.action_id, 200, "Action ID is invalid"),
        )
        _positive_version(self.expected_version)
        object.__setattr__(
            self,
            "actor",
            _bounded_text(self.actor, 120, "Action metadata is invalid"),
        )
        object.__setattr__(
            self,
            "reason",
            _bounded_text(self.reason, 1000, "Action metadata is invalid"),
        )


@dataclass(frozen=True, slots=True)
class CompletionEvidenceInput:
    authority_type: CompletionAuthorityType
    authority_id: str
    expected_version: int | None
    authority_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.authority_type, CompletionAuthorityType):
            raise ActionCommandValidationError("Completion evidence is invalid")
        object.__setattr__(
            self,
            "authority_id",
            _bounded_text(self.authority_id, 200, "Completion evidence is invalid"),
        )
        if self.authority_type is CompletionAuthorityType.CLAIM:
            _positive_version(self.expected_version)
        elif self.expected_version is not None:
            raise ActionCommandValidationError("Completion evidence is invalid")
        _hash(self.authority_sha256, "Completion evidence is invalid")


@dataclass(frozen=True, slots=True)
class CompleteActionCommand:
    action_id: str
    expected_version: int
    evidence: tuple[CompletionEvidenceInput, ...]
    actor: str
    reason: str

    def __post_init__(self) -> None:
        status = ActionStatusCommand(
            self.action_id,
            self.expected_version,
            self.actor,
            self.reason,
        )
        object.__setattr__(self, "action_id", status.action_id)
        object.__setattr__(self, "actor", status.actor)
        object.__setattr__(self, "reason", status.reason)
        _typed_tuple(self.evidence, CompletionEvidenceInput, 32, "Completion evidence is invalid")
        if not self.evidence or len(
            {(item.authority_type, item.authority_id) for item in self.evidence}
        ) != len(self.evidence):
            raise ActionCommandValidationError("Completion evidence is invalid")


@dataclass(frozen=True, slots=True)
class ActionBlocker:
    code: ActionBlockerCode
    field: str | None = None
    authority_id: str | None = None


@dataclass(frozen=True, slots=True)
class ActionRequirement:
    claim_id: str
    claim_version: int
    value_sha256: str
    field: str


@dataclass(frozen=True, slots=True)
class ActionDependency:
    action_id: str
    action_version: int
    status: ActionStatus


@dataclass(frozen=True, slots=True)
class ActionDeadline:
    deadline_id: str
    deadline_version: int
    definition_sha256: str
    date_kind: str
    due_value: str
    timezone: str
    status: str


@dataclass(frozen=True, slots=True)
class ActionCompletionEvidence:
    authority_type: CompletionAuthorityType
    authority_id: str
    authority_version: int | None
    authority_sha256: str


@dataclass(frozen=True, slots=True)
class ActionRecord:
    id: str
    case_id: str
    kind: ActionKind
    status: ActionStatus
    risk_level: ActionRiskLevel
    payload: ActionPayload
    blockers: tuple[ActionBlocker, ...]
    blockers_sha256: str
    requirements: tuple[ActionRequirement, ...]
    dependencies: tuple[ActionDependency, ...]
    deadline: ActionDeadline | None
    completion_evidence: tuple[ActionCompletionEvidence, ...]
    communication_draft: str | None
    version: int
    created_at: str
    updated_at: str
    analysis_basis_status: Literal["current", "changed", "unavailable"] | None = None


@dataclass(frozen=True, slots=True)
class ActionEvent:
    id: str
    action_id: str
    case_id: str
    event_type: ActionEventType
    prior_status: ActionStatus | None
    new_status: ActionStatus
    prior_version: int
    new_version: int
    payload_sha256: str
    blockers_sha256: str
    actor: str
    reason: str
    occurred_at: str


def _canonical_payload(kind: ActionKind, value: object) -> str:
    if not isinstance(kind, ActionKind) or not isinstance(value, dict):
        raise ActionCommandValidationError("Action payload is invalid")
    if kind is ActionKind.COLLECT_EVIDENCE:
        _exact_keys(value, {"requirement_key", "requirement_type"})
        normalized = {
            "requirement_key": _identifier(value["requirement_key"], "Action payload is invalid"),
            "requirement_type": _choice(
                value["requirement_type"], {"claim", "evidence"}, "Action payload is invalid"
            ),
        }
    elif kind is ActionKind.PREPARE_COMMUNICATION:
        base_keys = {"intent", "recipient_role"}
        analysis_keys = {
            "draft_text",
            "analysis_run_id",
            "analysis_basis_sha256",
            "analysis_answer_sha256",
        }
        _exact_keys(value, base_keys | analysis_keys if set(value) & analysis_keys else base_keys)
        normalized = {
            "intent": _choice(
                value["intent"],
                {"request_information", "request_remedy", "follow_up"},
                "Action payload is invalid",
            ),
            "recipient_role": _identifier(value["recipient_role"], "Action payload is invalid"),
        }
        if "draft_text" in value:
            if normalized != {"intent": "request_remedy", "recipient_role": "merchant"}:
                raise ActionCommandValidationError("Analysis Action purpose is invalid")
            normalized.update(
                draft_text=validate_analysis_draft_text(value["draft_text"]),
                analysis_run_id=_bounded_text(
                    value["analysis_run_id"], 200, "Analysis Run is invalid"
                ),
                analysis_basis_sha256=_hash(
                    value["analysis_basis_sha256"], "Analysis basis is invalid"
                ),
                analysis_answer_sha256=_hash(
                    value["analysis_answer_sha256"], "Analysis answer is invalid"
                ),
            )
    elif kind is ActionKind.FOLLOW_UP:
        _exact_keys(value, {"purpose"})
        normalized = {"purpose": _bounded_text(value["purpose"], 500, "Action payload is invalid")}
    else:
        _exact_keys(value, {"outcome"})
        normalized = {"outcome": _bounded_text(value["outcome"], 500, "Action payload is invalid")}
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(canonical.encode("utf-8")) > 2048:
        raise ActionCommandValidationError("Action payload is invalid")
    return canonical


def _exact_keys(value: dict[object, object], keys: set[str]) -> None:
    if set(value) != keys or not all(isinstance(key, str) for key in value):
        raise ActionCommandValidationError("Action payload is invalid")


def validate_analysis_draft_text(value: object) -> str:
    """Keep reviewed text verbatim, within the existing Action payload budget."""
    if not isinstance(value, str) or not value.strip() or len(value) > 500 or "\x00" in value:
        raise ActionCommandValidationError("Analysis draft text is invalid")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ActionCommandValidationError("Analysis draft text is invalid") from error
    return value


def _typed_tuple(value: object, item_type: type[object], maximum: int, message: str) -> None:
    if (
        not isinstance(value, tuple)
        or len(value) > maximum
        or not all(isinstance(item, item_type) for item in value)
    ):
        raise ActionCommandValidationError(message)


def _bounded_text(value: object, maximum: int, message: str) -> str:
    if not isinstance(value, str):
        raise ActionCommandValidationError(message)
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= maximum:
        raise ActionCommandValidationError(message)
    return cleaned


def _identifier(value: object, message: str) -> str:
    cleaned = _bounded_text(value, 64, message)
    if _IDENTIFIER.fullmatch(cleaned) is None:
        raise ActionCommandValidationError(message)
    return cleaned


def _choice(value: object, choices: set[str], message: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ActionCommandValidationError(message)
    return value


def _positive_version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ActionCommandValidationError("Expected version is invalid")
    return value


def _hash(value: object, message: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ActionCommandValidationError(message)
    return value
