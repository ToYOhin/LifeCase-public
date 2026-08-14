"""Validated local runtime configuration for LifeCase."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from lifecase.providers import ModelProviderConfig


@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    """Filesystem and intake policy owned by one application instance."""

    database_path: Path
    data_root: Path
    upload_root: Path
    max_upload_bytes: int = 25 * 1024 * 1024
    model_provider: ModelProviderConfig = field(default_factory=ModelProviderConfig)
    portfolio_agent_demo_enabled: bool = False
    live_agent_enabled: bool = False
    groq_zdr_confirmed: bool = False
    mistral_zdr_confirmed: bool = False
    deepseek_data_use_confirmed: bool = False
    gemini_data_use_confirmed: bool = False
    anthropic_data_use_confirmed: bool = False
    allowed_media_types: frozenset[str] = frozenset(
        {"application/pdf", "message/rfc822", "text/plain"}
    )

    def __post_init__(self) -> None:
        if self.max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        if not self.allowed_media_types:
            raise ValueError("allowed_media_types must not be empty")
        if any(value != value.lower() for value in self.allowed_media_types):
            raise ValueError("allowed_media_types must be lowercase")
        if not isinstance(self.model_provider, ModelProviderConfig):
            raise ValueError("model_provider must be valid")
        if not isinstance(self.portfolio_agent_demo_enabled, bool):
            raise ValueError("portfolio_agent_demo_enabled must be a boolean")
        if not isinstance(self.live_agent_enabled, bool):
            raise ValueError("live_agent_enabled must be a boolean")
        if not isinstance(self.groq_zdr_confirmed, bool):
            raise ValueError("groq_zdr_confirmed must be a boolean")
        if not isinstance(self.mistral_zdr_confirmed, bool):
            raise ValueError("mistral_zdr_confirmed must be a boolean")
        if not isinstance(self.deepseek_data_use_confirmed, bool):
            raise ValueError("deepseek_data_use_confirmed must be a boolean")
        if not isinstance(self.gemini_data_use_confirmed, bool):
            raise ValueError("gemini_data_use_confirmed must be a boolean")
        if not isinstance(self.anthropic_data_use_confirmed, bool):
            raise ValueError("anthropic_data_use_confirmed must be a boolean")

        data_root = self.data_root.expanduser().resolve()
        upload_root = self.upload_root.expanduser().resolve()
        if data_root == upload_root or data_root.is_relative_to(upload_root):
            raise ValueError("data_root must not be inside upload_root")
        if upload_root.is_relative_to(data_root):
            raise ValueError("upload_root must not be inside data_root")

    @classmethod
    def from_environment(cls) -> "ApplicationConfig":
        """Build portable defaults, optionally rooted by LIFECASE_RUNTIME_ROOT."""

        runtime_root = Path(os.environ.get("LIFECASE_RUNTIME_ROOT", ".lifecase")).resolve()
        return cls(
            database_path=runtime_root / "lifecase.sqlite3",
            data_root=runtime_root / "data",
            upload_root=runtime_root / "uploads",
            model_provider=ModelProviderConfig.from_environment(os.environ),
            portfolio_agent_demo_enabled=portfolio_agent_demo_enabled(os.environ),
            live_agent_enabled=live_agent_enabled(os.environ),
            groq_zdr_confirmed=groq_zdr_confirmed(os.environ),
            mistral_zdr_confirmed=mistral_zdr_confirmed(os.environ),
            deepseek_data_use_confirmed=deepseek_data_use_confirmed(os.environ),
        )


def portfolio_agent_demo_enabled(environment: Mapping[str, str]) -> bool:
    """Read the explicit non-secret demo capability flag."""

    value = environment.get("LIFECASE_PORTFOLIO_AGENT_DEMO")
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError("LIFECASE_PORTFOLIO_AGENT_DEMO must be a boolean")


def live_agent_enabled(environment: Mapping[str, str]) -> bool:
    """Read the explicit non-secret live read-only capability flag."""

    value = environment.get("LIFECASE_LIVE_AGENT")
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError("LIFECASE_LIVE_AGENT must be a boolean")


def groq_zdr_confirmed(environment: Mapping[str, str]) -> bool:
    """Read the explicit server assertion that Groq organization ZDR is enabled."""

    value = environment.get("LIFECASE_GROQ_ZDR_CONFIRMED")
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError("LIFECASE_GROQ_ZDR_CONFIRMED must be a boolean")


def mistral_zdr_confirmed(environment: Mapping[str, str]) -> bool:
    """Read the explicit server assertion that Mistral organization ZDR is enabled."""

    value = environment.get("LIFECASE_MISTRAL_ZDR_CONFIRMED")
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError("LIFECASE_MISTRAL_ZDR_CONFIRMED must be a boolean")


def deepseek_data_use_confirmed(environment: Mapping[str, str]) -> bool:
    """Read the server assertion that DeepSeek use is limited to approved data."""

    value = environment.get("LIFECASE_DEEPSEEK_DATA_USE_CONFIRMED")
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError("LIFECASE_DEEPSEEK_DATA_USE_CONFIRMED must be a boolean")
