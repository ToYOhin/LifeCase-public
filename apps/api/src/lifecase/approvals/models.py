"""Immutable M5-03 Approval Gate contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ApprovalCommandValidationError(ValueError):
    pass


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class ApprovalEventType(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True)
class ApprovalSummary:
    text: str
    sha256: str

    def __post_init__(self) -> None:
        canonical = _summary_text(self.text)
        if canonical != self.text or hashlib.sha256(canonical.encode()).hexdigest() != self.sha256:
            raise ApprovalCommandValidationError("Approval summary is invalid")

    @classmethod
    def from_input(cls, value: object) -> ApprovalSummary:
        text = _summary_text(value)
        return cls(text, hashlib.sha256(text.encode()).hexdigest())


@dataclass(frozen=True, slots=True)
class RequestApprovalCommand:
    action_id: str
    expected_action_version: int
    payload_sha256: str
    summary: ApprovalSummary
    actor: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", _text(self.action_id, 200, "Action is invalid"))
        _version(self.expected_action_version)
        _hash(self.payload_sha256, "Action is invalid")
        if not isinstance(self.summary, ApprovalSummary):
            raise ApprovalCommandValidationError("Approval summary is invalid")
        object.__setattr__(self, "actor", _text(self.actor, 120, "Approval metadata is invalid"))
        object.__setattr__(
            self,
            "reason",
            _text(self.reason, 1000, "Approval metadata is invalid"),
        )


@dataclass(frozen=True, slots=True)
class ApprovalDecisionCommand:
    approval_id: str
    action_id: str
    expected_approval_version: int
    expected_action_version: int
    actor: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "approval_id",
            _text(self.approval_id, 200, "Approval is invalid"),
        )
        object.__setattr__(self, "action_id", _text(self.action_id, 200, "Action is invalid"))
        _version(self.expected_approval_version)
        _version(self.expected_action_version)
        object.__setattr__(self, "actor", _text(self.actor, 120, "Approval metadata is invalid"))
        object.__setattr__(
            self,
            "reason",
            _text(self.reason, 1000, "Approval metadata is invalid"),
        )


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: str
    action_id: str
    case_id: str
    action_version: int
    payload_sha256: str
    summary: ApprovalSummary
    status: ApprovalStatus
    version: int
    requested_by: str
    requested_reason: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ApprovalEvent:
    id: str
    approval_id: str
    action_id: str
    case_id: str
    event_type: ApprovalEventType
    prior_status: ApprovalStatus | None
    new_status: ApprovalStatus
    prior_version: int
    new_version: int
    actor: str
    reason: str
    occurred_at: str


def _summary_text(value: object) -> str:
    if not isinstance(value, str):
        raise ApprovalCommandValidationError("Approval summary is invalid")
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not 1 <= len(text) <= 1000 or len(text.encode()) > 4000:
        raise ApprovalCommandValidationError("Approval summary is invalid")
    if any(ord(character) < 32 and character not in {"\n", "\t"} for character in text):
        raise ApprovalCommandValidationError("Approval summary is invalid")
    return text


def _text(value: object, maximum: int, message: str) -> str:
    if not isinstance(value, str):
        raise ApprovalCommandValidationError(message)
    clean = value.strip()
    if not 1 <= len(clean) <= maximum:
        raise ApprovalCommandValidationError(message)
    return clean


def _version(value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ApprovalCommandValidationError("Expected version is invalid")


def _hash(value: object, message: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ApprovalCommandValidationError(message)
