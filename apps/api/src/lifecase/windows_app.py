"""Double-click Windows entry for the frozen LifeCase application."""

from __future__ import annotations

import json
import multiprocessing
import sys
import webbrowser
from argparse import ArgumentParser
from collections.abc import Sequence
from pathlib import Path

from lifecase.daily_use import (
    DailyUseError,
    default_runtime_root,
    initialize_runtime,
    runtime_status,
    start_runtime,
    stop_runtime,
)
from lifecase.daily_use import (
    main as daily_use_main,
)

_DEFAULT_PORT = 8765


def program_root() -> Path:
    """Return the immutable installed-program root."""

    if bool(getattr(sys, "frozen", False)):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[4]


def static_root() -> Path:
    """Return the built Web assets in source or frozen layouts."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        return Path(str(frozen_root)) / "web"
    return program_root() / "apps" / "web" / "dist"


def _parser() -> ArgumentParser:
    parser = ArgumentParser(description="LifeCase Windows application")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-dialog", action="store_true", help="Suppress operator dialogs.")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    return parser


def _show_message(title: str, message: str, *, error: bool = False) -> None:
    if sys.platform == "win32":
        import ctypes

        icon = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, icon)
        return
    print(f"{title}: {message}")


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True))


def main(arguments: Sequence[str] | None = None) -> int:
    """Initialize, start, and open the installed local application."""

    args = list(sys.argv[1:] if arguments is None else arguments)
    if args and args[0] == "_serve":
        return daily_use_main(args)

    values = _parser().parse_args(args)
    root = values.data_root or default_runtime_root()
    installed_root = program_root()
    try:
        if values.stop:
            if not root.exists():
                result: dict[str, object] = {"status": "stopped", "data_retained": True}
            else:
                result = stop_runtime(root, program_root=installed_root)
        elif values.status:
            if not root.exists():
                result = {"status": "not_configured", "data_retained": True}
            else:
                result = runtime_status(root, program_root=installed_root)
            if not values.no_dialog:
                _show_message("LifeCase 状态", f"当前状态：{result['status']}")
        else:
            if not root.exists():
                initialize_runtime(root, program_root=installed_root)
            current = runtime_status(root, program_root=installed_root)
            if current["status"] == "running":
                result = current
            else:
                result = start_runtime(
                    root,
                    program_root=installed_root,
                    static_root=static_root(),
                    port=values.port,
                )
            if not values.no_browser:
                webbrowser.open(str(result["url"]), new=2)
    except DailyUseError as error:
        if not values.no_dialog:
            _show_message(
                "LifeCase 启动失败",
                f"{error.message}\n\n处理建议：{error.action}",
                error=True,
            )
        _emit(error.as_dict())
        return 1
    except OSError:
        launch_error = DailyUseError(
            "windows_launcher_failed",
            "LifeCase could not complete the local Windows launch safely.",
            "Keep the data directory unchanged, then retry or reinstall LifeCase.",
            retryable=True,
        )
        if not values.no_dialog:
            _show_message(
                "LifeCase 启动失败",
                f"{launch_error.message}\n\n处理建议：{launch_error.action}",
                error=True,
            )
        _emit(launch_error.as_dict())
        return 1
    _emit(result)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
