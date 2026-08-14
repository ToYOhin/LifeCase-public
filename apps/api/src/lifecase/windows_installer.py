"""Per-user Windows installer for the self-contained LifeCase application."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import uuid
from argparse import SUPPRESS, ArgumentParser
from collections.abc import Mapping, Sequence
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import Any

from lifecase import __version__

_APP_NAME = "LifeCase"
_APP_EXE = "LifeCase.exe"
_UNINSTALL_EXE = "LifeCaseUninstall.exe"
_MARKER_NAME = ".lifecase-install.json"
_MARKER_FORMAT = "lifecase.windows-install.v1"
_UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{_APP_NAME}"


class InstallerError(RuntimeError):
    """Operator-safe installer failure."""


def default_programs_root(environment: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    local_app_data = values.get("LOCALAPPDATA")
    if not local_app_data:
        raise InstallerError("The per-user Windows application directory is unavailable.")
    return Path(local_app_data) / "Programs"


def default_install_root(environment: Mapping[str, str] | None = None) -> Path:
    return default_programs_root(environment) / _APP_NAME


def bundled_payload_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is None:
        raise InstallerError("The installer payload is unavailable. Rebuild LifeCaseSetup.exe.")
    return Path(str(frozen_root)) / "payload" / _APP_NAME


def validate_install_root(selected: Path, *, programs_root: Path) -> Path:
    """Allow only a direct, non-reparse child of the selected Programs root."""

    root = Path(os.path.abspath(selected))
    parent = Path(os.path.abspath(programs_root))
    if not selected.is_absolute() or os.path.normcase(str(root.parent)) != os.path.normcase(
        str(parent)
    ):
        raise InstallerError(
            "The install directory must be directly under the per-user Programs folder."
        )
    if not root.name.casefold().startswith(_APP_NAME.casefold()):
        raise InstallerError("The install directory name must start with LifeCase.")
    if root.exists() and (not root.is_dir() or _is_reparse_point(root)):
        raise InstallerError("The selected install directory is not a safe LifeCase directory.")
    if parent.exists() and (not parent.is_dir() or _is_reparse_point(parent)):
        raise InstallerError("The per-user Programs folder is not a safe directory.")
    return root


def install_application(
    install_root: Path,
    *,
    programs_root: Path,
    payload_root: Path,
    setup_executable: Path,
    desktop_shortcut: bool = True,
    launch: bool = True,
    data_root: Path | None = None,
) -> Path:
    """Stage and atomically install or update one marked application directory."""

    root = validate_install_root(install_root, programs_root=programs_root)
    _validate_payload(payload_root)
    if not setup_executable.is_file():
        raise InstallerError("The installer executable is unavailable.")
    previous_version: str | None = None
    if root.exists():
        previous_version = _read_marker(root)["version"]
        _stop_installed_application(root, data_root=data_root)

    programs_root.mkdir(parents=True, exist_ok=True)
    stage = programs_root / f".{root.name}-install-{uuid.uuid4().hex}"
    backup = programs_root / f".{root.name}-backup-{uuid.uuid4().hex}"
    moved_existing = False
    try:
        shutil.copytree(payload_root, stage)
        shutil.copy2(setup_executable, stage / _UNINSTALL_EXE)
        _write_marker(stage)
        if root.exists():
            os.replace(root, backup)
            moved_existing = True
        os.replace(stage, root)
    except (OSError, shutil.Error) as error:
        if moved_existing and backup.exists() and not root.exists():
            os.replace(backup, root)
        _safe_remove_stage(stage, programs_root=programs_root)
        raise InstallerError("LifeCase could not be installed safely.") from error

    try:
        _create_shortcuts(root, desktop=desktop_shortcut)
        _register_uninstall(root)
        if launch:
            _launch_application(root)
    except (OSError, subprocess.SubprocessError) as error:
        _rollback_integration_failure(
            root,
            backup=backup,
            programs_root=programs_root,
            restore_existing=moved_existing,
            previous_version=previous_version,
        )
        message = (
            "LifeCase Windows integration failed and the prior installation was restored."
            if moved_existing
            else "LifeCase Windows integration failed and the new installation was rolled back."
        )
        raise InstallerError(message) from error
    with suppress(OSError):
        _safe_remove_stage(backup, programs_root=programs_root)
    return root


def uninstall_application(
    install_root: Path,
    *,
    programs_root: Path,
    setup_executable: Path,
    data_root: Path | None = None,
) -> None:
    """Remove the installed program while preserving the separate user-data directory."""

    root = validate_install_root(install_root, programs_root=programs_root)
    _read_marker(root)
    _stop_installed_application(root, data_root=data_root)
    _remove_shortcuts()
    _unregister_uninstall()
    if _is_within(setup_executable, root):
        _schedule_remove_after_exit(root)
    else:
        shutil.rmtree(root)


def _validate_payload(payload_root: Path) -> None:
    if (
        not payload_root.is_dir()
        or _is_reparse_point(payload_root)
        or not (payload_root / _APP_EXE).is_file()
    ):
        raise InstallerError("The bundled LifeCase application payload is invalid.")


def _marker_value() -> dict[str, str]:
    return {"format": _MARKER_FORMAT, "app": _APP_NAME, "version": __version__}


def _write_marker(root: Path) -> None:
    (root / _MARKER_NAME).write_text(
        json.dumps(_marker_value(), ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_marker(root: Path) -> dict[str, str]:
    try:
        value: Any = json.loads((root / _MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise InstallerError(
            "The existing directory is not a marked LifeCase installation."
        ) from None
    if (
        not isinstance(value, dict)
        or value.get("format") != _MARKER_FORMAT
        or value.get("app") != _APP_NAME
        or not isinstance(value.get("version"), str)
    ):
        raise InstallerError("The existing directory is not a marked LifeCase installation.")
    return value


def _stop_installed_application(root: Path, *, data_root: Path | None = None) -> None:
    executable = root / _APP_EXE
    if not executable.is_file():
        return
    arguments = [str(executable), "--stop", "--no-dialog"]
    if data_root is not None:
        arguments.extend(("--data-root", str(data_root)))
    try:
        result = subprocess.run(
            arguments,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise InstallerError("Stop LifeCase before installing or uninstalling it.") from error
    if result.returncode != 0:
        raise InstallerError("Stop LifeCase before installing or uninstalling it.")


def _known_folder(csidl: int) -> Path:
    buffer = ctypes.create_unicode_buffer(260)
    result = _windows_dlls().shell32.SHGetFolderPathW(None, csidl, None, 0, buffer)
    if result != 0:
        raise OSError("Windows known folder lookup failed.")
    return Path(buffer.value)


def _shortcut_paths() -> tuple[Path, Path]:
    return _known_folder(0x02) / f"{_APP_NAME}.lnk", _known_folder(0x10) / f"{_APP_NAME}.lnk"


def _create_shortcut(shortcut: Path, target: Path, working_directory: Path) -> None:
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["LIFECASE_SHORTCUT_PATH"] = str(shortcut)
    environment["LIFECASE_SHORTCUT_TARGET"] = str(target)
    environment["LIFECASE_SHORTCUT_WORKING_DIRECTORY"] = str(working_directory)
    script = (
        "$shell=New-Object -ComObject WScript.Shell;"
        "$link=$shell.CreateShortcut($env:LIFECASE_SHORTCUT_PATH);"
        "$link.TargetPath=$env:LIFECASE_SHORTCUT_TARGET;"
        "$link.WorkingDirectory=$env:LIFECASE_SHORTCUT_WORKING_DIRECTORY;"
        "$link.IconLocation=$env:LIFECASE_SHORTCUT_TARGET;"
        "$link.Save()"
    )
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=True,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _create_shortcuts(root: Path, *, desktop: bool) -> None:
    start_menu, desktop_path = _shortcut_paths()
    _create_shortcut(start_menu, root / _APP_EXE, root)
    if desktop:
        _create_shortcut(desktop_path, root / _APP_EXE, root)
    elif desktop_path.exists():
        desktop_path.unlink()


def _remove_shortcuts() -> None:
    for shortcut in _shortcut_paths():
        with suppress(OSError):
            shortcut.unlink(missing_ok=True)


def _register_uninstall(root: Path, *, version: str = __version__) -> None:
    winreg: Any = import_module("winreg")

    estimated_size = sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) // 1024
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _UNINSTALL_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, _APP_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(root / _APP_EXE))
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(root))
        winreg.SetValueEx(
            key,
            "UninstallString",
            0,
            winreg.REG_SZ,
            f'"{root / _UNINSTALL_EXE}" --uninstall',
        )
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, estimated_size)


def _unregister_uninstall() -> None:
    winreg: Any = import_module("winreg")

    with suppress(FileNotFoundError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_KEY)


def _launch_application(root: Path) -> None:
    subprocess.Popen(
        [str(root / _APP_EXE)],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _schedule_remove_after_exit(root: Path) -> None:
    environment = os.environ.copy()
    environment["LIFECASE_UNINSTALL_PID"] = str(os.getpid())
    environment["LIFECASE_UNINSTALL_ROOT"] = str(root)
    script = (
        "$process=Get-Process -Id ([int]$env:LIFECASE_UNINSTALL_PID) "
        "-ErrorAction SilentlyContinue;"
        "if($null -ne $process){$process.WaitForExit()};"
        "for($attempt=0;$attempt -lt 150;$attempt++){"
        "try{Remove-Item -LiteralPath $env:LIFECASE_UNINSTALL_ROOT "
        "-Recurse -Force -ErrorAction Stop}catch{};"
        "if(-not (Test-Path -LiteralPath $env:LIFECASE_UNINSTALL_ROOT)){exit 0};"
        "Start-Sleep -Milliseconds 200"
        "};exit 1"
    )
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=environment,
    )


def _safe_remove_stage(path: Path, *, programs_root: Path) -> None:
    if not path.exists():
        return
    if path.parent != programs_root or not path.name.startswith(f".{_APP_NAME}"):
        raise InstallerError("Installer cleanup target was rejected.")
    shutil.rmtree(path)


def _rollback_integration_failure(
    root: Path,
    *,
    backup: Path,
    programs_root: Path,
    restore_existing: bool,
    previous_version: str | None,
) -> None:
    try:
        _read_marker(root)
        shutil.rmtree(root)
        if restore_existing and backup.exists():
            os.replace(backup, root)
            if previous_version is None:
                raise InstallerError("The prior LifeCase version marker is unavailable.")
            _register_uninstall(root, version=previous_version)
        else:
            with suppress(OSError):
                _remove_shortcuts()
            with suppress(OSError):
                _unregister_uninstall()
    except (OSError, shutil.Error, InstallerError) as error:
        raise InstallerError(
            "LifeCase installation failed and requires manual program-directory recovery."
        ) from error
    _safe_remove_stage(backup, programs_root=programs_root)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.stat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


def _windows_dlls() -> Any:
    return vars(ctypes)["windll"]


def _show_message(title: str, message: str, *, error: bool = False, confirm: bool = False) -> bool:
    flags = 0x10 if error else 0x40
    if confirm:
        flags = 0x24
    result = _windows_dlls().user32.MessageBoxW(None, message, title, flags)
    return not confirm or result == 6


def _parser() -> ArgumentParser:
    parser = ArgumentParser(description="Install LifeCase for the current Windows user.")
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--no-launch", action="store_true")
    parser.add_argument("--no-desktop-shortcut", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--install-root", type=Path, help=SUPPRESS)
    parser.add_argument("--data-root", type=Path, help=SUPPRESS)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    values = _parser().parse_args(sys.argv[1:] if arguments is None else arguments)
    programs_root = default_programs_root()
    root = values.install_root or programs_root / _APP_NAME
    action = "卸载" if values.uninstall else "安装"
    if not values.silent and not _show_message(
        f"LifeCase {action}",
        f"将为当前 Windows 用户{action} LifeCase。\n\n用户数据不会在卸载时删除。",
        confirm=True,
    ):
        return 2
    try:
        if values.uninstall:
            uninstall_application(
                root,
                programs_root=programs_root,
                setup_executable=Path(sys.executable),
                data_root=values.data_root,
            )
            message = "LifeCase 已卸载；用户数据已保留。"
        else:
            install_application(
                root,
                programs_root=programs_root,
                payload_root=bundled_payload_root(),
                setup_executable=Path(sys.executable),
                desktop_shortcut=not values.no_desktop_shortcut,
                launch=not values.no_launch,
                data_root=values.data_root,
            )
            message = "LifeCase 已安装，可以从桌面或开始菜单启动。"
    except InstallerError as error:
        if not values.silent:
            _show_message("LifeCase 安装失败", str(error), error=True)
        return 1
    if not values.silent:
        _show_message(f"LifeCase {action}", message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
