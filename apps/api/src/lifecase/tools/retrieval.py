"""Controlled read-only Agent tool over the authoritative Evidence Context service."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

from lifecase.evidence.context import (
    EvidenceCitation,
    EvidenceContext,
    EvidenceContextCaseNotFoundError,
    EvidenceContextConfigurationError,
    EvidenceContextError,
    EvidenceContextIntegrityError,
    EvidenceContextPersistenceError,
    EvidenceContextRequest,
    EvidenceContextRequestError,
    EvidenceContextService,
)
from lifecase.providers.models import canonical_json_object

EVIDENCE_CONTEXT_TOOL_NAME = "evidence_context.search.v1"
_MAX_TOOL_TIMEOUT_MS = 5_000


class ToolRisk(StrEnum):
    """Reviewed risk classes for internal Agent tools."""

    READ_ONLY = "A"


class ToolErrorCode(StrEnum):
    """Stable codes that never include evidence, SQL, or local paths."""

    INVALID_REQUEST = "tool_invalid_request"
    NOT_ALLOWED = "tool_not_allowed"
    CASE_NOT_FOUND = "tool_case_not_found"
    UNAVAILABLE = "tool_unavailable"
    INTEGRITY = "tool_integrity_failure"
    TIMEOUT = "tool_timeout"


class ToolError(RuntimeError):
    """Base error crossing the controlled tool boundary."""

    def __init__(
        self,
        message: str,
        *,
        code: ToolErrorCode,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ToolRequestError(ToolError):
    """Raised when a tool request violates stable input boundaries."""


class ToolNotAllowedError(ToolError):
    """Raised when the current stage has not explicitly allowed the tool."""


class ToolCaseNotFoundError(ToolError):
    """Raised when the explicitly scoped Case does not exist."""


class ToolUnavailableError(ToolError):
    """Raised when retrieval or SQLite cannot serve the read safely."""


class ToolIntegrityError(ToolError):
    """Raised when evidence authority or a citation fails closed."""


class ToolTimeoutError(ToolError):
    """Raised when the configured local read deadline is exceeded."""


@dataclass(frozen=True, slots=True)
class ReadOnlyToolBudget:
    """Per-stage local tool ceiling; M3-04 permits exactly one read call."""

    max_calls: int = 1
    timeout_ms: int = 2_000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_calls, int)
            or isinstance(self.max_calls, bool)
            or self.max_calls != 1
            or not isinstance(self.timeout_ms, int)
            or isinstance(self.timeout_ms, bool)
            or not 1 <= self.timeout_ms <= _MAX_TOOL_TIMEOUT_MS
        ):
            raise ValueError("Read-only tool budget is invalid")


@dataclass(frozen=True, slots=True)
class EvidenceContextToolRequest:
    """One explicitly named and budgeted Evidence Context tool call."""

    tool_name: str
    context_request: EvidenceContextRequest
    budget: ReadOnlyToolBudget

    def __post_init__(self) -> None:
        if self.tool_name != EVIDENCE_CONTEXT_TOOL_NAME:
            raise ValueError("Evidence Context tool name is invalid")
        if not isinstance(self.context_request, EvidenceContextRequest) or not isinstance(
            self.budget, ReadOnlyToolBudget
        ):
            raise ValueError("Evidence Context tool request is invalid")


@dataclass(frozen=True, slots=True)
class EvidenceContextToolResult:
    """Deterministic read result; the Context remains ephemeral and authoritative."""

    tool_name: str
    risk: ToolRisk
    call_count: int
    context: EvidenceContext
    context_sha256: str

    def __post_init__(self) -> None:
        if (
            self.tool_name != EVIDENCE_CONTEXT_TOOL_NAME
            or self.risk is not ToolRisk.READ_ONLY
            or self.call_count != 1
            or not isinstance(self.context, EvidenceContext)
            or self.context_sha256 != evidence_context_sha256(self.context)
        ):
            raise ValueError("Evidence Context tool result is invalid")

    def to_stage_payload(self) -> dict[str, object]:
        """Return the complete ephemeral provider input with stable citation order."""

        return evidence_context_payload(self.context)


class EvidenceContextReadTool:
    """Execute the sole M3-04 tool through the unchanged M2-04 service."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        allowed_tools: tuple[str, ...],
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._connection = connection
        self._allowed_tools = allowed_tools
        self._clock = clock or time.monotonic

    def execute(self, request: EvidenceContextToolRequest) -> EvidenceContextToolResult:
        """Perform one bounded read without mutating any authoritative domain row."""

        if not isinstance(request, EvidenceContextToolRequest):
            raise ToolRequestError(
                "Tool request is invalid",
                code=ToolErrorCode.INVALID_REQUEST,
            )
        if request.tool_name not in self._allowed_tools:
            raise ToolNotAllowedError(
                "Tool is not allowed for this stage",
                code=ToolErrorCode.NOT_ALLOWED,
            )
        deadline = self._clock() + request.budget.timeout_ms / 1_000
        original_busy_timeout: int | None = None

        def interrupt_when_expired() -> int:
            return int(self._clock() >= deadline)

        try:
            busy_timeout = self._connection.execute("PRAGMA busy_timeout").fetchone()
            if busy_timeout is not None:
                original_busy_timeout = int(busy_timeout[0])
                bounded_busy_timeout = min(
                    original_busy_timeout,
                    request.budget.timeout_ms,
                )
                self._connection.execute(f"PRAGMA busy_timeout = {bounded_busy_timeout}")
            self._connection.set_progress_handler(interrupt_when_expired, 1_000)
            context = EvidenceContextService(self._connection).build(request.context_request)
            if self._clock() >= deadline:
                raise ToolTimeoutError(
                    "Tool execution timed out",
                    code=ToolErrorCode.TIMEOUT,
                    retryable=True,
                )
        except ToolError:
            raise
        except EvidenceContextCaseNotFoundError as error:
            raise ToolCaseNotFoundError(
                "Tool Case was not found",
                code=ToolErrorCode.CASE_NOT_FOUND,
            ) from error
        except EvidenceContextRequestError as error:
            if self._clock() >= deadline:
                raise ToolTimeoutError(
                    "Tool execution timed out",
                    code=ToolErrorCode.TIMEOUT,
                    retryable=True,
                ) from error
            raise ToolRequestError(
                "Tool request is invalid",
                code=ToolErrorCode.INVALID_REQUEST,
            ) from error
        except EvidenceContextIntegrityError as error:
            raise ToolIntegrityError(
                "Tool evidence validation failed",
                code=ToolErrorCode.INTEGRITY,
            ) from error
        except (EvidenceContextConfigurationError, EvidenceContextPersistenceError) as error:
            if self._clock() >= deadline:
                raise ToolTimeoutError(
                    "Tool execution timed out",
                    code=ToolErrorCode.TIMEOUT,
                    retryable=True,
                ) from error
            raise ToolUnavailableError(
                "Tool is unavailable",
                code=ToolErrorCode.UNAVAILABLE,
                retryable=True,
            ) from error
        except (EvidenceContextError, sqlite3.Error, ValueError) as error:
            if self._clock() >= deadline:
                raise ToolTimeoutError(
                    "Tool execution timed out",
                    code=ToolErrorCode.TIMEOUT,
                    retryable=True,
                ) from error
            raise ToolUnavailableError(
                "Tool is unavailable",
                code=ToolErrorCode.UNAVAILABLE,
                retryable=True,
            ) from error
        finally:
            with suppress(sqlite3.Error):
                self._connection.set_progress_handler(None, 0)
            if original_busy_timeout is not None:
                with suppress(sqlite3.Error):
                    self._connection.execute(f"PRAGMA busy_timeout = {original_busy_timeout}")
        return EvidenceContextToolResult(
            tool_name=request.tool_name,
            risk=ToolRisk.READ_ONLY,
            call_count=1,
            context=context,
            context_sha256=evidence_context_sha256(context),
        )


def evidence_context_sha256(context: EvidenceContext) -> str:
    """Hash one complete Context projection using its stable provider representation."""

    payload_json = canonical_json_object(evidence_context_payload(context), maximum_bytes=200_000)
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def evidence_context_payload(context: EvidenceContext) -> dict[str, object]:
    """Project a complete ephemeral Context with deterministic citation ordering."""

    return {
        "case_id": context.case_id,
        "query": context.query,
        "used_text_bytes": context.used_text_bytes,
        "truncated": context.truncated,
        "matches": [
            {
                "rank": match.rank,
                "score": match.score,
                "citation": _citation_payload(match.citation),
            }
            for match in context.matches
        ],
        "items": [
            {
                "citation": _citation_payload(item.citation),
                "text": item.text,
                "origin": item.origin.value,
                "match_ranks": list(item.match_ranks),
                "text_bytes": item.text_bytes,
            }
            for item in context.items
        ],
    }


def _citation_payload(citation: object) -> dict[str, object]:
    if not isinstance(citation, EvidenceCitation):
        raise ValueError("Evidence citation is invalid")
    return {
        "case_id": citation.case_id,
        "source_id": citation.source_id,
        "segment_id": citation.segment_id,
        "sequence": citation.sequence,
        "page": citation.page,
        "text_sha256": citation.text_sha256,
    }
