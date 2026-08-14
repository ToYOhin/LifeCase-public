"""FastAPI application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from lifecase.api.errors import install_error_handlers
from lifecase.api.router import api_router
from lifecase.config import ApplicationConfig
from lifecase.providers import (
    DeepSeekChatClient,
    GroqChatClient,
    MistralChatClient,
    OpenAIResponsesClient,
    ProviderRuntimeClients,
)
from lifecase.providers.runtime_settings import (
    AiSettingsManager,
    CredentialStore,
    RuntimeClientFactory,
    UnavailableCredentialStore,
    default_credential_store,
    default_runtime_client_factory,
)
from lifecase.storage import connect_database
from lifecase.storage.backup import apply_migrations_with_backup


class StaticAssetsError(RuntimeError):
    """Raised when the daily-use Web build is absent or unsafe to serve."""

    code = "static_assets_missing"

    def __init__(self) -> None:
        super().__init__("Built Web assets are missing. Run the package setup command.")


def create_app(
    config: ApplicationConfig | None = None,
    *,
    static_root: Path | None = None,
    prepared: bool = False,
    openai_client: OpenAIResponsesClient | None = None,
    groq_client: GroqChatClient | None = None,
    mistral_client: MistralChatClient | None = None,
    deepseek_client: DeepSeekChatClient | None = None,
    credential_store: CredentialStore | None = None,
    runtime_client_factory: RuntimeClientFactory | None = None,
    persistent_ai_settings: bool | None = None,
) -> FastAPI:
    """Create the local LifeCase API application."""

    resolved_config = config or ApplicationConfig.from_environment()
    persistence_enabled = (
        config is None if persistent_ai_settings is None else persistent_ai_settings
    )
    resolved_store = credential_store or (
        default_credential_store() if persistence_enabled else UnavailableCredentialStore()
    )
    base_clients = ProviderRuntimeClients(
        openai=openai_client,
        groq=groq_client,
        mistral=mistral_client,
        deepseek=deepseek_client,
    )
    settings_manager = AiSettingsManager(
        resolved_config,
        base_clients,
        resolved_store,
        runtime_client_factory or default_runtime_client_factory,
    )

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        if not prepared:
            prepare_application(resolved_config)
        yield

    application = FastAPI(
        title="LifeCase API",
        summary="Evidence-backed personal life administration agent",
        version="0.2.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    application.state.config = resolved_config
    application.state.openai_client = openai_client
    application.state.groq_client = groq_client
    application.state.mistral_client = mistral_client
    application.state.deepseek_client = deepseek_client
    application.state.ai_settings_manager = settings_manager
    install_error_handlers(application)
    application.include_router(api_router, prefix="/api/v1")
    if static_root is not None:
        resolved_static_root = _validated_static_root(static_root)
        application.mount(
            "/",
            StaticFiles(directory=resolved_static_root, html=True, follow_symlink=False),
            name="web",
        )
    return application


def prepare_application(config: ApplicationConfig) -> None:
    """Apply the existing SQLite/FTS and verified pre-migration Backup order."""

    config.data_root.mkdir(parents=True, exist_ok=True)
    config.upload_root.mkdir(parents=True, exist_ok=True)
    connection = connect_database(config.database_path)
    try:
        apply_migrations_with_backup(connection, config.database_path, config.data_root)
    finally:
        connection.close()


def _validated_static_root(static_root: Path) -> Path:
    try:
        root = static_root.expanduser().resolve(strict=True)
        assets = root / "assets"
        if (
            static_root.is_symlink()
            or not root.is_dir()
            or not (root / "index.html").is_file()
            or assets.is_symlink()
            or not assets.is_dir()
            or not any(item.is_file() and not item.is_symlink() for item in assets.iterdir())
        ):
            raise StaticAssetsError
    except (OSError, RuntimeError) as error:
        if isinstance(error, StaticAssetsError):
            raise
        raise StaticAssetsError from None
    return root


app = create_app()
