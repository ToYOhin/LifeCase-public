"""Thin P1-04 HTTP contract for the read-only live Agent composition."""

from __future__ import annotations

from typing import Any, Literal, NoReturn, cast

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from lifecase.agent import AgentServiceError
from lifecase.agent.demo import (
    PortfolioAgentCaseUnsupportedError,
    PortfolioAgentDemoError,
    PortfolioAgentRunChangedError,
    PortfolioAgentRunExistsError,
    PortfolioAgentRunMissingError,
    PortfolioAgentRunModeError,
    PortfolioAgentRunNotExecutableError,
)
from lifecase.agent.live import (
    LIVE_AGENT_VERSION,
    LiveAgentClientUnavailableError,
    LiveAgentConfirmationRequiredError,
    LiveAgentDisabledError,
    LiveAgentError,
    LiveAgentPreview,
    LiveAgentPreviewStaleError,
    LiveAgentProviderUnavailableError,
    LiveAgentService,
    LiveAgentState,
)
from lifecase.api.agent_runs import (
    AgentAnswerResponse,
    AgentBudgetResponse,
    AgentRunResponse,
    agent_run_response,
)
from lifecase.api.dependencies import (
    ConfigDependency,
    DatabaseDependency,
    ProviderClientsDependency,
)
from lifecase.api.errors import ApiError, ApiErrorResponse
from lifecase.cases import CaseNotFoundError, CaseServiceError
from lifecase.claims import ClaimServiceError
from lifecase.providers import ModelProviderError
from lifecase.tools import ToolError

router = APIRouter()

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ApiErrorResponse, "description": "Case or live run was not found."},
    409: {"model": ApiErrorResponse, "description": "Live Agent authority conflict."},
    422: {"model": ApiErrorResponse, "description": "Bounded request validation failed."},
    503: {"model": ApiErrorResponse, "description": "Live Provider is unavailable."},
}
_HANDLED_ERRORS: tuple[type[Exception], ...] = (
    LiveAgentError,
    PortfolioAgentDemoError,
    CaseServiceError,
    ClaimServiceError,
    AgentServiceError,
    ModelProviderError,
    ToolError,
    ValueError,
)


class LiveAgentAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_read_only: bool
    question: str | None = Field(default=None, min_length=1, max_length=2000, strict=True)


class LiveAgentQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000, strict=True)


class LiveAgentCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_run_id: str = Field(min_length=1, max_length=200, strict=True)


class LiveAgentCapabilityResponse(BaseModel):
    status: Literal["disabled", "unavailable", "ready"]
    provider: Literal["disabled", "openai", "groq", "mistral", "deepseek", "gemini", "anthropic"]
    model_id: str | None
    action: str
    supported_case_types: list[Literal["warranty"]]


class LiveAgentEvidencePreviewResponse(BaseModel):
    segment_count: int
    utf8_bytes: int


class LiveAgentPreviewResponse(BaseModel):
    case_type: Literal["warranty"]
    evidence: LiveAgentEvidencePreviewResponse
    budget: AgentBudgetResponse
    read_only_notice: str
    preview_sha256: str
    question: str | None = None


class LiveAgentResponse(BaseModel):
    live_version: Literal["live_agent_read_only.v3"]
    capability: LiveAgentCapabilityResponse
    preview: LiveAgentPreviewResponse | None
    run: AgentRunResponse | None
    run_history: list[LiveAgentRunHistoryResponse]


class LiveAgentRunHistoryResponse(BaseModel):
    version: int
    id: str
    stage_order: list[str]
    status: str
    provider: str | None
    model_id: str | None
    answer_status: str | None
    calls: int
    total_tokens: int
    created_at: str
    completed_at: str | None
    question: str | None = None
    basis_status: Literal["current", "changed", "unavailable", "legacy"] = "legacy"
    answer: AgentAnswerResponse | None = None


@router.get(
    "/cases/{case_id}/live-agent",
    response_model=LiveAgentResponse,
    responses=_ERROR_RESPONSES,
)
def get_live_agent(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
    provider_clients: ProviderClientsDependency,
) -> LiveAgentResponse:
    """Preview the exact server-owned evidence and budget scope without a model call."""

    try:
        state = LiveAgentService(database, config, provider_clients).get_state(case_id)
    except _HANDLED_ERRORS as error:
        _raise_live_agent_api_error(error)
    return _response(state)


@router.post(
    "/cases/{case_id}/live-agent/preview",
    response_model=LiveAgentResponse,
    responses=_ERROR_RESPONSES,
)
def preview_live_agent_question(
    case_id: str,
    request: LiveAgentQuestionRequest,
    database: DatabaseDependency,
    config: ConfigDependency,
    provider_clients: ProviderClientsDependency,
) -> LiveAgentResponse:
    """Bind a read-only preview to this question and current Case authority."""
    try:
        state = LiveAgentService(database, config, provider_clients).get_state(
            case_id,
            question=request.question,
        )
    except _HANDLED_ERRORS as error:
        _raise_live_agent_api_error(error)
    return _response(state)


@router.post(
    "/cases/{case_id}/live-agent",
    response_model=LiveAgentResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERROR_RESPONSES,
)
def create_live_agent(
    case_id: str,
    authorization: LiveAgentAuthorizationRequest,
    database: DatabaseDependency,
    config: ConfigDependency,
    provider_clients: ProviderClientsDependency,
) -> LiveAgentResponse:
    """Persist one queued run bound to explicit consent for the current preview hash."""

    try:
        state = LiveAgentService(database, config, provider_clients).create(
            case_id,
            preview_sha256=authorization.preview_sha256,
            confirm_read_only=authorization.confirm_read_only,
            question=authorization.question,
        )
    except _HANDLED_ERRORS as error:
        _raise_live_agent_api_error(error)
    return _response(state)


@router.post(
    "/cases/{case_id}/live-agent/execute",
    response_model=LiveAgentResponse,
    responses=_ERROR_RESPONSES,
)
async def execute_live_agent(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
    provider_clients: ProviderClientsDependency,
    command: LiveAgentCommandRequest | None = None,
) -> LiveAgentResponse:
    """Execute the consent-bound two-stage run, or resume its legacy seven-stage history."""

    try:
        state = await LiveAgentService(database, config, provider_clients).execute(
            case_id,
            expected_run_id=None if command is None else command.expected_run_id,
        )
    except _HANDLED_ERRORS as error:
        _raise_live_agent_api_error(error)
    return _response(state)


@router.post(
    "/cases/{case_id}/live-agent/retry",
    response_model=LiveAgentResponse,
    responses=_ERROR_RESPONSES,
)
async def retry_live_agent(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
    provider_clients: ProviderClientsDependency,
    command: LiveAgentCommandRequest | None = None,
) -> LiveAgentResponse:
    """Retry the one persisted retryable stage within unchanged consent scope."""

    try:
        state = await LiveAgentService(database, config, provider_clients).execute(
            case_id,
            retry_only=True,
            expected_run_id=None if command is None else command.expected_run_id,
        )
    except _HANDLED_ERRORS as error:
        _raise_live_agent_api_error(error)
    return _response(state)


@router.post(
    "/cases/{case_id}/live-agent/cancel",
    response_model=LiveAgentResponse,
    responses=_ERROR_RESPONSES,
)
def cancel_live_agent(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
    provider_clients: ProviderClientsDependency,
    command: LiveAgentCommandRequest | None = None,
) -> LiveAgentResponse:
    """Cancel an unfinished live run without requiring a Provider call."""

    try:
        state = LiveAgentService(database, config, provider_clients).cancel(
            case_id,
            expected_run_id=None if command is None else command.expected_run_id,
        )
    except _HANDLED_ERRORS as error:
        _raise_live_agent_api_error(error)
    return _response(state)


@router.post(
    "/cases/{case_id}/live-agent/recover",
    response_model=LiveAgentResponse,
    responses=_ERROR_RESPONSES,
)
def recover_live_agent(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
    provider_clients: ProviderClientsDependency,
    command: LiveAgentCommandRequest | None = None,
) -> LiveAgentResponse:
    """Reconcile an interrupted reservation without exposing its private input."""

    try:
        state = LiveAgentService(database, config, provider_clients).recover(
            case_id,
            expected_run_id=None if command is None else command.expected_run_id,
        )
    except _HANDLED_ERRORS as error:
        _raise_live_agent_api_error(error)
    return _response(state)


def _response(state: LiveAgentState) -> LiveAgentResponse:
    return LiveAgentResponse(
        live_version=cast(Literal["live_agent_read_only.v3"], LIVE_AGENT_VERSION),
        capability=LiveAgentCapabilityResponse(
            status=state.capability_status,
            provider=state.provider,
            model_id=state.model_id,
            action=state.action,
            supported_case_types=["warranty"],
        ),
        preview=_preview_response(state.preview),
        run=(None if state.agent.run is None else agent_run_response(state.agent)),
        run_history=[
            LiveAgentRunHistoryResponse.model_validate(item) for item in state.agent.history
        ],
    )


def _preview_response(preview: LiveAgentPreview | None) -> LiveAgentPreviewResponse | None:
    if preview is None:
        return None
    budget = preview.budget
    return LiveAgentPreviewResponse(
        case_type=preview.case_type,
        evidence=LiveAgentEvidencePreviewResponse(
            segment_count=preview.evidence.segment_count,
            utf8_bytes=preview.evidence.utf8_bytes,
        ),
        budget=AgentBudgetResponse(
            max_calls=budget.max_calls,
            max_input_tokens=budget.max_input_tokens,
            max_output_tokens=budget.max_output_tokens,
            max_total_tokens=budget.max_total_tokens,
            max_cost_minor=budget.max_cost_minor,
            cost_currency="CNY",
        ),
        read_only_notice=preview.read_only_notice,
        preview_sha256=preview.preview_sha256,
        question=preview.question,
    )


def _raise_live_agent_api_error(error: Exception) -> NoReturn:
    if isinstance(error, PortfolioAgentRunChangedError):
        raise ApiError(
            status_code=409,
            code="live_agent_run_stale",
            message="The displayed Agent run has changed; read the record again",
        ) from error
    if isinstance(error, LiveAgentDisabledError):
        raise ApiError(
            status_code=409,
            code="live_agent_disabled",
            message="Live read-only Agent is disabled",
        ) from error
    if isinstance(error, LiveAgentClientUnavailableError):
        raise ApiError(
            status_code=503,
            code="live_agent_client_unavailable",
            message="Live Agent client is unavailable",
        ) from error
    if isinstance(error, LiveAgentProviderUnavailableError):
        raise ApiError(
            status_code=503,
            code="live_agent_provider_unavailable",
            message="Live Agent Provider is unavailable",
        ) from error
    if isinstance(error, LiveAgentConfirmationRequiredError):
        raise ApiError(
            status_code=409,
            code="live_agent_confirmation_required",
            message="Read-only authorization is required",
        ) from error
    if isinstance(error, LiveAgentPreviewStaleError):
        raise ApiError(
            status_code=409,
            code="live_agent_preview_stale",
            message="Live Agent preview changed; review it again",
        ) from error
    if isinstance(error, PortfolioAgentCaseUnsupportedError):
        raise ApiError(
            status_code=409,
            code="live_agent_case_unsupported",
            message="Live Agent supports reviewed warranty Cases only",
            details={"supported_case_types": ["warranty"]},
        ) from error
    if isinstance(error, PortfolioAgentRunModeError):
        raise ApiError(
            status_code=409,
            code="agent_run_mode_conflict",
            message="Case Agent run belongs to another product mode",
        ) from error
    if isinstance(error, PortfolioAgentRunExistsError):
        raise ApiError(status_code=409, code="agent_run_exists", message="Case has an Agent run")
    if isinstance(error, PortfolioAgentRunMissingError):
        raise ApiError(status_code=404, code="agent_run_not_found", message="Run not found")
    if isinstance(error, PortfolioAgentRunNotExecutableError):
        raise ApiError(
            status_code=409,
            code="agent_run_not_executable",
            message="Live Agent run cannot perform this command",
        ) from error
    if isinstance(error, CaseNotFoundError):
        raise ApiError(status_code=404, code="case_not_found", message="Case was not found")
    if isinstance(error, AgentServiceError):
        conflict_codes = {
            "agent_version_conflict",
            "agent_invalid_sequence",
            "agent_integrity_failure",
            "agent_budget_exhausted",
        }
        raise ApiError(
            status_code=409 if error.code.value in conflict_codes else 503,
            code=error.code.value,
            message="Live Agent stopped safely",
            retryable=error.retryable,
        ) from error
    if isinstance(
        error,
        (LiveAgentError, PortfolioAgentDemoError, CaseServiceError, ClaimServiceError, ToolError),
    ):
        raise ApiError(
            status_code=503,
            code="live_agent_unavailable",
            message="Live Agent is unavailable",
        ) from error
    if isinstance(error, ValueError):
        raise ApiError(
            status_code=422,
            code="live_agent_invalid",
            message="Live Agent request is invalid",
        ) from error
    raise ApiError(
        status_code=503,
        code="live_agent_unavailable",
        message="Live Agent is unavailable",
    ) from error
