"""Pure deterministic ICS projection over confirmed Deadline authority."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from lifecase.deadlines import (
    DeadlineCaseNotFoundError,
    DeadlineDateKind,
    DeadlinePersistenceError,
    DeadlineRecord,
    DeadlineService,
    DeadlineServiceError,
    DeadlineStatus,
)

from .models import CalendarExport


class CalendarExportError(RuntimeError):
    pass


class CalendarExportCaseNotFoundError(CalendarExportError):
    pass


class CalendarExportDeadlineNotFoundError(CalendarExportError):
    pass


class CalendarExportDeadlineInvalidError(CalendarExportError):
    pass


class CalendarExportPersistenceError(CalendarExportError):
    pass


class CalendarExportService:
    """Render bytes only; never writes an Artifact or touches a calendar."""

    def __init__(self, deadline_service: DeadlineService) -> None:
        self._deadlines = deadline_service

    def export(self, case_id: str, deadline_id: str) -> CalendarExport:
        clean_case_id = _identifier(case_id, "Case ID is invalid")
        clean_deadline_id = _identifier(deadline_id, "Deadline ID is invalid")
        try:
            records = self._deadlines.list_case_deadlines(clean_case_id)
        except DeadlineCaseNotFoundError as error:
            raise CalendarExportCaseNotFoundError from error
        except DeadlinePersistenceError as error:
            raise CalendarExportPersistenceError(
                "Calendar export could not read Deadline"
            ) from error
        except DeadlineServiceError as error:
            raise CalendarExportDeadlineInvalidError(
                "Calendar export Deadline authority is invalid"
            ) from error
        deadline = next((item for item in records if item.id == clean_deadline_id), None)
        if deadline is None:
            raise CalendarExportDeadlineNotFoundError
        if deadline.status is not DeadlineStatus.CONFIRMED:
            raise CalendarExportDeadlineInvalidError("Deadline is not confirmed")
        content = _render_ics(deadline)
        digest = hashlib.sha256(content).hexdigest()
        filename_key = hashlib.sha256(deadline.id.encode()).hexdigest()[:16]
        return CalendarExport(
            filename=f"lifecase-deadline-{filename_key}.ics",
            content=content,
            sha256=digest,
        )


def _render_ics(deadline: DeadlineRecord) -> bytes:
    uid = hashlib.sha256(
        f"{deadline.case_id}\0{deadline.id}\0{deadline.definition_sha256}".encode()
    ).hexdigest()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LifeCase//Deadline Export M5-03//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}@lifecase.local",
        f"DTSTAMP:{_basic_utc(deadline.updated_at)}",
        _start_line(deadline),
        f"SUMMARY:LifeCase {deadline.kind.value} deadline",
        "DESCRIPTION:Confirmed local LifeCase Deadline authority. No external action was taken.",
        f"X-LIFECASE-DEADLINE-ID:{deadline.id}",
        f"X-LIFECASE-DEFINITION-SHA256:{deadline.definition_sha256}",
        f"X-LIFECASE-DATE-KIND:{deadline.due.kind.value}",
        f"X-LIFECASE-TIMEZONE:{deadline.due.timezone}",
        f"X-LIFECASE-UNCERTAINTY:{deadline.uncertainty.value}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    folded = [part for line in lines for part in _fold_ascii_line(line)]
    return ("\r\n".join(folded) + "\r\n").encode("utf-8")


def _start_line(deadline: DeadlineRecord) -> str:
    if deadline.due.kind is DeadlineDateKind.LOCAL_DATE:
        return f"DTSTART;VALUE=DATE:{deadline.due.value.replace('-', '')}"
    return f"DTSTART:{_basic_utc(deadline.due.value)}"


def _basic_utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise CalendarExportDeadlineInvalidError("Deadline timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CalendarExportDeadlineInvalidError("Deadline timestamp is invalid")
    return parsed.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _fold_ascii_line(value: str) -> tuple[str, ...]:
    try:
        value.encode("ascii")
    except UnicodeEncodeError as error:
        raise CalendarExportDeadlineInvalidError("Calendar field is invalid") from error
    if len(value) <= 75:
        return (value,)
    parts = [value[:75]]
    remainder = value[75:]
    while remainder:
        parts.append(f" {remainder[:74]}")
        remainder = remainder[74:]
    return tuple(parts)


def _identifier(value: object, message: str) -> str:
    if not isinstance(value, str):
        raise CalendarExportDeadlineInvalidError(message)
    clean = value.strip()
    if not 1 <= len(clean) <= 200:
        raise CalendarExportDeadlineInvalidError(message)
    return clean
