"""P1-04 explicit, Case-scoped, read-only live Provider composition."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from lifecase.agent.demo import (
    PortfolioAgentCaseUnsupportedError,
    PortfolioAgentDemoService,
    PortfolioAgentDemoState,
    PortfolioAgentRunMissingError,
    _retrieval_query,
)
from lifecase.agent.errors import AgentIntegrityError, AgentValidationError
from lifecase.agent.models import (
    LIVE_TWO_STAGE_RUN_PREFIX,
    AgentBudget,
    CallReservation,
    StageBudget,
)
from lifecase.agent.question_binding import QUESTION_RUN_PREFIX, validate_question_binding
from lifecase.agent.question_context import basis_sha256, question_basis, require_current_binding
from lifecase.cases import CaseService
from lifecase.config import ApplicationConfig
from lifecase.providers import (
    ModelProviderKind,
    ProviderConfigurationError,
    ProviderProfile,
    ProviderRegistry,
    ProviderRuntimeClients,
    canonical_json_object,
)

LIVE_AGENT_VERSION = "live_agent_read_only.v3"
LIVE_AGENT_RUN_PREFIX = "portfolio-live-"
READ_ONLY_NOTICE = (
    "This run may create candidate or inferred facts and a read-only answer only; "
    "it cannot confirm Claims or perform external actions."
)
QUESTION_READ_ONLY_NOTICE = (
    "This run only analyzes the bound question using current facts and Case evidence; "
    "it cannot create or confirm Claims or perform external actions."
)
_AGENT_BUDGET = AgentBudget()
_STAGE_BUDGET = StageBudget(
    max_calls=2,
    max_input_tokens=4_000,
    max_output_tokens=2_000,
    max_cost_minor=14,
)
_CALL_RESERVATION = CallReservation(
    input_tokens=2_000,
    output_tokens=1_000,
    cost_minor=7,
)
_DEEPSEEK_AGENT_BUDGET = AgentBudget(
    max_input_tokens=56_000,
    max_output_tokens=56_000,
)
_DEEPSEEK_STAGE_BUDGET = StageBudget(
    max_calls=2,
    max_input_tokens=8_000,
    max_output_tokens=8_000,
    max_cost_minor=14,
)
_DEEPSEEK_CALL_RESERVATION = CallReservation(
    input_tokens=4_000,
    output_tokens=4_000,
    cost_minor=7,
)


class LiveAgentError(RuntimeError):
    """Safe product-level live Agent failure."""


class LiveAgentDisabledError(LiveAgentError):
    """Raised when the server opt-in is absent."""


class LiveAgentClientUnavailableError(LiveAgentError):
    """Raised when the process-local credentialed client is absent."""


class LiveAgentProviderUnavailableError(LiveAgentError):
    """Raised when the selected server Provider/profile is not live-safe."""


class LiveAgentConfirmationRequiredError(LiveAgentError):
    """Raised when the user did not affirm the read-only boundary."""


class LiveAgentPreviewStaleError(LiveAgentError):
    """Raised when the server-owned preview scope changed before authorization."""


@dataclass(frozen=True, slots=True)
class LiveAgentEvidencePreview:
    segment_count: int
    utf8_bytes: int


@dataclass(frozen=True, slots=True)
class LiveAgentPreview:
    case_type: Literal["warranty"]
    evidence: LiveAgentEvidencePreview
    budget: AgentBudget
    read_only_notice: str
    preview_sha256: str
    question: str | None = None
    input_binding_json: str | None = None


@dataclass(frozen=True, slots=True)
class LiveAgentState:
    capability_status: Literal["disabled", "unavailable", "ready"]
    provider: Literal["disabled", "openai", "groq", "mistral", "deepseek", "gemini", "anthropic"]
    model_id: str | None
    action: str
    preview: LiveAgentPreview | None
    agent: PortfolioAgentDemoState


class LiveAgentService:
    """Bind server configuration, preview consent, and the existing StageRunner."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        config: ApplicationConfig,
        clients: ProviderRuntimeClients,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._connection = connection
        self._config = config
        self._clients = clients
        self._registry = registry or ProviderRegistry()

    def get_state(self, case_id: str, *, question: str | None = None) -> LiveAgentState:
        agent = self._demo_service().get_state(case_id)
        # Reading durable question history must not require a new authorization
        # preview or readable current materials. POST preview remains explicit.
        preview = (
            None
            if question is None and agent.analysis is not None
            else self.preview(case_id, question=question)
        )
        status, provider, model_id, action = self._capability()
        return LiveAgentState(
            capability_status=status,
            provider=provider,
            model_id=model_id,
            action=action,
            preview=preview,
            agent=agent,
        )

    def preview(
        self, case_id: str, *, legacy: bool = False, question: str | None = None
    ) -> LiveAgentPreview:
        case = CaseService(self._connection).get_case(case_id)
        if case.type != "warranty":
            raise PortfolioAgentCaseUnsupportedError(
                "Live Agent supports reviewed warranty Cases only"
            )
        if question is not None:
            return self._question_preview(case_id, question)
        scope = self._demo_service().evidence_scope(case_id)
        evidence = scope.unique_segments
        evidence_preview = LiveAgentEvidencePreview(
            segment_count=len(evidence),
            utf8_bytes=sum(len(item.text.encode("utf-8")) for item in evidence),
        )
        budget = self._agent_budget()
        scope_json = canonical_json_object(
            {
                "version": "live_agent_read_only.v1" if legacy else "live_agent_read_only.v2",
                "case_id": case_id,
                "case_type": case.type,
                "provider": self._configured_provider(),
                "model_id": self._configured_model_id(),
                "evidence": {
                    "extraction": [
                        {"segment_id": item.segment_id, "text_sha256": item.text_sha256}
                        for item in scope.extraction
                    ],
                    "validation": [
                        {"segment_id": item.segment_id, "text_sha256": item.text_sha256}
                        for item in scope.validation
                    ],
                },
                "budget": {
                    "max_calls": budget.max_calls,
                    "max_input_tokens": budget.max_input_tokens,
                    "max_output_tokens": budget.max_output_tokens,
                    "max_cost_minor": budget.max_cost_minor,
                    "cost_currency": budget.cost_currency,
                },
                "read_only_notice": READ_ONLY_NOTICE,
            },
            maximum_bytes=20_000,
        )
        return LiveAgentPreview(
            case_type="warranty",
            evidence=evidence_preview,
            budget=budget,
            read_only_notice=READ_ONLY_NOTICE,
            preview_sha256=hashlib.sha256(scope_json.encode("utf-8")).hexdigest(),
        )

    def _question_preview(self, case_id: str, question: str) -> LiveAgentPreview:
        question = question.strip()
        if not 1 <= len(question) <= 2000 or "\x00" in question:
            raise AgentValidationError("Question must contain 1 to 2000 characters")
        # Bind the exact input and retrieval scope under one consistent read snapshot.
        self._connection.execute("BEGIN")
        try:
            basis = question_basis(self._connection, case_id)
            query = _retrieval_query(self._connection, case_id, question)
            budget = self._agent_budget()
            authorization = {
                "version": "live_agent_question.v1",
                "case_id": case_id,
                "question": question,
                "retrieval_query": query,
                "basis_sha256": basis_sha256(basis),
                "provider": self._configured_provider(),
                "model_id": self._configured_model_id(),
                "budget": {
                    "max_calls": budget.max_calls,
                    "max_input_tokens": budget.max_input_tokens,
                    "max_output_tokens": budget.max_output_tokens,
                    "max_cost_minor": budget.max_cost_minor,
                    "cost_currency": budget.cost_currency,
                },
                "read_only_notice": QUESTION_READ_ONLY_NOTICE,
            }
            authorization_hash = hashlib.sha256(
                canonical_json_object(authorization, maximum_bytes=20_000).encode()
            ).hexdigest()
            binding = canonical_json_object(
                {
                    "schema_version": "live_agent_question.v1",
                    "case_id": case_id,
                    "question": question,
                    "retrieval_query": query,
                    "basis": basis,
                    "basis_sha256": basis_sha256(basis),
                    "authorization_sha256": authorization_hash,
                },
                maximum_bytes=100_000,
            )
            validate_question_binding(binding, case_id)
            self._connection.execute("COMMIT")
            # The existing Evidence Context service owns its read transaction.
            # Recheck the bound authority after its read, before returning consent.
            scope = self._demo_service().evidence_scope(
                case_id,
                question=question,
                retrieval_query=query,
            )
            require_current_binding(self._connection, binding, case_id)
            evidence = scope.unique_segments
            return LiveAgentPreview(
                case_type="warranty",
                evidence=LiveAgentEvidencePreview(
                    len(evidence), sum(len(item.text.encode()) for item in evidence)
                ),
                budget=budget,
                read_only_notice=QUESTION_READ_ONLY_NOTICE,
                preview_sha256=authorization_hash,
                question=question,
                input_binding_json=binding,
            )
        finally:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")

    def create(
        self,
        case_id: str,
        *,
        preview_sha256: str,
        confirm_read_only: bool,
        question: str | None = None,
    ) -> LiveAgentState:
        self._require_ready()
        if confirm_read_only is not True:
            raise LiveAgentConfirmationRequiredError("Read-only confirmation is required")
        preview = self.preview(case_id, question=question)
        if preview.preview_sha256 != preview_sha256:
            raise LiveAgentPreviewStaleError("Live Agent preview changed")
        current = self._demo_service().get_state(case_id)
        next_version = len(current.history) + 1
        run_id = (
            f"{QUESTION_RUN_PREFIX}{uuid4().hex}"
            if question is not None
            else f"{LIVE_TWO_STAGE_RUN_PREFIX}{preview.preview_sha256}"
        )
        if question is None and next_version > 1:
            run_id = f"{run_id}-{next_version:04d}"
        self._demo_service().create(
            case_id,
            run_id=run_id,
            input_binding_json=preview.input_binding_json,
        )
        return self.get_state(case_id)

    async def execute(
        self,
        case_id: str,
        *,
        retry_only: bool = False,
        expected_run_id: str | None = None,
    ) -> LiveAgentState:
        self._require_ready()
        service = self._demo_service(for_execution=True)
        state = service.get_state(case_id)
        self._require_live_run(state)
        if state.run is not None and state.run.input_binding_json is not None:
            try:
                require_current_binding(self._connection, state.run.input_binding_json, case_id)
            except AgentIntegrityError as error:
                raise LiveAgentPreviewStaleError("Question analysis basis changed") from error
            binding = json.loads(state.run.input_binding_json)
            preview = self.preview(case_id, question=str(binding["question"]))
            if preview.preview_sha256 != binding["authorization_sha256"]:
                raise LiveAgentPreviewStaleError("Question authorization changed")
            await service.execute(case_id, retry_only=retry_only, expected_run_id=expected_run_id)
            return self.get_state(case_id)
        preview = self.preview(
            case_id,
            legacy=state.run is not None and not state.run.id.startswith(LIVE_TWO_STAGE_RUN_PREFIX),
        )
        self._require_current_scope(state, preview)
        await service.execute(case_id, retry_only=retry_only, expected_run_id=expected_run_id)
        return self.get_state(case_id)

    def cancel(self, case_id: str, *, expected_run_id: str | None = None) -> LiveAgentState:
        service = self._demo_service()
        self._require_live_run(service.get_state(case_id))
        service.cancel(case_id, expected_run_id=expected_run_id)
        return self.get_state(case_id)

    def recover(self, case_id: str, *, expected_run_id: str | None = None) -> LiveAgentState:
        service = self._demo_service()
        self._require_live_run(service.get_state(case_id))
        service.recover(case_id, expected_run_id=expected_run_id)
        return self.get_state(case_id)

    def _demo_service(self, *, for_execution: bool = False) -> PortfolioAgentDemoService:
        provider = None
        if for_execution:
            self._require_ready()
            provider = self._registry.compose(
                self._config.model_provider,
                clients=self._clients,
                groq_zdr_confirmed=self._config.groq_zdr_confirmed,
                mistral_zdr_confirmed=self._config.mistral_zdr_confirmed,
                deepseek_data_use_confirmed=self._config.deepseek_data_use_confirmed,
                gemini_data_use_confirmed=self._config.gemini_data_use_confirmed,
                anthropic_data_use_confirmed=self._config.anthropic_data_use_confirmed,
            )
        return PortfolioAgentDemoService(
            self._connection,
            enabled=True,
            evaluation_provider=provider,
            call_reservation=self._call_reservation(),
            stage_budget=self._stage_budget(),
            agent_budget=self._agent_budget(),
            run_id_prefix=LIVE_AGENT_RUN_PREFIX,
            allow_new_runs=True,
            enable_retrieval_fallback=True,
        )

    def _agent_budget(self) -> AgentBudget:
        if self._config.model_provider.provider is ModelProviderKind.DEEPSEEK:
            return _DEEPSEEK_AGENT_BUDGET
        return _AGENT_BUDGET

    def _stage_budget(self) -> StageBudget:
        if self._config.model_provider.provider is ModelProviderKind.DEEPSEEK:
            return _DEEPSEEK_STAGE_BUDGET
        return _STAGE_BUDGET

    def _call_reservation(self) -> CallReservation:
        if self._config.model_provider.provider is ModelProviderKind.DEEPSEEK:
            return _DEEPSEEK_CALL_RESERVATION
        return _CALL_RESERVATION

    def _capability(
        self,
    ) -> tuple[
        Literal["disabled", "unavailable", "ready"],
        Literal["disabled", "openai", "groq", "mistral", "deepseek", "gemini", "anthropic"],
        str | None,
        str,
    ]:
        if not self._config.live_agent_enabled:
            return (
                "disabled",
                "disabled",
                None,
                "Enable the live read-only Agent explicitly on the server.",
            )
        profile = self._configured_profile()
        if profile is None:
            return (
                "unavailable",
                "disabled",
                None,
                "Configure one supported server-owned Provider profile.",
            )
        if not profile.live_safe:
            action = (
                "Confirm approved synthetic/private DeepSeek data use in server configuration."
                if profile.provider is ModelProviderKind.DEEPSEEK
                else (
                    f"Confirm {profile.provider.value.title()} organization ZDR in server "
                    "configuration before live use."
                )
            )
            return (
                "unavailable",
                self._profile_provider(profile),
                profile.model_id,
                action,
            )
        if not self._client_available(profile):
            return (
                "unavailable",
                self._profile_provider(profile),
                profile.model_id,
                f"Inject the credentialed {profile.provider.value} client into this process.",
            )
        return (
            "ready",
            self._profile_provider(profile),
            profile.model_id,
            "Review the server preview and explicitly authorize one read-only run.",
        )

    def _require_ready(self) -> None:
        status, _provider, _model_id, _action = self._capability()
        if status == "disabled":
            raise LiveAgentDisabledError("Live Agent is disabled")
        profile = self._configured_profile()
        if profile is None or not profile.live_safe:
            raise LiveAgentProviderUnavailableError("Live Agent Provider is unavailable")
        if not self._client_available(profile):
            raise LiveAgentClientUnavailableError("Live Agent client is unavailable")

    def _configured_profile(self) -> ProviderProfile | None:
        try:
            return self._registry.profile(
                self._config.model_provider,
                groq_zdr_confirmed=self._config.groq_zdr_confirmed,
                mistral_zdr_confirmed=self._config.mistral_zdr_confirmed,
                deepseek_data_use_confirmed=self._config.deepseek_data_use_confirmed,
                gemini_data_use_confirmed=self._config.gemini_data_use_confirmed,
                anthropic_data_use_confirmed=self._config.anthropic_data_use_confirmed,
            )
        except ProviderConfigurationError:
            return None

    def _configured_provider(
        self,
    ) -> Literal["disabled", "openai", "groq", "mistral", "deepseek", "gemini", "anthropic"]:
        profile = self._configured_profile()
        return "disabled" if profile is None else self._profile_provider(profile)

    def _configured_model_id(self) -> str | None:
        profile = self._configured_profile()
        return None if profile is None else profile.model_id

    def _client_available(self, profile: ProviderProfile) -> bool:
        try:
            self._registry.compose(
                self._config.model_provider,
                clients=self._clients,
                groq_zdr_confirmed=self._config.groq_zdr_confirmed,
                mistral_zdr_confirmed=self._config.mistral_zdr_confirmed,
                deepseek_data_use_confirmed=self._config.deepseek_data_use_confirmed,
                gemini_data_use_confirmed=self._config.gemini_data_use_confirmed,
                anthropic_data_use_confirmed=self._config.anthropic_data_use_confirmed,
            )
        except ProviderConfigurationError:
            return False
        return profile.provider in {
            ModelProviderKind.OPENAI,
            ModelProviderKind.GROQ,
            ModelProviderKind.MISTRAL,
            ModelProviderKind.DEEPSEEK,
            ModelProviderKind.GEMINI,
            ModelProviderKind.ANTHROPIC,
        }

    @staticmethod
    def _profile_provider(
        profile: ProviderProfile,
    ) -> Literal["openai", "groq", "mistral", "deepseek", "gemini", "anthropic"]:
        if profile.provider is ModelProviderKind.OPENAI:
            return "openai"
        if profile.provider is ModelProviderKind.GROQ:
            return "groq"
        if profile.provider is ModelProviderKind.MISTRAL:
            return "mistral"
        if profile.provider is ModelProviderKind.DEEPSEEK:
            return "deepseek"
        if profile.provider is ModelProviderKind.GEMINI:
            return "gemini"
        if profile.provider is ModelProviderKind.ANTHROPIC:
            return "anthropic"
        raise AssertionError("Unsupported live Provider profile")

    @staticmethod
    def _require_live_run(state: PortfolioAgentDemoState) -> None:
        if state.run is None or not state.run.id.startswith(LIVE_AGENT_RUN_PREFIX):
            raise PortfolioAgentRunMissingError("Live Agent run was not found")

    @staticmethod
    def _require_current_scope(
        state: PortfolioAgentDemoState,
        preview: LiveAgentPreview,
    ) -> None:
        LiveAgentService._require_live_run(state)
        prefix = (
            LIVE_TWO_STAGE_RUN_PREFIX
            if state.run is not None and state.run.id.startswith(LIVE_TWO_STAGE_RUN_PREFIX)
            else LIVE_AGENT_RUN_PREFIX
        )
        expected = f"{prefix}{preview.preview_sha256}"
        if state.run is None or not (
            state.run.id == expected or state.run.id.startswith(f"{expected}-")
        ):
            raise LiveAgentPreviewStaleError("Live Agent preview changed")
