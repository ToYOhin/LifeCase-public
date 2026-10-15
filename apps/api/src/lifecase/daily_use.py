"""Safe Windows daily-use runtime selection and lifecycle primitives."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from argparse import ArgumentParser
from asyncio import CancelledError
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, TypedDict, cast

from lifecase.config import ApplicationConfig, portfolio_agent_demo_enabled
from lifecase.providers import ModelProviderConfig

_MARKER_NAME = ".lifecase-runtime.json"
_MARKER_FORMAT = "lifecase.windows-daily-use.v1"
_REQUIRED_DIRECTORIES = ("data", "uploads", "runtime")
_STATE_NAME = "daily-use-state.json"
_STOP_NAME = "daily-use-stop.json"
_ERROR_NAME = "daily-use-last-error.json"
_LOG_NAME = "daily-use.log"
_START_LOCK_NAME = "daily-use-start.lock"
_STATE_FORMAT = "lifecase.daily-use-state.v1"
_START_LOCK_FORMAT = "lifecase.daily-use-start-lock.v1"
_CONTROL_WAIT_MS = 5000


class _RuntimeState(TypedDict):
    format: str
    pid: int
    port: int
    status: str
    token: str


class DailyUseError(RuntimeError):
    """Typed, path-free operator failure for the Windows daily-use entrypoint."""

    def __init__(
        self,
        code: str,
        message: str,
        action: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.action = action
        self.retryable = retryable

    def as_dict(self) -> dict[str, object]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "action": self.action,
                "retryable": self.retryable,
            }
        }


def default_runtime_root(environment: dict[str, str] | None = None) -> Path:
    """Return the explicit per-user Windows default without creating it."""

    values = os.environ if environment is None else environment
    local_app_data = values.get("LOCALAPPDATA")
    if not local_app_data:
        raise DailyUseError(
            "data_root_default_unavailable",
            "The safe per-user data-directory default is unavailable.",
            "Pass an absolute path with -DataRoot.",
        )
    return Path(local_app_data) / "LifeCase"


def initialize_runtime(selected: Path, *, program_root: Path) -> Path:
    """Create one marked runtime or validate an existing marked runtime."""

    root = _validated_path(selected, program_root=program_root)
    if root.exists():
        return validate_runtime(root, program_root=program_root)
    if not _probe_writable(root.parent):
        raise DailyUseError(
            "data_root_not_writable",
            "The selected data directory cannot be created safely.",
            "Choose a writable per-user directory and run setup again.",
        )

    created_root = False
    try:
        root.mkdir()
        created_root = True
        _write_marker(root / _MARKER_NAME)
        for name in _REQUIRED_DIRECTORIES:
            (root / name).mkdir()
    except DailyUseError:
        _rollback_new_root(root, created_root)
        raise
    except OSError:
        _rollback_new_root(root, created_root)
        raise DailyUseError(
            "data_root_not_writable",
            "The selected data directory could not be initialized.",
            "Choose a writable empty directory and run setup again.",
        ) from None
    return root


def validate_runtime(selected: Path, *, program_root: Path) -> Path:
    """Validate marker, layout, containment, and writability without repair."""

    root = _validated_path(selected, program_root=program_root)
    if not root.is_dir() or root.is_symlink() or _is_junction(root):
        raise _conflict_error()
    marker = root / _MARKER_NAME
    try:
        value: Any = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise _conflict_error() from None
    if value != _marker_value():
        raise _conflict_error()
    for name in _REQUIRED_DIRECTORIES:
        child = root / name
        if not child.is_dir() or child.is_symlink() or _is_junction(child):
            raise _conflict_error()
    if not _probe_writable(root / "runtime"):
        raise DailyUseError(
            "data_root_not_writable",
            "The selected LifeCase data directory is not writable.",
            "Restore write access or choose another marked LifeCase directory.",
        )
    return root


def runtime_config(selected: Path, *, program_root: Path) -> ApplicationConfig:
    """Build the package configuration with model access forcibly disabled."""

    root = validate_runtime(selected, program_root=program_root)
    try:
        demo_enabled = portfolio_agent_demo_enabled(os.environ)
    except ValueError as error:
        raise DailyUseError(
            "agent_demo_configuration_invalid",
            "The Portfolio Agent Demo setting is invalid.",
            "Use true or false, then retry start.",
        ) from error
    return ApplicationConfig(
        database_path=root / "lifecase.sqlite3",
        data_root=root / "data",
        upload_root=root / "uploads",
        model_provider=ModelProviderConfig(),
        portfolio_agent_demo_enabled=demo_enabled,
    )


def start_runtime(
    selected: Path,
    *,
    program_root: Path,
    static_root: Path,
    port: int,
    timeout_seconds: float = 20.0,
) -> dict[str, object]:
    """Start one detached, single-process loopback runtime and verify readiness."""

    root = validate_runtime(selected, program_root=program_root)
    _validate_port(port)
    _preflight_static(root, program_root, static_root)
    state_path, stop_path, error_path, log_path = _runtime_paths(root)
    start_lock = _acquire_start_lock(root / "runtime" / _START_LOCK_NAME)
    try:
        with _control_access(state_path):
            prior = _read_state(state_path, required=False)
            if prior is not None and _process_exists(int(prior["pid"])):
                raise DailyUseError(
                    "already_running",
                    "LifeCase is already running for this data directory.",
                    "Use status or stop before starting another process.",
                )
            _remove_runtime_control_files(state_path, stop_path)
            _remove_control_file(error_path)
        _require_port_available(port)

        command = [
            *_server_command_prefix(),
            "--data-root",
            str(root),
            "--program-root",
            str(program_root),
            "--static-root",
            str(static_root),
            "--port",
            str(port),
        ]
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        try:
            with log_path.open("ab", buffering=0) as log:
                process = subprocess.Popen(  # noqa: S603
                    command,
                    cwd=program_root,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    close_fds=True,
                    creationflags=flags,
                    env={
                        **os.environ,
                        "PYTHONUTF8": "1",
                        "LIFECASE_MODEL_PROVIDER": "disabled",
                    },
                )
        except OSError:
            raise DailyUseError(
                "process_start_failed",
                "The LifeCase process could not be started.",
                "Run setup again, then retry start.",
                retryable=True,
            ) from None

        try:
            return _wait_until_ready(
                process.pid,
                port,
                state_path,
                error_path,
                timeout_seconds=timeout_seconds,
            )
        except DailyUseError:
            _stop_spawned_process(process.pid, state_path, stop_path, timeout_seconds=5.0)
            raise
    finally:
        _release_start_lock(root / "runtime" / _START_LOCK_NAME, start_lock)


def stop_runtime(
    selected: Path,
    *,
    program_root: Path,
    timeout_seconds: float = 15.0,
) -> dict[str, object]:
    """Request graceful shutdown, then clean only transient runtime control state."""

    root = validate_runtime(selected, program_root=program_root)
    state_path, stop_path, _, _ = _runtime_paths(root)
    with _control_access(state_path):
        state = _read_state(state_path, required=False)
        if state is None:
            _remove_control_file(stop_path)
            return {"status": "stopped", "data_retained": True}
        pid = int(state["pid"])
        if not _process_exists(pid):
            _remove_runtime_control_files(state_path, stop_path)
            return {"status": "stopped", "data_retained": True}

        _atomic_json(stop_path, {"token": state["token"]})
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and _process_exists(pid):
        time.sleep(0.1)
    if _process_exists(pid):
        _terminate_process(pid)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and _process_exists(pid):
            time.sleep(0.1)
    if _process_exists(pid):
        raise DailyUseError(
            "process_stop_failed",
            "The LifeCase process did not stop cleanly.",
            "Close the local process manually before retrying start.",
            retryable=True,
        )
    _remove_owned_control_files(state_path, stop_path, pid=pid, token=state["token"])
    return {"status": "stopped", "data_retained": True}


def runtime_status(selected: Path, *, program_root: Path) -> dict[str, object]:
    root = validate_runtime(selected, program_root=program_root)
    state_path, stop_path, _, _ = _runtime_paths(root)
    with _control_access(state_path):
        state = _read_state(state_path, required=False)
        if state is None or not _process_exists(int(state["pid"])):
            _remove_runtime_control_files(state_path, stop_path)
            return {"status": "stopped", "data_retained": True}
        return {
            "status": state["status"],
            "pid": state["pid"],
            "url": f"http://127.0.0.1:{state['port']}",
            "data_retained": True,
        }


def _validated_path(selected: Path, *, program_root: Path) -> Path:
    candidate = selected.expanduser()
    if not candidate.is_absolute():
        raise DailyUseError(
            "data_root_not_absolute",
            "The data directory must be an absolute path.",
            "Choose an absolute per-user data directory.",
        )
    lexical = Path(os.path.abspath(candidate))
    if _contains_link(lexical):
        raise DailyUseError(
            "data_root_symlink",
            "The data directory cannot contain symbolic-link or junction components.",
            "Choose a normal local directory and run the command again.",
        )
    try:
        root = lexical.resolve(strict=False)
        program = program_root.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise DailyUseError(
            "data_root_unsafe",
            "The data directory could not be normalized safely.",
            "Choose a normal local per-user directory.",
        ) from None
    if root == Path(root.anchor) or root == program or root.is_relative_to(program):
        raise DailyUseError(
            "data_root_unsafe",
            "The selected root or program directory cannot store LifeCase data.",
            "Choose a dedicated per-user directory outside the program folder.",
        )
    return root


def _contains_link(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.exists() and (current.is_symlink() or _is_junction(current)):
            return True
    return False


def _is_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _probe_writable(path: Path) -> bool:
    try:
        with tempfile.NamedTemporaryFile(prefix=".lifecase-write-probe-", dir=path):
            pass
    except OSError:
        return False
    return True


def _write_marker(path: Path) -> None:
    content = json.dumps(_marker_value(), sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        raise DailyUseError(
            "data_root_not_writable",
            "The LifeCase data marker could not be created.",
            "Choose a writable empty directory and run setup again.",
        ) from None


def _marker_value() -> dict[str, object]:
    return {
        "application": "LifeCase",
        "format": _MARKER_FORMAT,
    }


def _conflict_error() -> DailyUseError:
    return DailyUseError(
        "data_root_conflict",
        "The selected directory is not an intact LifeCase daily-use data directory.",
        "Choose a new path for setup or select an existing marked LifeCase directory.",
    )


def _rollback_new_root(root: Path, created_root: bool) -> None:
    if not created_root:
        return
    for name in reversed(_REQUIRED_DIRECTORIES):
        with suppress(OSError):
            (root / name).rmdir()
    with suppress(OSError):
        (root / _MARKER_NAME).unlink(missing_ok=True)
        root.rmdir()


def _preflight_static(root: Path, program_root: Path, static_root: Path) -> None:
    from lifecase.main import StaticAssetsError, create_app

    try:
        create_app(
            runtime_config(root, program_root=program_root),
            static_root=static_root,
            prepared=True,
        )
    except StaticAssetsError:
        raise DailyUseError(
            "static_assets_missing",
            "Built Web assets are missing or incomplete.",
            "Run the package setup or upgrade command, then retry start.",
        ) from None


def _runtime_paths(root: Path) -> tuple[Path, Path, Path, Path]:
    runtime = root / "runtime"
    return (
        runtime / _STATE_NAME,
        runtime / _STOP_NAME,
        runtime / _ERROR_NAME,
        runtime / _LOG_NAME,
    )


def _validate_port(port: int) -> None:
    if isinstance(port, bool) or not 1 <= port <= 65535:
        raise DailyUseError(
            "port_invalid",
            "The loopback port must be between 1 and 65535.",
            "Choose an unused local TCP port.",
        )


def _server_command_prefix() -> list[str]:
    """Return the source or frozen command that owns the detached server."""

    if bool(getattr(sys, "frozen", False)):
        return [sys.executable, "_serve"]
    return [sys.executable, "-m", "lifecase.daily_use", "_serve"]


def _require_port_available(port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
    except OSError:
        raise DailyUseError(
            "port_in_use",
            "The selected loopback port is unavailable.",
            "Stop the other local service or choose another port.",
            retryable=True,
        ) from None


def _wait_until_ready(
    pid: int,
    port: int,
    state_path: Path,
    error_path: Path,
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    base_url = f"http://127.0.0.1:{port}"
    while time.monotonic() < deadline:
        error = _read_error(error_path)
        if error is not None:
            raise error
        if not _process_exists(pid):
            raise DailyUseError(
                "startup_failed",
                "LifeCase stopped before readiness checks completed.",
                "Review the safe startup code, correct the issue, and retry start.",
                retryable=True,
            )
        state = _read_state(state_path, required=False)
        if state is not None and state["status"] == "running":
            health = _get_json(f"{base_url}/api/v1/system/health")
            static_ok = _get_text_ok(f"{base_url}/")
            if health.get("status") == "ok" and static_ok:
                capabilities = _get_json(f"{base_url}/api/v1/system/capabilities")
                return {
                    "status": "running",
                    "pid": pid,
                    "url": base_url,
                    "capabilities": capabilities,
                }
        time.sleep(0.1)
    raise DailyUseError(
        "health_check_failed",
        "LifeCase did not pass API and Web readiness checks in time.",
        "Retry start; if the failure repeats, run setup and choose another port.",
        retryable=True,
    )


def _get_json(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:  # noqa: S310
            if response.status != 200:
                return {}
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.URLError):
        return {}
    return value if isinstance(value, dict) else {}


def _get_text_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:  # noqa: S310
            return response.status == 200 and bool(response.read(1024))
    except (OSError, urllib.error.URLError):
        return False


@contextmanager
def _control_access(path: Path) -> Iterator[None]:
    """Serialize short control-file access across this runtime's Windows processes."""
    if sys.platform != "win32":
        yield
        return

    import ctypes
    from ctypes import wintypes

    # A normal Windows reader prevents os.replace/unlink while its handle is
    # open. Coordinate our readers and writers, without changing atomic replace
    # or retrying an uncooperative external holder. No process waits belong here.
    identity = os.path.normcase(str(path.parent.resolve())).encode("utf-8")
    name = "Global\\LifeCase.DailyUse.Control." + hashlib.sha256(identity).hexdigest()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel32.WaitForSingleObject(handle, _CONTROL_WAIT_MS)
        acquired = result in (0, 0x80)  # WAIT_OBJECT_0 / WAIT_ABANDONED both grant ownership.
        if result in (0x80, 0x102):
            raise DailyUseError(
                "runtime_control_interrupted" if result == 0x80 else "runtime_control_busy",
                "LifeCase runtime control is interrupted or still in use.",
                "Keep the data intact; check status before retrying the command.",
                retryable=True,
            )
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _read_control_text(path: Path) -> str:
    with _control_access(path):
        return path.read_text(encoding="utf-8")


def _remove_control_file(path: Path) -> None:
    with _control_access(path):
        path.unlink(missing_ok=True)


def _read_state(path: Path, *, required: bool) -> _RuntimeState | None:
    try:
        value = json.loads(_read_control_text(path))
    except FileNotFoundError:
        if not required:
            return None
        raise DailyUseError(
            "runtime_state_invalid",
            "The transient LifeCase runtime state is invalid.",
            "Run stop to clear stale runtime state, then retry start.",
        ) from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise DailyUseError(
            "runtime_state_invalid",
            "The transient LifeCase runtime state is invalid.",
            "Run stop to clear stale runtime state, then retry start.",
        ) from None
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "pid", "port", "status", "token"}
        or value.get("format") != _STATE_FORMAT
        or not isinstance(value.get("pid"), int)
        or not isinstance(value.get("port"), int)
        or value.get("status") not in {"starting", "running", "stopping"}
        or not isinstance(value.get("token"), str)
        or len(str(value["token"])) != 64
    ):
        raise DailyUseError(
            "runtime_state_invalid",
            "The transient LifeCase runtime state is invalid.",
            "Run stop to clear stale runtime state, then retry start.",
        )
    return cast(_RuntimeState, value)


def _read_error(path: Path) -> DailyUseError | None:
    try:
        value = json.loads(_read_control_text(path))["error"]
        return DailyUseError(
            str(value["code"]),
            str(value["message"]),
            str(value["action"]),
            retryable=bool(value["retryable"]),
        )
    except FileNotFoundError:
        return None
    except (KeyError, OSError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return DailyUseError(
            "startup_failed",
            "LifeCase startup failed without a valid safe error record.",
            "Run setup again, then retry start.",
            retryable=True,
        )


def _atomic_json(path: Path, value: object) -> None:
    with _control_access(path):
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as output:
                json.dump(value, output, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _create_state(path: Path, value: object) -> None:
    with _control_access(path):
        try:
            with path.open("x", encoding="utf-8", newline="\n") as output:
                json.dump(value, output, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError:
            raise DailyUseError(
                "already_running",
                "LifeCase runtime state already exists.",
                "Run status or stop before retrying start.",
            ) from None


def _acquire_start_lock(path: Path, *, retry_stale: bool = True) -> str:
    with _control_access(path):
        token = secrets.token_hex(32)
        value = {"format": _START_LOCK_FORMAT, "pid": os.getpid(), "token": token}
        try:
            with path.open("x", encoding="utf-8", newline="\n") as output:
                json.dump(value, output, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            return token
        except FileExistsError:
            try:
                if path.is_symlink() or not path.is_file():
                    raise ValueError
                current = json.loads(path.read_text(encoding="utf-8"))
                if (
                    not isinstance(current, dict)
                    or current.get("format") != _START_LOCK_FORMAT
                    or not isinstance(current.get("pid"), int)
                    or not isinstance(current.get("token"), str)
                    or len(current["token"]) != 64
                ):
                    raise ValueError
            except (OSError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
                raise DailyUseError(
                    "startup_lock_invalid",
                    "The transient LifeCase startup lock is invalid.",
                    "Keep the data intact; inspect the local LifeCase process before retrying.",
                ) from None
            if _process_exists(int(current["pid"])):
                raise DailyUseError(
                    "startup_in_progress",
                    "Another LifeCase startup is already in progress.",
                    "Wait for that startup to finish, then use status before retrying.",
                    retryable=True,
                ) from None
            if not retry_stale:
                raise DailyUseError(
                    "startup_lock_invalid",
                    "The transient LifeCase startup lock could not be recovered safely.",
                    "Keep the data intact; inspect the local LifeCase process before retrying.",
                ) from None
            path.unlink()
            return _acquire_start_lock(path, retry_stale=False)


def _release_start_lock(path: Path, token: str) -> None:
    with _control_access(path):
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(current, dict) and current.get("token") == token:
                path.unlink()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return


def _remove_runtime_control_files(state_path: Path, stop_path: Path) -> None:
    with _control_access(state_path):
        state_path.unlink(missing_ok=True)
        stop_path.unlink(missing_ok=True)


def _remove_owned_control_files(state_path: Path, stop_path: Path, *, pid: int, token: str) -> None:
    with _control_access(state_path):
        current = _read_state(state_path, required=False)
        if current is not None and current["pid"] == pid and current["token"] == token:
            _remove_runtime_control_files(state_path, stop_path)


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            reaped_pid, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        else:
            if reaped_pid == pid:
                return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
            exit_code.value == 259
        )
    finally:
        kernel32.CloseHandle(handle)


def _terminate_process(pid: int) -> None:
    if sys.platform != "win32":
        with suppress(OSError):
            os.kill(pid, signal.SIGTERM)
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x0001, False, pid)
    if handle:
        try:
            kernel32.TerminateProcess(handle, 1)
        finally:
            kernel32.CloseHandle(handle)


def _stop_spawned_process(
    pid: int,
    state_path: Path,
    stop_path: Path,
    *,
    timeout_seconds: float,
) -> None:
    state = None
    # A busy/corrupt control file must not skip stopping the child we just
    # created. Only the known spawned PID is eligible for this fallback.
    with suppress(DailyUseError, OSError), _control_access(state_path):
        state = _read_state(state_path, required=False)
        if state is not None and state["pid"] == pid:
            _atomic_json(stop_path, {"token": state["token"]})
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and _process_exists(pid):
        time.sleep(0.1)
    if _process_exists(pid):
        _terminate_process(pid)
    if state is not None and state["pid"] == pid:
        with suppress(DailyUseError, OSError):
            _remove_owned_control_files(state_path, stop_path, pid=pid, token=state["token"])


def _serve(selected: Path, program_root: Path, static_root: Path, port: int) -> int:
    import asyncio
    import sqlite3

    import uvicorn

    from lifecase.main import StaticAssetsError, create_app, prepare_application
    from lifecase.storage import DatabaseConfigurationError, MigrationError
    from lifecase.storage.backup import BackupRestoreError

    root = validate_runtime(selected, program_root=program_root)
    state_path, stop_path, error_path, _ = _runtime_paths(root)
    token = secrets.token_hex(32)
    state: _RuntimeState = {
        "format": _STATE_FORMAT,
        "pid": os.getpid(),
        "port": port,
        "status": "starting",
        "token": token,
    }
    try:
        _create_state(state_path, state)
        config = runtime_config(root, program_root=program_root)
        prepare_application(config)
        application = create_app(
            config,
            static_root=static_root,
            prepared=True,
            persistent_ai_settings=True,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                host="127.0.0.1",
                port=port,
                workers=1,
                access_log=False,
                log_config=None,
            )
        )
        asyncio.run(_serve_and_watch(server, state_path, stop_path, state, token))
        if not server.started and not server.should_exit:
            raise DailyUseError(
                "startup_failed",
                "LifeCase stopped before becoming ready.",
                "Correct the startup issue and retry start.",
                retryable=True,
            )
        return 0
    except StaticAssetsError:
        error = DailyUseError(
            "static_assets_missing",
            "Built Web assets are missing or incomplete.",
            "Run the package setup or upgrade command, then retry start.",
        )
    except BackupRestoreError:
        error = DailyUseError(
            "pre_migration_backup_failed",
            "A verified pre-migration Backup could not be created.",
            "Keep the existing data unchanged, repair Backup storage, and retry start.",
            retryable=True,
        )
    except MigrationError:
        error = DailyUseError(
            "migration_failed",
            "The local database migration check failed safely.",
            "Do not replace the data directory; restore the prior build or a verified Backup.",
        )
    except DatabaseConfigurationError:
        error = DailyUseError(
            "sqlite_capability_unavailable",
            "Required SQLite, FTS5, or trigram capability is unavailable.",
            "Run setup with the locked Python runtime, then retry start.",
        )
    except sqlite3.Error:
        error = DailyUseError(
            "database_startup_failed",
            "The local database could not be opened safely.",
            "Keep the data directory unchanged and recover from a verified Backup if needed.",
        )
    except (DailyUseError, OSError, ValueError, CancelledError) as caught:
        error = (
            caught
            if isinstance(caught, DailyUseError)
            else DailyUseError(
                "startup_failed",
                "LifeCase startup failed safely.",
                "Correct the reported local configuration issue and retry start.",
                retryable=True,
            )
        )
    with suppress(DailyUseError, OSError):
        _remove_owned_control_files(state_path, stop_path, pid=state["pid"], token=token)
    _atomic_json(error_path, error.as_dict())
    return 1


async def _serve_and_watch(
    server: Any,
    state_path: Path,
    stop_path: Path,
    state: _RuntimeState,
    token: str,
) -> None:
    import asyncio

    async def watch() -> None:
        marked_running = False
        while not server.should_exit:
            if server.started and not marked_running:
                state["status"] = "running"
                _atomic_json(state_path, state)
                marked_running = True
            if _valid_stop_request(stop_path, token):
                state["status"] = "stopping"
                _atomic_json(state_path, state)
                server.should_exit = True
                return
            await asyncio.sleep(0.1)

    serve_task = asyncio.create_task(server.serve())
    watch_task = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait((serve_task, watch_task), return_when=asyncio.FIRST_COMPLETED)
        if serve_task in done:
            server.should_exit = True
        await asyncio.gather(serve_task, watch_task)
    finally:
        _remove_owned_control_files(state_path, stop_path, pid=state["pid"], token=token)


def _valid_stop_request(path: Path, token: str) -> bool:
    try:
        value = json.loads(_read_control_text(path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and value == {"token": token}


def _parser() -> ArgumentParser:
    parser = ArgumentParser(description="LifeCase Windows daily-use package")
    parser.add_argument(
        "command",
        choices=("setup", "start", "stop", "status", "upgrade", "uninstall", "_serve"),
    )
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--program-root", required=True, type=Path)
    parser.add_argument("--static-root", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(arguments: list[str] | None = None) -> int:
    values = _parser().parse_args(arguments)
    try:
        if values.command == "setup":
            initialize_runtime(values.data_root, program_root=values.program_root)
            result: dict[str, object] = {"status": "ready", "data_retained": True}
        elif values.command == "start":
            if values.static_root is None:
                raise DailyUseError(
                    "static_assets_missing",
                    "The built Web directory was not supplied.",
                    "Run the package setup command, then retry start.",
                )
            result = start_runtime(
                values.data_root,
                program_root=values.program_root,
                static_root=values.static_root,
                port=values.port,
            )
        elif values.command == "stop":
            result = stop_runtime(values.data_root, program_root=values.program_root)
        elif values.command == "status":
            result = runtime_status(values.data_root, program_root=values.program_root)
        elif values.command == "upgrade":
            validate_runtime(values.data_root, program_root=values.program_root)
            if runtime_status(values.data_root, program_root=values.program_root)["status"] != (
                "stopped"
            ):
                raise DailyUseError(
                    "upgrade_requires_stop",
                    "LifeCase must be stopped before package upgrade.",
                    "Run stop, then retry upgrade; user data will be retained.",
                )
            result = {"status": "ready_for_restart", "data_retained": True}
        elif values.command == "uninstall":
            stop_runtime(values.data_root, program_root=values.program_root)
            result = {"status": "uninstalled", "data_retained": True}
        else:
            if values.static_root is None:
                raise DailyUseError(
                    "static_assets_missing",
                    "The built Web directory was not supplied.",
                    "Run the package setup command, then retry start.",
                )
            return _serve(
                values.data_root,
                values.program_root,
                values.static_root,
                values.port,
            )
    except OSError:
        error = DailyUseError(
            "runtime_control_failed",
            "LifeCase could not update its transient local control state safely.",
            "Keep the data intact, stop any local LifeCase process, and retry.",
            retryable=True,
        )
        print(json.dumps(error.as_dict(), ensure_ascii=True, sort_keys=True))
        return 1
    except DailyUseError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=True, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
