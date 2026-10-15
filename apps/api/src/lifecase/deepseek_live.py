"""Loopback-only DeepSeek Live Agent demo launcher with hidden credential input."""

from __future__ import annotations

import argparse
import getpass
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from lifecase.config import ApplicationConfig
from lifecase.demo_readiness import DemoRuntimeError, prepare_demo_root
from lifecase.main import create_app
from lifecase.providers import (
    DEEPSEEK_CHAT_MODEL_ID,
    DeepSeekHttpClient,
    ModelProviderConfig,
    ModelProviderKind,
)

_DEFAULT_PORT = 8765


def build_deepseek_live_app(
    *,
    runtime_root: Path,
    api_key: str,
    static_root: Path | None = None,
) -> FastAPI:
    """Compose the fixed DeepSeek profile without placing its credential in config."""

    root = prepare_demo_root(runtime_root)
    config = ApplicationConfig(
        database_path=root / "lifecase.sqlite3",
        data_root=root / "data",
        upload_root=root / "uploads",
        model_provider=ModelProviderConfig(
            provider=ModelProviderKind.DEEPSEEK,
            model_id=DEEPSEEK_CHAT_MODEL_ID,
            timeout_seconds=120.0,
        ),
        live_agent_enabled=True,
        deepseek_data_use_confirmed=True,
    )
    client = DeepSeekHttpClient(api_key, timeout_seconds=120.0)
    return create_app(
        config,
        static_root=static_root,
        deepseek_client=client,
    )


def main(arguments: Sequence[str] | None = None) -> int:
    """Prompt once for a process-local key and run one loopback Uvicorn worker."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--static-root", type=Path)
    parser.add_argument("--port", type=_port, default=_DEFAULT_PORT)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--confirm-synthetic-data", action="store_true")
    parsed = parser.parse_args(arguments)
    if not parsed.confirm_synthetic_data:
        parser.error("--confirm-synthetic-data is required")
    if not sys.stdin.isatty():
        parser.error("an interactive terminal is required for hidden credential input")

    try:
        if parsed.static_root is not None:
            _validate_web_build(parsed.static_root)
        prepare_demo_root(parsed.runtime_root)
        _ensure_port_available(parsed.port)
    except (DemoRuntimeError, OSError) as error:
        parser.error(str(error))

    api_key = getpass.getpass("DeepSeek API key (hidden, process-only): ").strip()
    if not api_key:
        parser.error("a DeepSeek API key is required")
    application = build_deepseek_live_app(
        runtime_root=parsed.runtime_root,
        static_root=parsed.static_root,
        api_key=api_key,
    )
    if parsed.open_browser:
        _start_browser_open(parsed.port)
    uvicorn.run(
        application,
        host="127.0.0.1",
        port=parsed.port,
        workers=1,
        access_log=False,
        server_header=False,
        log_level="info",
    )
    return 0


def _port(value: str) -> int:
    port = int(value)
    if not 1_024 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def _validate_web_build(static_root: Path) -> None:
    try:
        root = static_root.expanduser().resolve(strict=True)
        assets = root / "assets"
        valid = (
            root.is_dir()
            and not static_root.is_symlink()
            and (root / "index.html").is_file()
            and assets.is_dir()
            and not assets.is_symlink()
            and any(item.is_file() and not item.is_symlink() for item in assets.iterdir())
        )
    except OSError:
        valid = False
    if not valid:
        raise OSError("Built Web assets are missing; run npm run build:web")


def _ensure_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as error:
            raise OSError(f"127.0.0.1:{port} is already in use") from error


def _start_browser_open(port: int) -> None:
    threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()


def _open_when_ready(port: int) -> None:
    health_url = f"http://127.0.0.1:{port}/api/v1/system/health"
    for _attempt in range(50):
        try:
            with urllib.request.urlopen(health_url, timeout=0.5) as response:
                if response.status == 200:
                    webbrowser.open(f"http://127.0.0.1:{port}/")
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(main())
