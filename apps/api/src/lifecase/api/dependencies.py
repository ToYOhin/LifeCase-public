"""Request-scoped infrastructure dependencies."""

import sqlite3
from collections.abc import Iterator
from typing import Annotated, cast

from fastapi import Depends, Request

from lifecase.config import ApplicationConfig
from lifecase.providers import (
    OpenAIResponsesClient,
    ProviderRuntimeClients,
)
from lifecase.providers.runtime_settings import AiSettingsManager
from lifecase.storage import connect_database


def get_config(request: Request) -> ApplicationConfig:
    """Read the immutable configuration owned by the app lifespan."""

    manager = cast(AiSettingsManager, request.app.state.ai_settings_manager)
    return manager.effective_config()


def get_database(
    config: Annotated[ApplicationConfig, Depends(get_config)],
) -> Iterator[sqlite3.Connection]:
    """Open and close one configured SQLite connection per request."""

    connection = connect_database(config.database_path)
    try:
        yield connection
    finally:
        connection.close()


def get_openai_client(request: Request) -> OpenAIResponsesClient | None:
    """Read only the explicitly injected process-local client boundary."""

    return cast(OpenAIResponsesClient | None, request.app.state.openai_client)


def get_provider_clients(request: Request) -> ProviderRuntimeClients:
    """Build the process-local client set without reading credentials or configuration."""

    manager = cast(AiSettingsManager, request.app.state.ai_settings_manager)
    return manager.effective_clients()


def get_ai_settings_manager(request: Request) -> AiSettingsManager:
    return cast(AiSettingsManager, request.app.state.ai_settings_manager)


ConfigDependency = Annotated[ApplicationConfig, Depends(get_config)]
DatabaseDependency = Annotated[sqlite3.Connection, Depends(get_database)]
OpenAIClientDependency = Annotated[OpenAIResponsesClient | None, Depends(get_openai_client)]
ProviderClientsDependency = Annotated[ProviderRuntimeClients, Depends(get_provider_clients)]
AiSettingsDependency = Annotated[AiSettingsManager, Depends(get_ai_settings_manager)]
