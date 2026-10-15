"""Fixed, process-injected DeepSeek Live Agent launcher coverage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lifecase.deepseek_live import build_deepseek_live_app, main
from lifecase.demo_readiness import (
    DEMO_MARKER_FILENAME,
    DemoRuntimeError,
    prepare_demo_root,
    reset_demo_root,
)
from lifecase.providers import DEEPSEEK_CHAT_ENDPOINT, DEEPSEEK_CHAT_MODEL_ID, ModelProviderKind


def test_launcher_builds_fixed_live_app_without_putting_credential_in_config(
    tmp_path: Path,
) -> None:
    static_root = _static_root(tmp_path)
    credential = "synthetic-launcher-secret-canary"

    application = build_deepseek_live_app(
        runtime_root=tmp_path / "runtime",
        static_root=static_root,
        api_key=credential,
    )

    config = application.state.config
    assert config.live_agent_enabled is True
    assert config.deepseek_data_use_confirmed is True
    assert config.model_provider.provider is ModelProviderKind.DEEPSEEK
    assert config.model_provider.model_id == DEEPSEEK_CHAT_MODEL_ID
    assert application.state.deepseek_client.endpoint == DEEPSEEK_CHAT_ENDPOINT
    assert credential not in repr(config)
    assert credential not in repr(application)
    assert credential not in repr(application.state.deepseek_client)
    assert not config.database_path.exists()
    assert (tmp_path / "runtime" / DEMO_MARKER_FILENAME).is_file()


def test_launcher_requires_explicit_data_confirmation_before_prompting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompted = False

    def prompt(_message: str) -> str:
        nonlocal prompted
        prompted = True
        return "unused"

    monkeypatch.setattr("lifecase.deepseek_live.getpass.getpass", prompt)

    with pytest.raises(SystemExit) as caught:
        main(["--runtime-root", str(tmp_path / "runtime")])

    assert caught.value.code == 2
    assert prompted is False


def test_launcher_prompts_once_and_runs_loopback_without_access_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credential = "synthetic-launcher-secret-canary"
    prompts: list[str] = []
    calls: list[tuple[object, dict[str, Any]]] = []

    def prompt(message: str) -> str:
        prompts.append(message)
        return credential

    def run(application: object, **kwargs: Any) -> None:
        calls.append((application, kwargs))

    monkeypatch.setattr("lifecase.deepseek_live.getpass.getpass", prompt)
    monkeypatch.setattr("lifecase.deepseek_live.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("lifecase.deepseek_live._ensure_port_available", lambda _port: None)
    monkeypatch.setattr("lifecase.deepseek_live.uvicorn.run", run)

    result = main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--port",
            "8765",
            "--confirm-synthetic-data",
        ]
    )

    assert result == 0
    assert len(prompts) == 1
    assert len(calls) == 1
    application, kwargs = calls[0]
    assert application.state.config.deepseek_data_use_confirmed is True
    assert kwargs == {
        "host": "127.0.0.1",
        "port": 8765,
        "workers": 1,
        "access_log": False,
        "server_header": False,
        "log_level": "info",
    }
    assert credential not in repr(kwargs)


def test_launcher_checks_web_build_and_port_before_prompting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompted = False

    def prompt(_message: str) -> str:
        nonlocal prompted
        prompted = True
        return "unused"

    monkeypatch.setattr("lifecase.deepseek_live.getpass.getpass", prompt)
    monkeypatch.setattr("lifecase.deepseek_live.sys.stdin.isatty", lambda: True)

    with pytest.raises(SystemExit) as missing_build:
        main(
            [
                "--runtime-root",
                str(tmp_path / "runtime"),
                "--static-root",
                str(tmp_path / "missing-web"),
                "--confirm-synthetic-data",
            ]
        )

    assert missing_build.value.code == 2
    assert prompted is False

    static_root = _static_root(tmp_path)
    monkeypatch.setattr(
        "lifecase.deepseek_live._ensure_port_available",
        lambda _port: (_ for _ in ()).throw(OSError("occupied")),
    )
    with pytest.raises(SystemExit) as occupied_port:
        main(
            [
                "--runtime-root",
                str(tmp_path / "runtime"),
                "--static-root",
                str(static_root),
                "--confirm-synthetic-data",
            ]
        )

    assert occupied_port.value.code == 2
    assert prompted is False


def test_launcher_can_request_browser_open_without_exposing_credential(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    opened: list[int] = []
    credential = "synthetic-launcher-secret-canary"

    monkeypatch.setattr("lifecase.deepseek_live.getpass.getpass", lambda _message: credential)
    monkeypatch.setattr("lifecase.deepseek_live.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("lifecase.deepseek_live._ensure_port_available", lambda _port: None)
    monkeypatch.setattr("lifecase.deepseek_live._start_browser_open", opened.append)
    monkeypatch.setattr("lifecase.deepseek_live.uvicorn.run", lambda *_args, **_kwargs: None)

    result = main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--static-root",
            str(_static_root(tmp_path)),
            "--port",
            "8766",
            "--open-browser",
            "--confirm-synthetic-data",
        ]
    )

    assert result == 0
    assert opened == [8766]


def test_demo_root_refuses_unmarked_data_and_reset_is_marker_bound(tmp_path: Path) -> None:
    unmarked = tmp_path / "ordinary-data"
    unmarked.mkdir()
    (unmarked / "keep.txt").write_text("ordinary", encoding="utf-8")

    with pytest.raises(DemoRuntimeError, match="not an initialized"):
        prepare_demo_root(unmarked)
    with pytest.raises(DemoRuntimeError, match="not an initialized"):
        reset_demo_root(unmarked)

    demo_root = tmp_path / "LifeCase-DeepSeek-Demo"
    resolved = prepare_demo_root(demo_root)
    (resolved / "lifecase.sqlite3").write_bytes(b"synthetic")

    removed = reset_demo_root(demo_root)

    assert removed == resolved
    assert not resolved.exists()
    assert (unmarked / "keep.txt").read_text(encoding="utf-8") == "ordinary"


def test_demo_root_rejects_drive_root_and_tampered_marker(tmp_path: Path) -> None:
    with pytest.raises(DemoRuntimeError, match="unsafe"):
        prepare_demo_root(Path(tmp_path.anchor))

    demo_root = tmp_path / "LifeCase-DeepSeek-Demo"
    prepare_demo_root(demo_root)
    (demo_root / DEMO_MARKER_FILENAME).write_text('{"schema":"wrong"}', encoding="utf-8")

    with pytest.raises(DemoRuntimeError, match="marker is invalid"):
        reset_demo_root(demo_root)


def test_launcher_rejects_noninteractive_secret_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompted = False

    def prompt(_message: str) -> str:
        nonlocal prompted
        prompted = True
        return "unused"

    monkeypatch.setattr("lifecase.deepseek_live.getpass.getpass", prompt)
    monkeypatch.setattr("lifecase.deepseek_live.sys.stdin.isatty", lambda: False)

    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--runtime-root",
                str(tmp_path / "runtime"),
                "--confirm-synthetic-data",
            ]
        )

    assert caught.value.code == 2
    assert prompted is False


def _static_root(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html></html>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("export {};", encoding="utf-8")
    return root
