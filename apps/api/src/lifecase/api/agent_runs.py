"""Thin P1-01 HTTP contract for the Case-scoped Portfolio Agent Demo."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal, NoReturn, cast

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, JsonValue

from lifecase.agent import (
    AgentRunRecord,
    AgentRunStatus,
    AgentServiceError,
    AgentStageRecord,
)
from lifecase.agent.demo import (
    PORTFOLIO_AGENT_DEMO_VERSION,
    PortfolioAgentCaseUnsupportedError,
    PortfolioAgentDemoDisabledError,
    PortfolioAgentDemoError,
    PortfolioAgentDemoService,
    PortfolioAgentDemoState,
    PortfolioAgentRunExistsError,
    PortfolioAgentRunMissingError,
    PortfolioAgentRunModeError,
    PortfolioAgentRunNotExecutableError,
)
from lifecase.agent.models import stage_order_for_run
from lifecase.api.dependencies import ConfigDependency, DatabaseDependency
from lifecase.api.errors import ApiError, ApiErrorResponse
from lifecase.cases import CaseNotFoundError, CaseServiceError
from lifecase.claims import ClaimServiceError
from lifecase.config import ApplicationConfig
from lifecase.tools import ToolError

router = APIRouter()

_AGENT_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ApiErrorResponse, "description": "Case or Agent run was not found."},
    409: {
        "model": ApiErrorResponse,
        "description": "Agent command conflicts with current authority.",
    },
    422: {
        "model": ApiErrorResponse,
        "description": "Agent request failed bounded validation.",
    },
    503: {
        "model": ApiErrorResponse,
        "description": "Agent run dependency is safely unavailable.",
    },
}


class AgentDemoCapabilityResponse(BaseModel):
    status: Literal["enabled", "disabled"]
    provider: Literal["deterministic_fake", "disabled"]
    action: str
    supported_case_types: list[Literal["warranty"]]


class AgentUsageResponse(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_minor: int
    cost_currency: Literal["CNY"]


class AgentBudgetResponse(BaseModel):
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_total_tokens: int
    max_cost_minor: int
    cost_currency: Literal["CNY"]


class AgentStageTraceResponse(BaseModel):
    name: str
    status: str
    version: int
    calls: int
    input_tokens: int
    output_tokens: int
    cost_minor: int
    provider: str | None
    model_id: str | None
    error_code: str | None
    retryable: bool | None
    summary: str | None
    updated_at: str


class AgentClaimResponse(BaseModel):
    id: str
    field: str
    value: JsonValue
    status: str
    confidence: float | None
    created_by: Literal["agent"]
    version: int


class AgentAnswerResponse(BaseModel):
    status: Literal["answered", "unknown"]
    statements: list[str]
    citations: list[dict[str, JsonValue]]
    uncertainty_reason: str | None
    fact_references: list[dict[str, JsonValue]] = Field(default_factory=list)


class AgentAnalysisResponse(BaseModel):
    question: str
    basis_sha256: str
    basis_status: Literal["current", "changed", "unavailable"]
    facts: list[dict[str, JsonValue]]


class AgentToolCallResponse(BaseModel):
    name: Literal["evidence_context.search.v1"]
    risk: Literal["A"]
    call_count: Literal[1]
    result: Literal["match", "no_match"]


class AgentRunResponse(BaseModel):
    id: str
    case_id: str
    status: str
    version: int
    stage_order: list[str]
    current_stage: str | None
    last_completed_stage: str | None
    cancellation_requested: bool
    can_execute: bool
    can_retry: bool
    recovery_required: bool
    provider: str | None
    model_id: str | None
    budget: AgentBudgetResponse
    usage: AgentUsageResponse
    stages: list[AgentStageTraceResponse]
    claims: list[AgentClaimResponse]
    answer: AgentAnswerResponse | None
    analysis: AgentAnalysisResponse | None = None
    tool_calls: list[AgentToolCallResponse]
    created_at: str
    updated_at: str
    completed_at: str | None


class AgentDemoResponse(BaseModel):
    demo_version: Literal["portfolio_agent_demo.v1"]
    capability: AgentDemoCapabilityResponse
    run: AgentRunResponse | None


@router.get(
    "/cases/{case_id}/agent-run",
    response_model=AgentDemoResponse,
    responses=_AGENT_ERROR_RESPONSES,
)
def get_agent_run(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
) -> AgentDemoResponse:
    """Read the current safe run projection or explicit disabled capability."""

    try:
        state = PortfolioAgentDemoService(
            database, enabled=config.portfolio_agent_demo_enabled
        ).get_state(case_id)
    except (
        PortfolioAgentDemoError,
        CaseServiceError,
        ClaimServiceError,
        AgentServiceError,
        ToolError,
        ValueError,
    ) as error:
        _raise_agent_api_error(error)
    return _response(state)


@router.post(
    "/cases/{case_id}/agent-run",
    response_model=AgentDemoResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_AGENT_ERROR_RESPONSES,
)
def create_agent_run(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
) -> AgentDemoResponse:
    """Create the single queued deterministic demo run for a Case."""

    try:
        state = _service(database, config).create(case_id)
    except (
        PortfolioAgentDemoError,
        CaseServiceError,
        ClaimServiceError,
        AgentServiceError,
        ToolError,
        ValueError,
    ) as error:
        _raise_agent_api_error(error)
    return _response(state)


@router.post(
    "/cases/{case_id}/agent-run/execute",
    response_model=AgentDemoResponse,
    responses=_AGENT_ERROR_RESPONSES,
)
async def execute_agent_run(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
) -> AgentDemoResponse:
    """Execute all currently eligible fixed stages without external side effects."""

    try:
        state = await _service(database, config).execute(case_id)
    except (
        PortfolioAgentDemoError,
        CaseServiceError,
        ClaimServiceError,
        AgentServiceError,
        ToolError,
        ValueError,
    ) as error:
        _raise_agent_api_error(error)
    return _response(state)


@router.post(
    "/cases/{case_id}/agent-run/retry",
    response_model=AgentDemoResponse,
    responses=_AGENT_ERROR_RESPONSES,
)
async def retry_agent_run(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
) -> AgentDemoResponse:
    """Retry the one persisted retryable stage using its unchanged authority scope."""

    try:
        state = await _service(database, config).execute(case_id, retry_only=True)
    except (
        PortfolioAgentDemoError,
        CaseServiceError,
        ClaimServiceError,
        AgentServiceError,
        ToolError,
        ValueError,
    ) as error:
        _raise_agent_api_error(error)
    return _response(state)


@router.post(
    "/cases/{case_id}/agent-run/cancel",
    response_model=AgentDemoResponse,
    responses=_AGENT_ERROR_RESPONSES,
)
def cancel_agent_run(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
) -> AgentDemoResponse:
    """Cancel one unfinished run using its current optimistic version."""

    try:
        state = _service(database, config).cancel(case_id)
    except (
        PortfolioAgentDemoError,
        CaseServiceError,
        ClaimServiceError,
        AgentServiceError,
        ToolError,
        ValueError,
    ) as error:
        _raise_agent_api_error(error)
    return _response(state)


@router.post(
    "/cases/{case_id}/agent-run/recover",
    response_model=AgentDemoResponse,
    responses=_AGENT_ERROR_RESPONSES,
)
def recover_agent_run(
    case_id: str,
    database: DatabaseDependency,
    config: ConfigDependency,
) -> AgentDemoResponse:
    """Reconcile a process-interrupted reservation before an explicit retry."""

    try:
        state = _service(database, config).recover(case_id)
    except (
        PortfolioAgentDemoError,
        CaseServiceError,
        ClaimServiceError,
        AgentServiceError,
        ToolError,
        ValueError,
    ) as error:
        _raise_agent_api_error(error)
    return _response(state)


def _service(
    database: sqlite3.Connection,
    config: ApplicationConfig,
) -> PortfolioAgentDemoService:
    return PortfolioAgentDemoService(database, enabled=config.portfolio_agent_demo_enabled)


def _response(state: PortfolioAgentDemoState) -> AgentDemoResponse:
    capability = AgentDemoCapabilityResponse(
        status="enabled" if state.enabled else "disabled",
        provider="deterministic_fake" if state.enabled else "disabled",
        action=(
            "Start or resume the Case-scoped deterministic demo."
            if state.enabled
            else "Enable the deterministic Portfolio Agent Demo explicitly."
        ),
        supported_case_types=["warranty"],
    )
    return AgentDemoResponse(
        demo_version=cast(Literal["portfolio_agent_demo.v1"], PORTFOLIO_AGENT_DEMO_VERSION),
        capability=capability,
        run=None if state.run is None else agent_run_response(state),
    )


def agent_run_response(state: PortfolioAgentDemoState) -> AgentRunResponse:
    """Project durable Agent authority without prompts or checkpoint bodies."""

    run = state.run
    if run is None:
        raise RuntimeError("Agent response requires a run")
    identities = [
        (stage.provider, stage.model_id)
        for stage in run.stages
        if stage.provider is not None and stage.model_id is not None
    ]
    provider, model_id = (None, None) if not identities else identities[-1]
    return AgentRunResponse(
        id=run.id,
        case_id=run.case_id,
        status=run.status.value,
        version=run.version,
        stage_order=[stage.value for stage in stage_order_for_run(run.id)],
        current_stage=None if run.current_stage is None else run.current_stage.value,
        last_completed_stage=(
            None if run.last_completed_stage is None else run.last_completed_stage.value
        ),
        cancellation_requested=run.cancellation_requested,
        can_execute=(
            run.status in {AgentRunStatus.QUEUED, AgentRunStatus.CHECKPOINTED}
            and (state.analysis is None or state.analysis["basis_status"] == "current")
        ),
        can_retry=(
            _can_retry(run)
            and (state.analysis is None or state.analysis["basis_status"] == "current")
        ),
        analysis=(
            None if state.analysis is None else AgentAnalysisResponse.model_validate(state.analysis)
        ),
        recovery_required=run.status is AgentRunStatus.RUNNING,
        provider=("deterministic_fake" if provider == "fake" else provider),
        model_id=model_id,
        budget=AgentBudgetResponse(
            max_calls=run.budget.max_calls,
            max_input_tokens=run.budget.max_input_tokens,
            max_output_tokens=run.budget.max_output_tokens,
            max_total_tokens=run.budget.max_total_tokens,
            max_cost_minor=run.budget.max_cost_minor,
            cost_currency="CNY",
        ),
        usage=_usage_response(run),
        stages=[
            AgentStageTraceResponse(
                name=stage.stage.value,
                status=stage.status.value,
                version=stage.version,
                calls=stage.usage.calls,
                input_tokens=stage.usage.input_tokens,
                output_tokens=stage.usage.output_tokens,
                cost_minor=stage.usage.cost_minor,
                provider=("deterministic_fake" if stage.provider == "fake" else stage.provider),
                model_id=stage.model_id,
                error_code=stage.error_code,
                retryable=stage.retryable,
                summary=_stage_summary(stage),
                updated_at=stage.updated_at,
            )
            for stage in run.stages
        ],
        claims=[AgentClaimResponse.model_validate(claim) for claim in state.claims],
        answer=(None if state.answer is None else AgentAnswerResponse.model_validate(state.answer)),
        tool_calls=[AgentToolCallResponse.model_validate(item) for item in state.tool_calls],
        created_at=run.created_at,
        updated_at=run.updated_at,
        completed_at=run.completed_at,
    )


def _usage_response(run: AgentRunRecord) -> AgentUsageResponse:
    return AgentUsageResponse(
        calls=run.usage.calls,
        input_tokens=run.usage.input_tokens,
        output_tokens=run.usage.output_tokens,
        total_tokens=run.usage.total_tokens,
        cost_minor=run.usage.cost_minor,
        cost_currency="CNY",
    )


def _stage_summary(stage: AgentStageRecord) -> str | None:
    if stage.checkpoint is None:
        return None
    payload = json.loads(stage.checkpoint.payload_json)
    if stage.stage.value == "extract":
        origins = [fact["origin"] for fact in payload["facts"]]
        return (
            f"candidate={origins.count('extracted')}; "
            f"inferred={origins.count('inferred')}; "
            f"unknown={origins.count('unknown')}"
        )
    if stage.stage.value == "validate":
        return f"RAG decision: {payload['status']}"
    summary = payload.get("summary")
    return summary if isinstance(summary, str) else None


def _can_retry(run: AgentRunRecord) -> bool:
    if run.status is not AgentRunStatus.NEEDS_REVIEW or not run.stages:
        return False
    stage = run.stages[-1]
    return stage.retryable is True and stage.usage.calls < stage.budget.max_calls


def _raise_agent_api_error(error: Exception) -> NoReturn:
    if isinstance(error, PortfolioAgentDemoDisabledError):
        raise ApiError(
            status_code=409,
            code="agent_demo_disabled",
            message="Portfolio Agent Demo is disabled",
            details={"action": "Enable the deterministic demo explicitly."},
        ) from error
    if isinstance(error, PortfolioAgentRunExistsError):
        raise ApiError(
            status_code=409,
            code="agent_run_exists",
            message="Case already has an Agent run",
        ) from error
    if isinstance(error, PortfolioAgentRunMissingError):
        raise ApiError(
            status_code=404,
            code="agent_run_not_found",
            message="Agent run was not found",
        ) from error
    if isinstance(error, PortfolioAgentRunNotExecutableError):
        raise ApiError(
            status_code=409,
            code="agent_run_not_executable",
            message="Agent run cannot perform this command",
        ) from error
    if isinstance(error, PortfolioAgentRunModeError):
        raise ApiError(
            status_code=409,
            code="agent_run_mode_conflict",
            message="Case Agent run belongs to another product mode",
        ) from error
    if isinstance(error, PortfolioAgentCaseUnsupportedError):
        raise ApiError(
            status_code=409,
            code="agent_demo_case_unsupported",
            message="Portfolio Agent Demo does not support this Case type",
            details={"supported_case_types": ["warranty"]},
        ) from error
    if isinstance(error, CaseNotFoundError):
        raise ApiError(
            status_code=404,
            code="case_not_found",
            message="Case was not found",
        ) from error
    if isinstance(error, AgentServiceError):
        status_code = (
            409
            if error.code.value
            in {
                "agent_version_conflict",
                "agent_invalid_sequence",
                "agent_integrity_failure",
                "agent_budget_exhausted",
            }
            else 503
        )
        raise ApiError(
            status_code=status_code,
            code=error.code.value,
            message="Agent run stopped safely",
            retryable=error.retryable,
        ) from error
    if isinstance(error, ValueError):
        raise ApiError(
            status_code=422,
            code="agent_run_invalid",
            message="Agent run request is invalid",
        ) from error
    raise ApiError(
        status_code=503,
        code="agent_run_unavailable",
        message="Agent run is unavailable",
        retryable=True,
    ) from error
