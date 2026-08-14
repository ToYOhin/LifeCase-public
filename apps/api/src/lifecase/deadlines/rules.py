"""Closed deterministic Deadline calculation registry."""

from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

from .models import (
    DeadlineCommandValidationError,
    DeadlineDate,
    DeadlineDateKind,
    DeadlineRuleParameters,
)

CALENDAR_DAYS_AFTER_BASIS = "calendar_days_after_basis"


class DeadlineRuleError(RuntimeError):
    """Base class for safe deterministic rule failures."""


class DeadlineRuleNotFoundError(DeadlineRuleError):
    """Raised for an unknown rule ID or version."""


class DeadlineRuleInputError(DeadlineRuleError):
    """Raised when a registered rule receives invalid finite inputs."""


@dataclass(frozen=True, slots=True)
class DeadlineRuleResult:
    due: DeadlineDate
    parameters: DeadlineRuleParameters


def calculate_deadline(
    *,
    rule_id: str,
    rule_version: int,
    bases: tuple[DeadlineDate, ...],
    timezone: str,
    parameters: DeadlineRuleParameters,
) -> DeadlineRuleResult:
    """Execute one reviewed rule without a model or wall-clock dependency."""

    if (rule_id, rule_version) != (CALENDAR_DAYS_AFTER_BASIS, 1):
        raise DeadlineRuleNotFoundError("Deadline rule is not registered")
    if len(bases) != 1:
        raise DeadlineRuleInputError("Deadline rule basis is invalid")
    values = parameters.to_dict()
    if set(values) != {"days"}:
        raise DeadlineRuleInputError("Deadline rule parameters are invalid")
    days = values["days"]
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 366:
        raise DeadlineRuleInputError("Deadline rule parameters are invalid")
    basis = bases[0]
    try:
        if basis.kind is DeadlineDateKind.LOCAL_DATE:
            due = DeadlineDate(
                kind=DeadlineDateKind.LOCAL_DATE,
                value=(basis.as_date() + timedelta(days=days)).isoformat(),
                timezone=timezone,
            )
        else:
            local = basis.as_datetime().astimezone(ZoneInfo(timezone))
            target = local.replace(tzinfo=None) + timedelta(days=days)
            target_text = target.isoformat(timespec="microseconds")
            try:
                due = DeadlineDate.utc_from_local(
                    local_datetime=target_text,
                    timezone=timezone,
                )
            except DeadlineCommandValidationError:
                # A repeated target wall time needs a deterministic side of the overlap.
                # Reusing the basis fold preserves the reviewed v1 rule semantics; gaps
                # and every other invalid target still fail on this second resolution.
                due = DeadlineDate.utc_from_local(
                    local_datetime=target_text,
                    timezone=timezone,
                    fold=local.fold,
                )
    except DeadlineCommandValidationError as error:
        raise DeadlineRuleInputError("Deadline rule produced an invalid date") from error
    return DeadlineRuleResult(due=due, parameters=parameters)
