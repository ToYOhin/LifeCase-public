"""Real Windows-process coverage for short daily-use control-file locking.

The child imports lifecycle primitives only: it never starts an application,
restores credentials, opens user data, or creates a descendant process.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Any

import pytest

from lifecase import daily_use
from lifecase.daily_use import DailyUseError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex behavior")

_OWNER_SCRIPT = """
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from lifecase import daily_use

with daily_use._control_access(Path(sys.argv[2])):
    print("held", flush=True)
    command = sys.stdin.readline().strip()
    if command == "abandon":
        os._exit(0)
    if command != "release":
        raise RuntimeError("Unexpected synthetic test command")
print("released", flush=True)
"""


@contextmanager
def _other_process_holding(path: Path) -> Iterator[subprocess.Popen[str]]:
    """Wait for an explicit ownership handshake, and clean up only this child."""
    source_root = Path(daily_use.__file__).resolve().parents[1]
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-I", "-u", "-c", _OWNER_SCRIPT, str(source_root), str(path)],
        cwd=path.parent,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
        env={
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "LIFECASE_MODEL_PROVIDER": "disabled",
        },
    )
    assert process.stdout is not None
    ready: queue.Queue[str] = queue.Queue()
    output = process.stdout
    reader = threading.Thread(target=lambda: ready.put(output.readline()), daemon=True)
    reader.start()
    try:
        assert ready.get(timeout=10).strip() == "held"
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        reader.join(timeout=1)
        if process.stdin is not None:
            process.stdin.close()
        process.stdout.close()


def _finish_owner(process: subprocess.Popen[str], command: str) -> None:
    assert process.stdin is not None
    process.stdin.write(command + "\n")
    process.stdin.flush()
    assert process.wait(timeout=10) == 0


@contextmanager
def _observe_wait(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """Signal once the waiter has a real handle; keep the real kernel wait."""
    waiting = threading.Event()
    original_library = ctypes.WinDLL

    def load_library(*args: Any, **kwargs: Any) -> Any:
        library = original_library(*args, **kwargs)
        original_wait = library.WaitForSingleObject
        original_wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        original_wait.restype = wintypes.DWORD

        def wait(handle: Any, milliseconds: int) -> int:
            waiting.set()
            return int(original_wait(handle, milliseconds))

        library.WaitForSingleObject = wait
        return library

    with monkeypatch.context() as patch:
        patch.setattr(ctypes, "WinDLL", load_library)
        yield waiting


def test_control_writer_waits_for_same_runtime_without_blocking_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "first-runtime"
    other_runtime = tmp_path / "second-runtime"
    runtime.mkdir()
    other_runtime.mkdir()
    reader_path = runtime / "daily-use-state.json"
    writer_path = runtime / "daily-use-stop.json"
    other_path = other_runtime / "daily-use-stop.json"
    writer_path.write_text('{"before":true}\n', encoding="utf-8")
    before = writer_path.read_bytes()

    with _other_process_holding(reader_path) as owner:
        # A zero-wait probe establishes that different control filenames in the
        # same directory really contend; thread scheduling cannot fake that proof.
        with monkeypatch.context() as patch:
            patch.setattr(daily_use, "_CONTROL_WAIT_MS", 0)
            with pytest.raises(DailyUseError, match="control") as blocked:
                daily_use._atomic_json(writer_path, {"after": True})
            assert blocked.value.code == "runtime_control_busy"

        with _observe_wait(monkeypatch) as waiting, ThreadPoolExecutor(max_workers=1) as pool:
            write = pool.submit(daily_use._atomic_json, writer_path, {"after": True})
            assert waiting.wait(timeout=5)
            assert not write.done()
            daily_use._atomic_json(other_path, {"independent": True})
            assert json.loads(other_path.read_text(encoding="utf-8")) == {"independent": True}
            assert writer_path.read_bytes() == before
            assert not write.done()
            _finish_owner(owner, "release")
            write.result(timeout=10)

    assert json.loads(writer_path.read_text(encoding="utf-8")) == {"after": True}
    assert list(runtime.glob("*.tmp")) == []
    assert list(other_runtime.glob("*.tmp")) == []


@pytest.mark.parametrize("operation", ["replace", "remove"])
def test_control_timeout_keeps_file_intact_and_recovers_after_owner_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    path = tmp_path / "daily-use-state.json"
    path.write_text('{"untouched":true}\n', encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(daily_use, "_CONTROL_WAIT_MS", 0)

    def operate() -> None:
        if operation == "replace":
            daily_use._atomic_json(path, {"recovered": True})
        else:
            daily_use._remove_control_file(path)

    with _other_process_holding(path) as owner:
        with pytest.raises(DailyUseError) as error:
            operate()
        assert error.value.code == "runtime_control_busy"
        assert error.value.retryable
        assert path.read_bytes() == before
        assert list(tmp_path.iterdir()) == [path]
        _finish_owner(owner, "release")

    operate()
    if operation == "replace":
        assert json.loads(path.read_text(encoding="utf-8")) == {"recovered": True}
    else:
        assert not path.exists()


def test_control_exception_releases_ownership_before_thread_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "daily-use-state.json"
    monkeypatch.setattr(daily_use, "_CONTROL_WAIT_MS", 0)

    def interrupted_operation() -> None:
        with daily_use._control_access(path):
            raise ValueError("synthetic operation failed")

    with ThreadPoolExecutor(max_workers=1) as pool:
        failed = pool.submit(interrupted_operation)
        with pytest.raises(ValueError, match="synthetic operation failed"):
            failed.result(timeout=5)
        # The worker remains alive. A missing release cannot be hidden by Windows
        # automatically abandoning the mutex when its owning thread terminates.
        daily_use._atomic_json(path, {"recovered": True})
        assert json.loads(path.read_text(encoding="utf-8")) == {"recovered": True}
        daily_use._remove_control_file(path)
        assert not path.exists()


def test_abandoned_control_fails_without_writing_then_releases_for_next_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "daily-use-state.json"
    path.write_text('{"untouched":true}\n', encoding="utf-8")
    before = path.read_bytes()

    with (
        _other_process_holding(path) as owner,
        _observe_wait(monkeypatch) as waiting,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        write = pool.submit(daily_use._atomic_json, path, {"must_not_commit": True})
        assert waiting.wait(timeout=5)
        assert not write.done()
        # The waiter already has a real handle, keeping the mutex object alive
        # when its owner exits without executing Python cleanup.
        _finish_owner(owner, "abandon")
        with pytest.raises(DailyUseError) as error:
            write.result(timeout=10)
        assert error.value.code == "runtime_control_interrupted"
        assert error.value.retryable
        assert path.read_bytes() == before
        assert list(tmp_path.iterdir()) == [path]

        # WAIT_ABANDONED grants ownership even though the command fails. The
        # worker is still alive here, so this checks the failure-path release.
        monkeypatch.setattr(daily_use, "_CONTROL_WAIT_MS", 0)
        daily_use._atomic_json(path, {"recovered": True})
        assert json.loads(path.read_text(encoding="utf-8")) == {"recovered": True}
