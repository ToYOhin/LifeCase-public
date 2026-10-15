"""Real Windows handle overlap, without timing loops or a running application."""

import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from lifecase import daily_use


def _state(token: str = "a" * 64) -> dict[str, object]:
    return {
        "format": "lifecase.daily-use-state.v1",
        "pid": os.getpid(),
        "port": 8765,
        "status": "starting",
        "token": token,
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file-sharing regression")
@pytest.mark.parametrize("kind", ["state", "stop", "error", "start-lock"])
@pytest.mark.parametrize("operation", ["replace", "remove"])
def test_control_operations_wait_for_the_active_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, operation: str
) -> None:
    path = tmp_path / f"daily-use-{kind}.json"
    old: dict[str, object]
    if kind == "state":
        old = _state()
    elif kind == "stop":
        old = {"token": "a" * 64}
    elif kind == "error":
        old = daily_use.DailyUseError("synthetic_failure", "safe", "retry").as_dict()
    else:
        old = {
            "format": "lifecase.daily-use-start-lock.v1",
            "pid": os.getpid(),
            "token": "a" * 64,
        }
    replacement = {**old, "token": "b" * 64}
    daily_use._atomic_json(path, old)
    original = io.open
    opened, release, attempting, finished = Event(), Event(), Event(), Event()
    held = False

    def open_held(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        nonlocal held
        stream = original(file, mode, *args, **kwargs)
        if not held and Path(file) == path and mode == "r":
            held = True
            read = stream.read

            def read_held(*read_args: Any, **read_kwargs: Any) -> Any:
                opened.set()
                assert release.wait(5), "Test reader must always be released"
                return read(*read_args, **read_kwargs)

            stream.read = read_held
        return stream

    def read_current() -> object:
        if kind == "state":
            return daily_use._read_state(path, required=True)
        if kind == "stop":
            return daily_use._valid_stop_request(path, "a" * 64)
        if kind == "error":
            error = daily_use._read_error(path)
            return error.code if error else None
        with pytest.raises(daily_use.DailyUseError) as caught:
            daily_use._acquire_start_lock(path)
        return caught.value.code

    errors: list[str] = []

    def mutate() -> None:
        attempting.set()
        try:
            if operation == "replace":
                daily_use._atomic_json(path, replacement)
            elif kind == "start-lock":
                daily_use._release_start_lock(path, "a" * 64)
            else:
                daily_use._remove_runtime_control_files(path, tmp_path / "absent-stop.json")
        except OSError as error:
            errors.append(str(error))
        finally:
            finished.set()

    monkeypatch.setattr(io, "open", open_held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(read_current)
        writer = None
        try:
            assert opened.wait(5), "Production reader must hold a real OS file handle"
            writer = pool.submit(mutate)
            assert attempting.wait(5)
            # Fixed rendezvous, not a probabilistic stress loop: the reader stays
            # open until this assertion, so an unguarded mutation hits that handle.
            assert not finished.wait(0.15), errors or "Mutation bypassed the active reader"
        finally:
            release.set()
            if writer is not None:
                writer.result(timeout=5)
            snapshot = reader.result(timeout=5)
    assert errors == []
    expected = (
        old
        if kind == "state"
        else (
            True
            if kind == "stop"
            else "synthetic_failure"
            if kind == "error"
            else "startup_in_progress"
        )
    )
    assert snapshot == expected
    if operation == "replace":
        assert json.loads(path.read_text(encoding="utf-8")) == replacement
    else:
        assert not path.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


@pytest.mark.parametrize("kind", ["state", "error"])
def test_optional_control_disappearing_before_open_is_not_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path = tmp_path / "daily-use-control.json"
    daily_use._atomic_json(path, _state())
    original = io.open

    def remove_before_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if Path(file) == path and mode == "r":
            path.unlink(missing_ok=True)
        return original(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", remove_before_open)
    result = (
        daily_use._read_state(path, required=False)
        if kind == "state"
        else daily_use._read_error(path)
    )
    assert result is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows non-sharing external handle")
def test_uncooperative_external_reader_fails_without_replacing_or_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "daily-use-state.json"
    daily_use._atomic_json(path, _state())
    original = path.read_bytes()
    replace = os.replace
    attempts: list[tuple[Path, Path]] = []

    def replace_once(source: Path, target: Path) -> None:
        attempts.append((source, target))
        replace(source, target)

    monkeypatch.setattr(os, "replace", replace_once)
    with path.open("r", encoding="utf-8") as reader:
        with pytest.raises(PermissionError):
            daily_use._atomic_json(path, _state("b" * 64))
        assert reader.read().encode() == original
    assert len(attempts) == 1
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".*.tmp")) == []


def test_bad_state_and_foreign_lock_still_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "daily-use-state.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(daily_use.DailyUseError) as captured:
        daily_use._read_state(path, required=False)
    assert captured.value.code == "runtime_state_invalid"
    assert path.read_text(encoding="utf-8") == "{broken"
    path.unlink()
    with pytest.raises(daily_use.DailyUseError) as absent:
        daily_use._read_state(path, required=True)
    assert absent.value.code == "runtime_state_invalid"

    lock = tmp_path / "daily-use-start.lock"
    owner = daily_use._acquire_start_lock(lock)
    original = lock.read_bytes()
    daily_use._release_start_lock(lock, "foreign-token")
    assert lock.read_bytes() == original
    with pytest.raises(daily_use.DailyUseError) as live:
        daily_use._acquire_start_lock(lock)
    assert live.value.code == "startup_in_progress"
    assert lock.read_bytes() == original
    daily_use._release_start_lock(lock, owner)
    assert not lock.exists()


@pytest.mark.parametrize("cleanup", ["stop", "failed-spawn"])
def test_exit_cleanup_does_not_delete_replacement_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: str
) -> None:
    root = daily_use.initialize_runtime(tmp_path / "runtime", program_root=Path.cwd())
    state_path, stop_path, _, _ = daily_use._runtime_paths(root)
    prior = _state()
    replacement = _state("b" * 64)  # Even an identical/reused PID is not the same owner.
    daily_use._atomic_json(state_path, prior)
    calls = 0

    def exited(_pid: int) -> bool:
        nonlocal calls
        calls += 1
        if cleanup == "stop" and calls == 1:
            return True
        daily_use._atomic_json(state_path, replacement)
        daily_use._atomic_json(stop_path, {"token": replacement["token"]})
        return False

    monkeypatch.setattr(daily_use, "_process_exists", exited)
    if cleanup == "stop":
        daily_use.stop_runtime(root, program_root=Path.cwd(), timeout_seconds=0.1)
    else:
        daily_use._stop_spawned_process(os.getpid(), state_path, stop_path, timeout_seconds=0.1)
    assert daily_use._read_state(state_path, required=True) == replacement
    assert daily_use._valid_stop_request(stop_path, str(replacement["token"]))


@pytest.mark.parametrize("failure", ["read", "stop-write"])
def test_control_failure_still_stops_only_known_spawned_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    state_path = tmp_path / "daily-use-state.json"
    stop_path = tmp_path / "daily-use-stop.json"
    child_pid = 123456
    owned = {**_state(), "pid": child_pid}
    foreign = {**_state("b" * 64), "pid": 654321}
    daily_use._atomic_json(state_path, foreign if failure == "read" else owned)
    daily_use._atomic_json(stop_path, {"token": foreign["token"]})
    terminated: list[int] = []

    def fail_read(*_args: Any, **_kwargs: Any) -> None:
        raise daily_use.DailyUseError("runtime_control_busy", "busy", "check status")

    def fail_stop_write(*_args: Any, **_kwargs: Any) -> None:
        state_path.write_text(json.dumps(foreign), encoding="utf-8")
        raise PermissionError("synthetic external holder")

    monkeypatch.setattr(daily_use, "_process_exists", lambda _pid: True)
    monkeypatch.setattr(daily_use, "_terminate_process", terminated.append)
    if failure == "read":
        monkeypatch.setattr(daily_use, "_read_state", fail_read)
    else:
        monkeypatch.setattr(daily_use, "_atomic_json", fail_stop_write)
    daily_use._stop_spawned_process(child_pid, state_path, stop_path, timeout_seconds=0)
    assert terminated == [child_pid]
    assert json.loads(state_path.read_text(encoding="utf-8")) == foreign
    assert json.loads(stop_path.read_text(encoding="utf-8")) == {"token": foreign["token"]}
