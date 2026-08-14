"""Immutable Deadline domain values and command contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RULE_ID = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")


class DeadlineCommandValidationError(ValueError):
    """Raised when a Deadline value or command violates its boundary."""


class DeadlineKind(StrEnum):
    EXPLICIT = "explicit"
    CALCULATED = "calculated"
    SUGGESTED = "suggested"


class DeadlineDateKind(StrEnum):
    UTC_INSTANT = "utc_instant"
    LOCAL_DATE = "local_date"


class DeadlineStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    PASSED = "passed"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


class DeadlineEventType(StrEnum):
    CREATED = "created"
    CONFIRMED = "confirmed"
    PASSED = "passed"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


class DeadlineUncertainty(StrEnum):
    EVIDENCE_BACKED = "evidence_backed_date"
    RULE_DERIVED = "deterministic_rule_result_requires_confirmation"
    PERSONAL_PREFERENCE = "personal_follow_up_preference_not_external_obligation"


def validate_timezone(value: object) -> str:
    timezone = _bounded_text(value, 100, "Deadline timezone is invalid")
    try:
        ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise DeadlineCommandValidationError("Deadline timezone is invalid") from error
    return timezone


@dataclass(frozen=True, slots=True)
class DeadlineDate:
    """Canonical UTC instant or calendar-only local date plus IANA timezone."""

    kind: DeadlineDateKind
    value: str
    timezone: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DeadlineDateKind):
            raise DeadlineCommandValidationError("Deadline date kind is invalid")
        timezone = validate_timezone(self.timezone)
        canonical = _canonical_date_value(self.kind, self.value)
        object.__setattr__(self, "timezone", timezone)
        object.__setattr__(self, "value", canonical)

    @classmethod
    def from_storage(cls, *, kind: DeadlineDateKind, value: str, timezone: str) -> DeadlineDate:
        result = cls(kind=kind, value=value, timezone=timezone)
        if result.value != value or result.timezone != timezone:
            raise DeadlineCommandValidationError("Stored Deadline date is not canonical")
        return result

    @classmethod
    def utc_from_local(
        cls,
        *,
        local_datetime: str,
        timezone: str,
        fold: int | None = None,
    ) -> DeadlineDate:
        """Resolve a wall time explicitly; reject gaps and unresolved overlaps."""

        zone_name = validate_timezone(timezone)
        try:
            naive = datetime.fromisoformat(local_datetime)
        except (TypeError, ValueError) as error:
            raise DeadlineCommandValidationError("Local datetime is invalid") from error
        if naive.tzinfo is not None or naive.isoformat(timespec="microseconds") != local_datetime:
            raise DeadlineCommandValidationError("Local datetime is invalid")
        zone = ZoneInfo(zone_name)
        candidates: list[datetime] = []
        for candidate_fold in (0, 1):
            aware = naive.replace(tzinfo=zone, fold=candidate_fold)
            round_trip = aware.astimezone(UTC).astimezone(zone)
            if round_trip.replace(tzinfo=None) == naive and round_trip.fold == candidate_fold:
                candidates.append(aware)
        unique = {candidate.utcoffset() for candidate in candidates}
        if not candidates:
            raise DeadlineCommandValidationError("Local datetime does not exist")
        if len(unique) > 1 and fold is None:
            raise DeadlineCommandValidationError("Local datetime is ambiguous")
        selected_fold = 0 if fold is None else fold
        if selected_fold not in (0, 1):
            raise DeadlineCommandValidationError("Local datetime fold is invalid")
        selected = next((item for item in candidates if item.fold == selected_fold), None)
        if selected is None:
            raise DeadlineCommandValidationError("Local datetime fold is invalid")
        return cls(
            kind=DeadlineDateKind.UTC_INSTANT,
            value=selected.astimezone(UTC).isoformat(timespec="microseconds"),
            timezone=zone_name,
        )

    def as_datetime(self) -> datetime:
        if self.kind is not DeadlineDateKind.UTC_INSTANT:
            raise DeadlineCommandValidationError("Deadline is not a UTC instant")
        return datetime.fromisoformat(self.value.replace("Z", "+00:00"))

    def as_date(self) -> date:
        if self.kind is not DeadlineDateKind.LOCAL_DATE:
            raise DeadlineCommandValidationError("Deadline is not a local date")
        return date.fromisoformat(self.value)


@dataclass(frozen=True, slots=True)
class DeadlineRuleParameters:
    json_text: str
    sha256: str

    def __post_init__(self) -> None:
        try:
            value = json.loads(self.json_text)
        except (TypeError, json.JSONDecodeError) as error:
            raise DeadlineCommandValidationError("Deadline rule parameters are invalid") from error
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if (
            not isinstance(value, dict)
            or canonical != self.json_text
            or len(canonical) > 512
            or hashlib.sha256(canonical.encode()).hexdigest() != self.sha256
        ):
            raise DeadlineCommandValidationError("Deadline rule parameters are invalid")

    @classmethod
    def from_input(cls, value: object) -> DeadlineRuleParameters:
        if not isinstance(value, dict):
            raise DeadlineCommandValidationError("Deadline rule parameters are invalid")
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return cls(canonical, hashlib.sha256(canonical.encode()).hexdigest())

    def to_dict(self) -> dict[str, object]:
        value = json.loads(self.json_text)
        if not isinstance(value, dict):
            raise DeadlineCommandValidationError("Deadline rule parameters are invalid")
        return value


@dataclass(frozen=True, slots=True)
class DeadlineBasisInput:
    claim_id: str
    expected_claim_version: int
    value_sha256: str
    date_kind: DeadlineDateKind

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "claim_id", _bounded_text(self.claim_id, 200, "Basis Claim is invalid")
        )
        _positive_version(self.expected_claim_version)
        if not isinstance(self.value_sha256, str) or _SHA256.fullmatch(self.value_sha256) is None:
            raise DeadlineCommandValidationError("Basis Claim is invalid")
        if not isinstance(self.date_kind, DeadlineDateKind):
            raise DeadlineCommandValidationError("Basis Claim date kind is invalid")


@dataclass(frozen=True, slots=True)
class CreateExplicitDeadlineCommand:
    case_id: str
    basis: DeadlineBasisInput
    timezone: str
    actor: str
    reason: str

    def __post_init__(self) -> None:
        _normalize_common(self)
        if not isinstance(self.basis, DeadlineBasisInput):
            raise DeadlineCommandValidationError("Basis Claim is invalid")
        object.__setattr__(self, "timezone", validate_timezone(self.timezone))


@dataclass(frozen=True, slots=True)
class CreateCalculatedDeadlineCommand:
    case_id: str
    bases: tuple[DeadlineBasisInput, ...]
    timezone: str
    rule_id: str
    rule_version: int
    rule_parameters: DeadlineRuleParameters
    actor: str
    reason: str

    def __post_init__(self) -> None:
        _normalize_common(self)
        if not isinstance(self.bases, tuple) or not self.bases or len(self.bases) > 10:
            raise DeadlineCommandValidationError("Basis Claims are invalid")
        if not all(isinstance(item, DeadlineBasisInput) for item in self.bases):
            raise DeadlineCommandValidationError("Basis Claims are invalid")
        if len({item.claim_id for item in self.bases}) != len(self.bases):
            raise DeadlineCommandValidationError("Basis Claims are invalid")
        object.__setattr__(self, "timezone", validate_timezone(self.timezone))
        rule_id = _bounded_text(self.rule_id, 64, "Deadline rule is invalid")
        if _RULE_ID.fullmatch(rule_id) is None:
            raise DeadlineCommandValidationError("Deadline rule is invalid")
        object.__setattr__(self, "rule_id", rule_id)
        _positive_version(self.rule_version)
        if not isinstance(self.rule_parameters, DeadlineRuleParameters):
            raise DeadlineCommandValidationError("Deadline rule parameters are invalid")


@dataclass(frozen=True, slots=True)
class CreateSuggestedDeadlineCommand:
    case_id: str
    due: DeadlineDate
    actor: str
    reason: str

    def __post_init__(self) -> None:
        _normalize_common(self)
        if not isinstance(self.due, DeadlineDate):
            raise DeadlineCommandValidationError("Suggested Deadline date is invalid")


@dataclass(frozen=True, slots=True)
class DeadlineStatusCommand:
    deadline_id: str
    expected_version: int
    actor: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "deadline_id", _bounded_text(self.deadline_id, 200, "Deadline ID is invalid")
        )
        _positive_version(self.expected_version)
        object.__setattr__(
            self, "actor", _bounded_text(self.actor, 120, "Deadline metadata is invalid")
        )
        object.__setattr__(
            self, "reason", _bounded_text(self.reason, 1000, "Deadline metadata is invalid")
        )


@dataclass(frozen=True, slots=True)
class DeadlineBasis:
    claim_id: str
    claim_version: int
    value_sha256: str
    field: str
    date_kind: DeadlineDateKind


@dataclass(frozen=True, slots=True)
class DeadlineRecord:
    id: str
    case_id: str
    kind: DeadlineKind
    due: DeadlineDate
    status: DeadlineStatus
    uncertainty: DeadlineUncertainty
    confidence: float | None
    rule_id: str | None
    rule_version: int | None
    rule_parameters: DeadlineRuleParameters | None
    bases: tuple[DeadlineBasis, ...]
    version: int
    definition_sha256: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DeadlineEvent:
    id: str
    deadline_id: str
    case_id: str
    event_type: DeadlineEventType
    prior_status: DeadlineStatus | None
    new_status: DeadlineStatus
    prior_version: int
    new_version: int
    definition_sha256: str
    actor: str
    reason: str
    occurred_at: str


def _canonical_date_value(kind: DeadlineDateKind, value: object) -> str:
    if not isinstance(value, str):
        raise DeadlineCommandValidationError("Deadline date is invalid")
    try:
        if kind is DeadlineDateKind.LOCAL_DATE:
            parsed_date = date.fromisoformat(value)
            if parsed_date.isoformat() != value:
                raise ValueError
            return value
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError) as error:
        raise DeadlineCommandValidationError("Deadline date is invalid") from error


def _normalize_common(command: object) -> None:
    case_id = _bounded_text(getattr(command, "case_id", None), 200, "Case ID is invalid")
    actor = _bounded_text(getattr(command, "actor", None), 120, "Deadline metadata is invalid")
    reason = _bounded_text(getattr(command, "reason", None), 1000, "Deadline metadata is invalid")
    object.__setattr__(command, "case_id", case_id)
    object.__setattr__(command, "actor", actor)
    object.__setattr__(command, "reason", reason)


def _positive_version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DeadlineCommandValidationError("Expected version is invalid")
    return value


def _bounded_text(value: object, maximum: int, message: str) -> str:
    if not isinstance(value, str):
        raise DeadlineCommandValidationError(message)
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= maximum:
        raise DeadlineCommandValidationError(message)
    return cleaned
