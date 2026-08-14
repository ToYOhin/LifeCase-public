"""System status endpoints."""

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from lifecase.api.dependencies import (
    AiSettingsDependency,
    ProviderClientsDependency,
    get_config,
    get_database,
)
from lifecase.api.errors import ApiError
from lifecase.config import ApplicationConfig
from lifecase.providers import (
    ANTHROPIC_MESSAGES_MODEL_ID,
    DEEPSEEK_CHAT_MODEL_ID,
    GEMINI_GENERATE_MODEL_ID,
    GROQ_CHAT_MODEL_ID,
    MISTRAL_CHAT_MODEL_ID,
    ModelProviderKind,
    ProviderConfigurationError,
    ProviderRegistry,
    ProviderRuntimeClients,
)
from lifecase.providers.runtime_settings import (
    AiSettingsSnapshot,
    CredentialStoreError,
)
from lifecase.storage.backup import BackupNotFoundError, BackupRestoreError, BackupService

router = APIRouter()


class HealthResponse(BaseModel):
    """Public health response contract."""

    status: Literal["ok"]
    service: Literal["lifecase-api"]
    version: str


class CapabilityStatus(BaseModel):
    status: Literal["unavailable", "disabled"]
    action: str
    code: str | None = None


class VerifiedBackupSummary(BaseModel):
    id: str
    created_at: str
    size_bytes: int
    case_count: int


class BackupReminder(BaseModel):
    status: Literal["verified", "no_verified_backup", "verification_failed"]
    latest: VerifiedBackupSummary | None
    action: str


class CapabilitiesResponse(BaseModel):
    ocr: CapabilityStatus
    model: CapabilityStatus
    backup_reminder: BackupReminder


ProviderName = Literal["openai", "deepseek", "groq", "mistral", "gemini", "anthropic", "ollama"]
AiRuntimeStatus = Literal["disabled", "ready", "unavailable", "evaluation_only"]


class AiProviderSummary(BaseModel):
    """Safe presentation metadata for one implemented Provider boundary."""

    provider: ProviderName
    label: str
    interface_type: Literal["cloud_api", "local_model"]
    product_status: Literal["registered", "evaluation_only"]
    evidence_status: Literal[
        "offline_injection_verified",
        "synthetic_acceptance_verified",
        "quality_gate_failed",
    ]
    selected: bool
    runtime_status: AiRuntimeStatus
    model_id: str | None
    action: str


class AiCapabilitiesResponse(BaseModel):
    """Server-owned catalog and safe local settings capability."""

    catalog_version: Literal["ai_provider_catalog.v1"]
    current_provider: str
    current_model_id: str | None
    runtime_status: AiRuntimeStatus
    browser_configuration: Literal["persistent_local_settings"]
    providers: list[AiProviderSummary]


class AiSettingsRequest(BaseModel):
    """One allowlisted local Provider credential; never serialized in responses."""

    model_config = ConfigDict(extra="forbid")

    provider: ModelProviderKind
    model_id: str
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=1_500)
    data_use_confirmed: bool


class AiSettingsResponse(BaseModel):
    settings_version: Literal["ai_settings.v1"]
    persistence: Literal["windows_credential_manager", "unavailable"]
    configured: bool
    provider: str | None
    model_id: str | None
    runtime_status: Literal["disabled", "ready"]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report whether the API process is ready to receive local requests."""
    return HealthResponse(status="ok", service="lifecase-api", version="0.2.0")


@router.get("/ai-settings", response_model=AiSettingsResponse)
def ai_settings(
    manager: AiSettingsDependency,
    response: Response,
) -> AiSettingsResponse:
    """Return only non-secret persistent Provider status."""

    response.headers["Cache-Control"] = "no-store"
    return _ai_settings_response(manager.snapshot())


@router.put("/ai-settings", response_model=AiSettingsResponse)
def save_ai_settings(
    request: AiSettingsRequest,
    manager: AiSettingsDependency,
    response: Response,
    local_intent: Annotated[str | None, Header(alias="X-LifeCase-Local-Intent")] = None,
) -> AiSettingsResponse:
    """Persist one allowlisted credential in the current Windows user profile."""

    _require_ai_settings_intent(local_intent)
    response.headers["Cache-Control"] = "no-store"
    try:
        snapshot = manager.save(
            provider=request.provider,
            model_id=request.model_id,
            api_key=(None if request.api_key is None else request.api_key.get_secret_value()),
            data_use_confirmed=request.data_use_confirmed,
        )
    except CredentialStoreError:
        raise ApiError(
            status_code=503,
            code="ai_settings_storage_unavailable",
            message="Persistent AI credential storage is unavailable",
        ) from None
    except ValueError:
        raise ApiError(
            status_code=422,
            code="ai_settings_invalid",
            message="AI Provider settings are invalid or incomplete",
        ) from None
    return _ai_settings_response(snapshot)


@router.delete("/ai-settings", response_model=AiSettingsResponse)
def clear_ai_settings(
    manager: AiSettingsDependency,
    response: Response,
    local_intent: Annotated[str | None, Header(alias="X-LifeCase-Local-Intent")] = None,
) -> AiSettingsResponse:
    """Remove the saved local Provider credential and restore the base runtime."""

    _require_ai_settings_intent(local_intent)
    response.headers["Cache-Control"] = "no-store"
    try:
        snapshot = manager.clear()
    except CredentialStoreError:
        raise ApiError(
            status_code=503,
            code="ai_settings_storage_unavailable",
            message="Persistent AI credential storage is unavailable",
        ) from None
    return _ai_settings_response(snapshot)


def _require_ai_settings_intent(value: str | None) -> None:
    if value != "ai-settings.v1":
        raise ApiError(
            status_code=403,
            code="ai_settings_intent_required",
            message="Explicit local AI settings intent is required",
        )


def _ai_settings_response(snapshot: AiSettingsSnapshot) -> AiSettingsResponse:
    return AiSettingsResponse(
        settings_version="ai_settings.v1",
        persistence=snapshot.persistence,
        configured=snapshot.configured,
        provider=None if snapshot.provider is None else snapshot.provider.value,
        model_id=snapshot.model_id,
        runtime_status=snapshot.runtime_status,
    )


@router.get(
    "/capabilities",
    response_model=CapabilitiesResponse,
    response_model_exclude_none=True,
)
async def capabilities(
    config: Annotated[ApplicationConfig, Depends(get_config)],
    connection: Annotated[sqlite3.Connection, Depends(get_database)],
) -> CapabilitiesResponse:
    """Report explicit offline capabilities and the newest verified Backup only."""

    backup_reminder = _backup_reminder(config, connection)
    model_status: Literal["disabled", "unavailable"] = (
        "disabled"
        if config.model_provider.provider is ModelProviderKind.DISABLED
        else "unavailable"
    )
    return CapabilitiesResponse(
        ocr=CapabilityStatus(
            status="unavailable",
            code="OCR_REQUIRED",
            action="Use a text PDF, TXT, or EML source; OCR is not included.",
        ),
        model=CapabilityStatus(
            status=model_status,
            action="Deterministic Case reads, exports, and Backup remain available.",
        ),
        backup_reminder=backup_reminder,
    )


@router.get("/ai-capabilities", response_model=AiCapabilitiesResponse)
async def ai_capabilities(
    config: Annotated[ApplicationConfig, Depends(get_config)],
    clients: ProviderClientsDependency,
) -> AiCapabilitiesResponse:
    """Describe every current Provider boundary without exposing mutable configuration."""

    current_provider = config.model_provider.provider
    runtime_status = _ai_runtime_status(config, clients)
    definitions: tuple[
        tuple[
            ProviderName,
            str,
            Literal["cloud_api", "local_model"],
            Literal["registered", "evaluation_only"],
            Literal[
                "offline_injection_verified",
                "synthetic_acceptance_verified",
                "quality_gate_failed",
            ],
            str | None,
            str,
        ],
        ...,
    ] = (
        (
            "openai",
            "OpenAI API",
            "cloud_api",
            "registered",
            "offline_injection_verified",
            "gpt-5.6-terra",
            "可在本机 AI 设置中保存凭据；仅支持只读案件分析。",
        ),
        (
            "deepseek",
            "DeepSeek API",
            "cloud_api",
            "registered",
            "synthetic_acceptance_verified",
            DEEPSEEK_CHAT_MODEL_ID,
            "可在本机 AI 设置中保存凭据；运行前须确认外部材料处理。",
        ),
        (
            "groq",
            "Groq API",
            "cloud_api",
            "registered",
            "offline_injection_verified",
            GROQ_CHAT_MODEL_ID,
            "可在本机 AI 设置中保存凭据；使用前须确认账户已启用 ZDR。",
        ),
        (
            "mistral",
            "Mistral API",
            "cloud_api",
            "registered",
            "offline_injection_verified",
            MISTRAL_CHAT_MODEL_ID,
            "可在本机 AI 设置中保存凭据；使用前须确认账户已启用 ZDR。",
        ),
        (
            "gemini",
            "Google Gemini API",
            "cloud_api",
            "registered",
            "offline_injection_verified",
            GEMINI_GENERATE_MODEL_ID,
            "使用 Google generateContent 结构化输出；仅支持只读案件分析。",
        ),
        (
            "anthropic",
            "Anthropic Claude API",
            "cloud_api",
            "registered",
            "offline_injection_verified",
            ANTHROPIC_MESSAGES_MODEL_ID,
            "使用 Anthropic Messages API；Claude Code CLI 不属于此接口。",
        ),
        (
            "ollama",
            "Ollama 本地模型",
            "local_model",
            "evaluation_only",
            "quality_gate_failed",
            None,
            "严格质量门禁未通过；不用于产品 Agent，也不会由页面启动模型。",
        ),
    )
    providers = [
        AiProviderSummary(
            provider=provider,
            label=label,
            interface_type=interface_type,
            product_status=product_status,
            evidence_status=evidence_status,
            selected=current_provider.value == provider,
            runtime_status=(runtime_status if current_provider.value == provider else "disabled"),
            model_id=(
                config.model_provider.model_id if current_provider.value == provider else model_id
            ),
            action=action,
        )
        for (
            provider,
            label,
            interface_type,
            product_status,
            evidence_status,
            model_id,
            action,
        ) in definitions
    ]
    return AiCapabilitiesResponse(
        catalog_version="ai_provider_catalog.v1",
        current_provider=current_provider.value,
        current_model_id=config.model_provider.model_id,
        runtime_status=runtime_status,
        browser_configuration="persistent_local_settings",
        providers=providers,
    )


def _ai_runtime_status(
    config: ApplicationConfig,
    clients: ProviderRuntimeClients,
) -> AiRuntimeStatus:
    provider = config.model_provider.provider
    if provider is ModelProviderKind.DISABLED or not config.live_agent_enabled:
        return "disabled"
    if provider is ModelProviderKind.OLLAMA:
        return "evaluation_only"
    try:
        ProviderRegistry().compose(
            config.model_provider,
            clients=clients,
            groq_zdr_confirmed=config.groq_zdr_confirmed,
            mistral_zdr_confirmed=config.mistral_zdr_confirmed,
            deepseek_data_use_confirmed=config.deepseek_data_use_confirmed,
            gemini_data_use_confirmed=config.gemini_data_use_confirmed,
            anthropic_data_use_confirmed=config.anthropic_data_use_confirmed,
        )
    except ProviderConfigurationError:
        return "unavailable"
    return "ready"


def _backup_reminder(config: ApplicationConfig, connection: sqlite3.Connection) -> BackupReminder:
    service = BackupService(connection, config.database_path, config.data_root)
    try:
        latest = service.get_latest()
    except BackupNotFoundError:
        return BackupReminder(
            status="no_verified_backup",
            latest=None,
            action="Create a Backup explicitly from the Backup and Restore panel.",
        )
    except BackupRestoreError:
        return BackupReminder(
            status="verification_failed",
            latest=None,
            action="Review Backup storage, then explicitly create and verify a new Backup.",
        )
    return BackupReminder(
        status="verified",
        latest=VerifiedBackupSummary(
            id=latest.id,
            created_at=latest.created_at,
            size_bytes=latest.size_bytes,
            case_count=latest.case_count,
        ),
        action="Keep this verified Backup or explicitly create a newer one before changes.",
    )
