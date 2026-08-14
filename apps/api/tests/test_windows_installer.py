"""Tests for the per-user Windows installer boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lifecase import windows_installer


def _payload(root: Path, content: str = "new") -> Path:
    payload = root / "payload"
    payload.mkdir(parents=True)
    (payload / "LifeCase.exe").write_text(content, encoding="utf-8")
    (payload / "_internal").mkdir()
    (payload / "_internal" / "web.txt").write_text(content, encoding="utf-8")
    return payload


def _disable_windows_integration(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        windows_installer,
        "_create_shortcuts",
        lambda root, *, desktop: calls.append(f"shortcuts:{desktop}"),
    )
    monkeypatch.setattr(
        windows_installer, "_register_uninstall", lambda root: calls.append("register")
    )
    monkeypatch.setattr(
        windows_installer, "_launch_application", lambda root: calls.append("launch")
    )
    monkeypatch.setattr(
        windows_installer,
        "_stop_installed_application",
        lambda root, *, data_root=None: None,
    )
    return calls


def test_install_stages_marked_payload_and_windows_integration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    programs = tmp_path / "Programs"
    programs.mkdir()
    payload = _payload(tmp_path / "source")
    setup = tmp_path / "LifeCaseSetup.exe"
    setup.write_bytes(b"setup")
    calls = _disable_windows_integration(monkeypatch)

    installed = windows_installer.install_application(
        programs / "LifeCase",
        programs_root=programs,
        payload_root=payload,
        setup_executable=setup,
    )

    assert (installed / "LifeCase.exe").read_text(encoding="utf-8") == "new"
    assert (installed / "LifeCaseUninstall.exe").read_bytes() == b"setup"
    marker = json.loads((installed / ".lifecase-install.json").read_text(encoding="utf-8"))
    assert marker == {
        "app": "LifeCase",
        "format": "lifecase.windows-install.v1",
        "version": "0.2.0",
    }
    assert calls == ["shortcuts:True", "register", "launch"]


def test_install_rejects_an_unmarked_existing_directory(tmp_path: Path) -> None:
    programs = tmp_path / "Programs"
    existing = programs / "LifeCase"
    existing.mkdir(parents=True)
    payload = _payload(tmp_path / "source")
    setup = tmp_path / "LifeCaseSetup.exe"
    setup.write_bytes(b"setup")

    with pytest.raises(windows_installer.InstallerError, match="not a marked"):
        windows_installer.install_application(
            existing,
            programs_root=programs,
            payload_root=payload,
            setup_executable=setup,
        )

    assert list(existing.iterdir()) == []


def test_update_replaces_program_but_preserves_separate_user_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    programs = tmp_path / "Programs"
    root = programs / "LifeCase"
    root.mkdir(parents=True)
    (root / "LifeCase.exe").write_text("old", encoding="utf-8")
    (root / ".lifecase-install.json").write_text(
        json.dumps(
            {"format": "lifecase.windows-install.v1", "app": "LifeCase", "version": "0.1.0"}
        ),
        encoding="utf-8",
    )
    data = tmp_path / "user-data"
    data.mkdir()
    database = data / "lifecase.sqlite3"
    database.write_bytes(b"retained")
    payload = _payload(tmp_path / "source", "updated")
    setup = tmp_path / "LifeCaseSetup.exe"
    setup.write_bytes(b"setup")
    _disable_windows_integration(monkeypatch)

    windows_installer.install_application(
        root,
        programs_root=programs,
        payload_root=payload,
        setup_executable=setup,
        launch=False,
    )

    assert (root / "LifeCase.exe").read_text(encoding="utf-8") == "updated"
    assert database.read_bytes() == b"retained"
    assert not list(programs.glob(".LifeCase-*"))


def test_update_rolls_back_when_windows_integration_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    programs = tmp_path / "Programs"
    root = programs / "LifeCase"
    root.mkdir(parents=True)
    (root / "LifeCase.exe").write_text("old", encoding="utf-8")
    (root / ".lifecase-install.json").write_text(
        json.dumps(
            {"format": "lifecase.windows-install.v1", "app": "LifeCase", "version": "0.1.0"}
        ),
        encoding="utf-8",
    )
    payload = _payload(tmp_path / "source", "updated")
    setup = tmp_path / "LifeCaseSetup.exe"
    setup.write_bytes(b"setup")
    monkeypatch.setattr(
        windows_installer,
        "_stop_installed_application",
        lambda root, *, data_root=None: None,
    )

    def fail_shortcut(root: Path, *, desktop: bool) -> None:
        raise OSError("shortcut failure")

    monkeypatch.setattr(
        windows_installer,
        "_create_shortcuts",
        fail_shortcut,
    )
    monkeypatch.setattr(windows_installer, "_remove_shortcuts", lambda: None)
    monkeypatch.setattr(windows_installer, "_unregister_uninstall", lambda: None)
    restored_versions: list[str] = []
    monkeypatch.setattr(
        windows_installer,
        "_register_uninstall",
        lambda root, *, version="0.2.0": restored_versions.append(version),
    )

    with pytest.raises(windows_installer.InstallerError, match="prior installation was restored"):
        windows_installer.install_application(
            root,
            programs_root=programs,
            payload_root=payload,
            setup_executable=setup,
        )

    assert (root / "LifeCase.exe").read_text(encoding="utf-8") == "old"
    assert not list(programs.glob(".LifeCase-*"))
    assert restored_versions == ["0.1.0"]


def test_uninstall_requires_marker_and_preserves_user_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    programs = tmp_path / "Programs"
    root = programs / "LifeCase"
    root.mkdir(parents=True)
    windows_installer._write_marker(root)
    data = tmp_path / "LifeCase-data"
    data.mkdir()
    database = data / "lifecase.sqlite3"
    database.write_bytes(b"retained")
    calls: list[str] = []
    monkeypatch.setattr(
        windows_installer,
        "_stop_installed_application",
        lambda root, *, data_root=None: None,
    )
    monkeypatch.setattr(windows_installer, "_remove_shortcuts", lambda: calls.append("shortcuts"))
    monkeypatch.setattr(
        windows_installer, "_unregister_uninstall", lambda: calls.append("registry")
    )

    windows_installer.uninstall_application(
        root,
        programs_root=programs,
        setup_executable=tmp_path / "downloaded-setup.exe",
    )

    assert not root.exists()
    assert database.read_bytes() == b"retained"
    assert calls == ["shortcuts", "registry"]


def test_install_root_must_be_direct_lifecase_child(tmp_path: Path) -> None:
    programs = tmp_path / "Programs"

    with pytest.raises(windows_installer.InstallerError):
        windows_installer.validate_install_root(
            programs / "nested" / "LifeCase",
            programs_root=programs,
        )
    with pytest.raises(windows_installer.InstallerError):
        windows_installer.validate_install_root(
            programs / "AnotherProduct",
            programs_root=programs,
        )
