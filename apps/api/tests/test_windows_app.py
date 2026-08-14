"""Tests for the frozen Windows double-click entry."""

from pathlib import Path
from typing import Any

import pytest

from lifecase import windows_app


def test_frozen_layout_uses_executable_and_meipass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "LifeCase" / "LifeCase.exe"
    bundle = tmp_path / "LifeCase" / "_internal"
    monkeypatch.setattr(windows_app.sys, "executable", str(executable))
    monkeypatch.setattr(windows_app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows_app.sys, "_MEIPASS", str(bundle), raising=False)

    assert windows_app.program_root() == executable.parent
    assert windows_app.static_root() == bundle / "web"


def test_default_launch_initializes_starts_and_opens_browser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data-root"
    installed_root = tmp_path / "program"
    web_root = tmp_path / "web"
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(windows_app, "default_runtime_root", lambda: data_root)
    monkeypatch.setattr(windows_app, "program_root", lambda: installed_root)
    monkeypatch.setattr(windows_app, "static_root", lambda: web_root)
    monkeypatch.setattr(
        windows_app,
        "initialize_runtime",
        lambda selected, *, program_root: calls.append(("initialize", selected, program_root)),
    )
    monkeypatch.setattr(
        windows_app,
        "runtime_status",
        lambda selected, *, program_root: {"status": "stopped", "data_retained": True},
    )
    monkeypatch.setattr(
        windows_app,
        "start_runtime",
        lambda selected, *, program_root, static_root, port: {
            "status": "running",
            "url": "http://127.0.0.1:8765",
        },
    )
    monkeypatch.setattr(
        windows_app.webbrowser,
        "open",
        lambda url, *, new: calls.append(("browser", url, new)) or True,
    )

    assert windows_app.main([]) == 0
    assert calls == [
        ("initialize", data_root, installed_root),
        ("browser", "http://127.0.0.1:8765", 2),
    ]


def test_internal_serve_delegates_to_daily_use(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(
        windows_app,
        "daily_use_main",
        lambda arguments: captured.append(list(arguments)) or 7,
    )

    assert windows_app.main(["_serve", "--data-root", "synthetic"]) == 7
    assert captured == [["_serve", "--data-root", "synthetic"]]


def test_no_dialog_suppresses_status_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(windows_app, "program_root", lambda: tmp_path / "program")
    monkeypatch.setattr(
        windows_app,
        "runtime_status",
        lambda selected, *, program_root: {"status": "stopped", "data_retained": True},
    )
    monkeypatch.setattr(
        windows_app,
        "_show_message",
        lambda *args, **kwargs: pytest.fail("dialog should be suppressed"),
    )

    assert windows_app.main(["--status", "--no-dialog", "--data-root", str(root)]) == 0
