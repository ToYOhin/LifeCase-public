"""Thin HTTP download for deterministic local ICS bytes."""

from typing import NoReturn

from fastapi import APIRouter
from fastapi.responses import Response

from lifecase.api.dependencies import DatabaseDependency
from lifecase.api.errors import ApiError
from lifecase.calendar_export import (
    CalendarExportCaseNotFoundError,
    CalendarExportDeadlineInvalidError,
    CalendarExportDeadlineNotFoundError,
    CalendarExportError,
    CalendarExportPersistenceError,
    CalendarExportService,
)
from lifecase.deadlines import DeadlineService

router = APIRouter()


@router.get("/cases/{case_id}/deadlines/{deadline_id}/calendar-export")
def export_calendar(
    case_id: str,
    deadline_id: str,
    database: DatabaseDependency,
) -> Response:
    try:
        exported = CalendarExportService(DeadlineService(database)).export(case_id, deadline_id)
    except CalendarExportError as error:
        _raise_calendar_error(error)
    return Response(
        content=exported.content,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{exported.filename}"',
            "X-Content-Type-Options": "nosniff",
            "X-LifeCase-Content-SHA256": exported.sha256,
        },
    )


def _raise_calendar_error(error: CalendarExportError) -> NoReturn:
    if isinstance(error, CalendarExportCaseNotFoundError):
        raise ApiError(
            status_code=404, code="case_not_found", message="Case was not found"
        ) from error
    if isinstance(error, CalendarExportDeadlineNotFoundError):
        raise ApiError(
            status_code=404,
            code="deadline_not_found",
            message="Deadline was not found",
        ) from error
    if isinstance(error, CalendarExportDeadlineInvalidError):
        raise ApiError(
            status_code=409,
            code="calendar_export_not_allowed",
            message="Deadline cannot be exported safely",
        ) from error
    if isinstance(error, CalendarExportPersistenceError):
        raise ApiError(
            status_code=503,
            code="calendar_export_unavailable",
            message="Calendar export could not be generated",
            retryable=True,
        ) from error
    raise ApiError(
        status_code=503,
        code="calendar_export_unavailable",
        message="Calendar export is unavailable",
    ) from error
