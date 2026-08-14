"""Deterministic local calendar export values."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CalendarExport:
    filename: str
    content: bytes
    sha256: str
