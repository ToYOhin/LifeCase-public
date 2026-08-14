"""Focused M7-03 daily-use package integration coverage."""

import json
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lifecase.cases import CaseService
from lifecase.claims import (
    ClaimCreatedBy,
    ClaimService,
    ClaimStatus,
    ClaimValue,
    CreateClaimCommand,
)
from lifecase.config import ApplicationConfig
from lifecase.daily_use import (
    DailyUseError,
    _server_command_prefix,
    initialize_runtime,
    runtime_config,
    runtime_status,
    start_runtime,
    stop_runtime,
    validate_runtime,
)
from lifecase.main import StaticAssetsError, create_app
from lifecase.storage import MIGRATIONS, apply_migrations, connect_database
from lifecase.storage.backup import BackupService


def _config(tmp_path: Path) -> ApplicationConfig:
    runtime = tmp_path / "runtime"
    return ApplicationConfig(
        database_path=runtime / "lifecase.sqlite3",
        data_root=runtime / "data",
        upload_root=runtime / "uploads",
    )


def _static_root(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><html><body><div id="root">LifeCase</div></body></html>',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("console.log('synthetic')", encoding="utf-8")
    return root


def test_server_command_prefix_switches_only_for_a_frozen_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\LifeCase\LifeCase.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert _server_command_prefix() == [
        r"C:\Program Files\LifeCase\LifeCase.exe",
        "_serve",
    ]

    monkeypatch.delattr(sys, "frozen")
    assert _server_command_prefix() == [
        r"C:\Program Files\LifeCase\LifeCase.exe",
        "-m",
        "lifecase.daily_use",
        "_serve",
    ]


def test_daily_use_app_serves_health_and_built_web_from_one_origin(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path), static_root=_static_root(tmp_path))) as client:
        health = client.get("/api/v1/system/health")
        index = client.get("/")
        asset = client.get("/assets/app.js")

    assert health.status_code == index.status_code == asset.status_code == 200
    assert health.json()["status"] == "ok"
    assert "LifeCase" in index.text
    assert "synthetic" in asset.text


@pytest.mark.parametrize("missing", ["root", "index", "assets"])
def test_daily_use_app_rejects_missing_static_assets(tmp_path: Path, missing: str) -> None:
    root = _static_root(tmp_path)
    if missing == "root":
        for child in (root / "assets" / "app.js", root / "index.html"):
            child.unlink()
        (root / "assets").rmdir()
        root.rmdir()
    elif missing == "index":
        (root / "index.html").unlink()
    else:
        (root / "assets" / "app.js").unlink()
        (root / "assets").rmdir()

    with pytest.raises(StaticAssetsError) as captured:
        create_app(_config(tmp_path), static_root=root)

    assert captured.value.code == "static_assets_missing"
    assert str(tmp_path) not in str(captured.value)


def test_daily_use_capabilities_are_explicit_and_backup_reminder_is_read_only(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config, static_root=_static_root(tmp_path))) as client:
        before = list(config.database_path.parent.rglob("*.zip"))
        response = client.get("/api/v1/system/capabilities")
        after = list(config.database_path.parent.rglob("*.zip"))

    assert response.status_code == 200
    assert response.json() == {
        "ocr": {
            "status": "unavailable",
            "code": "OCR_REQUIRED",
            "action": "Use a text PDF, TXT, or EML source; OCR is not included.",
        },
        "model": {
            "status": "disabled",
            "action": "Deterministic Case reads, exports, and Backup remain available.",
        },
        "backup_reminder": {
            "status": "no_verified_backup",
            "action": "Create a Backup explicitly from the Backup and Restore panel.",
        },
    }
    assert before == after == []


def test_backup_reminder_reports_latest_verified_backup_without_creating_another(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.data_root.mkdir(parents=True)
    connection = connect_database(config.database_path)
    apply_migrations(connection)
    artifact = BackupService(connection, config.database_path, config.data_root).create()
    connection.close()
    before = list(config.database_path.parent.rglob("*.zip"))

    with TestClient(create_app(config, static_root=_static_root(tmp_path))) as client:
        response = client.get("/api/v1/system/capabilities")

    after = list(config.database_path.parent.rglob("*.zip"))
    reminder = response.json()["backup_reminder"]
    assert response.status_code == 200
    assert reminder["status"] == "verified"
    assert reminder["latest"]["id"] == artifact.id
    assert reminder["latest"]["created_at"] == artifact.created_at
    assert before == after


def test_setup_is_repeatable_for_one_marked_runtime_and_preserves_its_data(
    tmp_path: Path,
) -> None:
    program_root = tmp_path / "program"
    program_root.mkdir()
    selected = tmp_path / "selected-data"

    created = initialize_runtime(selected, program_root=program_root)
    marker = selected / ".lifecase-runtime.json"
    retained = selected / "data" / "retained.bin"
    retained.write_bytes(b"synthetic retained authority")
    repeated = initialize_runtime(selected, program_root=program_root)

    assert created == repeated == selected.resolve()
    assert marker.is_file()
    assert retained.read_bytes() == b"synthetic retained authority"
    assert runtime_config(selected, program_root=program_root).model_provider.provider.value == (
        "disabled"
    )


def test_daily_use_can_enable_fake_demo_without_enabling_model_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program_root = tmp_path / "program"
    program_root.mkdir()
    selected = initialize_runtime(tmp_path / "selected-data", program_root=program_root)
    monkeypatch.setenv("LIFECASE_PORTFOLIO_AGENT_DEMO", "true")

    config = runtime_config(selected, program_root=program_root)

    assert config.portfolio_agent_demo_enabled is True
    assert config.model_provider.provider.value == "disabled"

    monkeypatch.setenv("LIFECASE_PORTFOLIO_AGENT_DEMO", "invalid")
    with pytest.raises(DailyUseError) as captured:
        runtime_config(selected, program_root=program_root)
    assert captured.value.code == "agent_demo_configuration_invalid"
    assert str(tmp_path) not in str(captured.value)


def test_conflicting_existing_directory_fails_without_modification(tmp_path: Path) -> None:
    program_root = tmp_path / "program"
    program_root.mkdir()
    selected = tmp_path / "existing"
    selected.mkdir()
    marker = selected / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(DailyUseError) as captured:
        initialize_runtime(selected, program_root=program_root)

    assert captured.value.code == "data_root_conflict"
    assert list(selected.iterdir()) == [marker]
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("kind", ["relative", "root", "program", "program-child"])
def test_unsafe_data_roots_fail_before_any_write(tmp_path: Path, kind: str) -> None:
    program_root = tmp_path / "program"
    program_root.mkdir()
    candidates = {
        "relative": Path("relative-data"),
        "root": Path(tmp_path.anchor),
        "program": program_root,
        "program-child": program_root / "private-data",
    }
    selected = candidates[kind]

    with pytest.raises(DailyUseError) as captured:
        initialize_runtime(selected, program_root=program_root)

    assert captured.value.code in {"data_root_not_absolute", "data_root_unsafe"}
    if kind == "program-child":
        assert not selected.exists()


def test_symbolic_link_and_unwritable_roots_fail_without_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program_root = tmp_path / "program"
    program_root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    symbolic = tmp_path / "symbolic"
    try:
        symbolic.symlink_to(target, target_is_directory=True)
    except OSError:
        linked = subprocess.run(  # noqa: S603
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "New-Item -ItemType Junction -Path $env:LIFECASE_TEST_LINK "
                "-Target $env:LIFECASE_TEST_TARGET | Out-Null",
            ],
            check=False,
            capture_output=True,
            env={
                **os.environ,
                "LIFECASE_TEST_LINK": str(symbolic),
                "LIFECASE_TEST_TARGET": str(target),
            },
            text=True,
            timeout=10,
        )
        if linked.returncode != 0:
            pytest.skip("Symbolic-link and junction creation are unavailable on this host")

    try:
        with pytest.raises(DailyUseError) as captured:
            initialize_runtime(symbolic, program_root=program_root)
        assert captured.value.code == "data_root_symlink"
        assert list(target.iterdir()) == []
    finally:
        if symbolic.is_symlink():
            symbolic.unlink()
        elif symbolic.is_junction():
            symbolic.rmdir()

    unwritable = tmp_path / "unwritable"
    monkeypatch.setattr("lifecase.daily_use._probe_writable", lambda _path: False)
    with pytest.raises(DailyUseError) as blocked:
        initialize_runtime(unwritable, program_root=program_root)
    assert blocked.value.code == "data_root_not_writable"
    assert not unwritable.exists()


def test_validate_runtime_rejects_damaged_marker_without_repair(tmp_path: Path) -> None:
    program_root = tmp_path / "program"
    program_root.mkdir()
    selected = tmp_path / "selected"
    initialize_runtime(selected, program_root=program_root)
    marker = selected / ".lifecase-runtime.json"
    marker.write_text("{}", encoding="utf-8")

    with pytest.raises(DailyUseError) as captured:
        validate_runtime(selected, program_root=program_root)

    assert captured.value.code == "data_root_conflict"
    assert marker.read_text(encoding="utf-8") == "{}"


def test_one_command_start_checks_api_and_web_then_stop_leaves_no_runtime_state(
    tmp_path: Path,
) -> None:
    program_root = Path.cwd()
    selected = tmp_path / "daily-use"
    initialize_runtime(selected, program_root=program_root)
    port = _free_port()

    started = start_runtime(
        selected,
        program_root=program_root,
        static_root=_static_root(tmp_path),
        port=port,
    )
    try:
        with urllib.request.urlopen(  # noqa: S310
            f"http://127.0.0.1:{port}/api/v1/system/health", timeout=2
        ) as response:
            health = json.loads(response.read())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:  # noqa: S310
            index = response.read().decode("utf-8")

        assert started["status"] == "running"
        assert health["status"] == "ok"
        assert "LifeCase" in index
        assert runtime_status(selected, program_root=program_root)["status"] == "running"
    finally:
        stopped = stop_runtime(selected, program_root=program_root)

    assert stopped == {"status": "stopped", "data_retained": True}
    assert runtime_status(selected, program_root=program_root)["status"] == "stopped"
    assert not (selected / "runtime" / "daily-use-state.json").exists()
    assert not (selected / "runtime" / "daily-use-stop.json").exists()


def test_port_conflict_and_missing_static_fail_without_process_state(tmp_path: Path) -> None:
    program_root = Path.cwd()
    selected = tmp_path / "daily-use"
    initialize_runtime(selected, program_root=program_root)
    static_root = _static_root(tmp_path)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = int(occupied.getsockname()[1])
        with pytest.raises(DailyUseError) as conflict:
            start_runtime(
                selected,
                program_root=program_root,
                static_root=static_root,
                port=port,
            )
    assert conflict.value.code == "port_in_use"

    (static_root / "index.html").unlink()
    with pytest.raises(DailyUseError) as missing:
        start_runtime(
            selected,
            program_root=program_root,
            static_root=static_root,
            port=_free_port(),
        )
    assert missing.value.code == "static_assets_missing"
    assert not (selected / "runtime" / "daily-use-state.json").exists()


def test_live_start_lock_fails_closed_without_removing_another_launcher_lock(
    tmp_path: Path,
) -> None:
    program_root = Path.cwd()
    selected = tmp_path / "daily-use"
    initialize_runtime(selected, program_root=program_root)
    lock = selected / "runtime" / "daily-use-start.lock"
    value = {
        "format": "lifecase.daily-use-start-lock.v1",
        "pid": os.getpid(),
        "token": "a" * 64,
    }
    lock.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(DailyUseError) as captured:
        start_runtime(
            selected,
            program_root=program_root,
            static_root=_static_root(tmp_path),
            port=_free_port(),
        )

    assert captured.value.code == "startup_in_progress"
    assert json.loads(lock.read_text(encoding="utf-8")) == value
    assert not (selected / "runtime" / "daily-use-state.json").exists()


def test_migration_failure_is_typed_and_leaves_no_false_running_state(tmp_path: Path) -> None:
    program_root = Path.cwd()
    selected = tmp_path / "daily-use"
    initialize_runtime(selected, program_root=program_root)
    config = runtime_config(selected, program_root=program_root)
    connection = connect_database(config.database_path)
    apply_migrations(connection)
    connection.execute("UPDATE schema_migration SET checksum = ? WHERE version = 1", ("f" * 64,))
    connection.close()

    with pytest.raises(DailyUseError) as captured:
        start_runtime(
            selected,
            program_root=program_root,
            static_root=_static_root(tmp_path),
            port=_free_port(),
        )

    assert captured.value.code == "migration_failed"
    assert str(tmp_path) not in str(captured.value)
    assert not (selected / "runtime" / "daily-use-state.json").exists()


def test_pre_migration_backup_failure_blocks_upgrade_without_false_running_state(
    tmp_path: Path,
) -> None:
    program_root = Path.cwd()
    selected = tmp_path / "daily-use"
    initialize_runtime(selected, program_root=program_root)
    config = runtime_config(selected, program_root=program_root)
    connection = connect_database(config.database_path)
    apply_migrations(connection, MIGRATIONS[:-1])
    connection.close()
    conflicting_authority = config.data_root / "unregistered.bin"
    conflicting_authority.write_bytes(b"synthetic retained conflict")

    with pytest.raises(DailyUseError) as captured:
        start_runtime(
            selected,
            program_root=program_root,
            static_root=_static_root(tmp_path),
            port=_free_port(),
        )

    assert captured.value.code == "pre_migration_backup_failed"
    assert str(tmp_path) not in str(captured.value)
    assert conflicting_authority.read_bytes() == b"synthetic retained conflict"
    connection = connect_database(config.database_path)
    try:
        latest = connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
    finally:
        connection.close()
    assert latest == MIGRATIONS[-2].version
    assert not (selected / "runtime" / "daily-use-state.json").exists()


def test_selected_data_recovers_after_daily_use_exit_and_restart(tmp_path: Path) -> None:
    program_root = Path.cwd()
    selected = tmp_path / "daily-use"
    initialize_runtime(selected, program_root=program_root)
    config = runtime_config(selected, program_root=program_root)
    connection = connect_database(config.database_path)
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic restart warranty",
        goal="Verify package restart recovery",
        template_id="warranty",
    )
    connection.close()
    static_root = _static_root(tmp_path)

    for _ in range(2):
        port = _free_port()
        start_runtime(
            selected,
            program_root=program_root,
            static_root=static_root,
            port=port,
        )
        try:
            with urllib.request.urlopen(  # noqa: S310
                f"http://127.0.0.1:{port}/api/v1/cases/{case.id}", timeout=2
            ) as response:
                restored = json.loads(response.read())
            assert restored["id"] == case.id
        finally:
            stop_runtime(selected, program_root=program_root)


def test_short_synthetic_warranty_acceptance_through_daily_use_and_restart(
    tmp_path: Path,
) -> None:
    """Package acceptance only; this is not seven-day private dogfood."""

    program_root = Path.cwd()
    selected = tmp_path / "daily-use-acceptance"
    initialize_runtime(selected, program_root=program_root)
    static_root = _static_root(tmp_path)
    port = _free_port()
    start_runtime(selected, program_root=program_root, static_root=static_root, port=port)
    base = f"http://127.0.0.1:{port}"
    try:
        case = _json_call(
            base,
            "/api/v1/cases",
            method="POST",
            payload={
                "title": "Synthetic daily-use warranty",
                "goal": "Verify the packaged deterministic workflow",
                "template_id": "warranty",
            },
            expected=201,
        )
        source = _multipart_upload(
            base,
            f"/api/v1/cases/{case['id']}/sources",
            filename="synthetic-warranty.txt",
            content=(
                b"Synthetic warranty authority. Explicit deadline: 2026-09-01.\n"
                b"No person, account, credential, or external service.\n"
            ),
        )
        assert source["extraction"]["status"] == "completed"

        # There is deliberately no Agent/Claim-creation HTTP route in M7-03. Seed one
        # explicit-user candidate through the existing domain boundary, then exercise
        # the public confirmation and all remaining user-facing HTTP contracts.
        config = runtime_config(selected, program_root=program_root)
        connection = connect_database(config.database_path)
        try:
            claim = ClaimService(connection).create_claim(
                CreateClaimCommand(
                    case_id=str(case["id"]),
                    field="policy_deadline",
                    value=ClaimValue.from_input("2026-09-01"),
                    status=ClaimStatus.CANDIDATE,
                    confidence=None,
                    created_by=ClaimCreatedBy.USER,
                    actor="synthetic_local_user",
                    reason="Seed explicit-user candidate without model runtime",
                )
            )
        finally:
            connection.close()

        confirmed_claim = _json_call(
            base,
            f"/api/v1/claims/{claim.id}/confirm",
            method="POST",
            payload={
                "expected_version": claim.version,
                "basis": "user",
                "actor": "synthetic_local_user",
                "reason": "Confirm the synthetic warranty deadline",
            },
        )
        deadline = _json_call(
            base,
            f"/api/v1/cases/{case['id']}/deadlines/explicit",
            method="POST",
            payload={
                "basis": {
                    "claim_id": claim.id,
                    "expected_claim_version": confirmed_claim["version"],
                    "value_sha256": claim.value.sha256,
                    "date_kind": "local_date",
                },
                "timezone": "Asia/Shanghai",
                "actor": "synthetic_local_user",
                "reason": "Create an evidence-bound synthetic deadline",
            },
            expected=201,
        )
        confirmed_deadline = _json_call(
            base,
            f"/api/v1/deadlines/{deadline['id']}/confirm",
            method="POST",
            payload={
                "expected_version": deadline["version"],
                "actor": "synthetic_local_user",
                "reason": "Confirm the displayed synthetic deadline",
            },
        )
        action = _json_call(
            base,
            f"/api/v1/cases/{case['id']}/actions",
            method="POST",
            payload={
                "kind": "collect_evidence",
                "risk_level": "B",
                "payload": {
                    "requirement_key": "purchase_proof",
                    "requirement_type": "evidence",
                },
                "requirements": [],
                "dependencies": [],
                "deadline": None,
                "actor": "synthetic_local_user",
                "reason": "Create a synthetic approval-gated Action",
            },
            expected=201,
        )
        approval = _json_call(
            base,
            f"/api/v1/actions/{action['id']}/approval-requests",
            method="POST",
            payload={
                "expected_action_version": action["version"],
                "payload_sha256": action["payload_sha256"],
                "summary": "Approve exact synthetic evidence collection Action",
                "actor": "synthetic_local_user",
                "reason": "Request explicit synthetic approval",
            },
            expected=201,
        )
        decision = _json_call(
            base,
            f"/api/v1/actions/{action['id']}/approve",
            method="POST",
            payload={
                "approval_id": approval["id"],
                "expected_approval_version": approval["version"],
                "expected_action_version": action["version"],
                "actor": "synthetic_local_user",
                "reason": "Approve only the displayed synthetic Action",
            },
        )
        timeline = _json_call(base, f"/api/v1/cases/{case['id']}/timeline")
        calendar = _bytes_call(
            base,
            f"/api/v1/cases/{case['id']}/deadlines/{deadline['id']}/calendar-export",
        )
        pack = _json_call(
            base,
            f"/api/v1/cases/{case['id']}/evidence-pack",
            method="POST",
            expected=201,
        )
        pack_bytes = _bytes_call(base, str(pack["download_url"]))
        backup = _json_call(base, "/api/v1/backups", method="POST", expected=201)
        capabilities = _json_call(base, "/api/v1/system/capabilities")

        assert confirmed_claim["status"] == "confirmed"
        assert confirmed_deadline["status"] == "confirmed"
        assert decision["approval"]["status"] == "approved"
        assert decision["action"]["status"] == "approved"
        assert timeline["events"]
        assert calendar.startswith(b"BEGIN:VCALENDAR")
        assert pack_bytes.startswith(b"PK")
        assert backup["case_count"] == 1
        assert capabilities["ocr"]["code"] == "OCR_REQUIRED"
        assert capabilities["model"]["status"] in {"disabled", "unavailable"}
        assert capabilities["backup_reminder"]["latest"]["id"] == backup["id"]
    finally:
        stop_runtime(selected, program_root=program_root)

    restarted_port = _free_port()
    start_runtime(
        selected,
        program_root=program_root,
        static_root=static_root,
        port=restarted_port,
    )
    restarted = f"http://127.0.0.1:{restarted_port}"
    try:
        assert _json_call(restarted, f"/api/v1/cases/{case['id']}")["id"] == case["id"]
        assert (
            _json_call(restarted, f"/api/v1/cases/{case['id']}/deadlines")[0]["status"]
            == "confirmed"
        )
        assert (
            _json_call(restarted, f"/api/v1/cases/{case['id']}/actions")[0]["status"] == "approved"
        )
        assert (
            _json_call(restarted, f"/api/v1/cases/{case['id']}/approvals")[0]["status"]
            == "approved"
        )
        assert _json_call(restarted, f"/api/v1/cases/{case['id']}/timeline")["events"]
        assert (
            _json_call(restarted, f"/api/v1/cases/{case['id']}/evidence-pack")["id"] == pack["id"]
        )
        assert _json_call(restarted, "/api/v1/backups")["id"] == backup["id"]
    finally:
        stop_runtime(selected, program_root=program_root)


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell entrypoint is Windows-only")
def test_powershell_status_entry_and_uninstall_semantics_retain_user_data(
    tmp_path: Path,
) -> None:
    program_root = Path.cwd()
    selected = tmp_path / "daily-use"
    initialize_runtime(selected, program_root=program_root)
    retained = selected / "data" / "retained.bin"
    retained.write_bytes(b"synthetic retained authority")

    for command, expected in (("status", "stopped"), ("uninstall", "uninstalled")):
        completed = subprocess.run(  # noqa: S603
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(program_root / "scripts" / "lifecase.ps1"),
                command,
                "-DataRoot",
                str(selected),
                "-NoBrowser",
            ],
            cwd=program_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0
        assert json.loads(completed.stdout)["status"] == expected
    assert retained.read_bytes() == b"synthetic retained authority"
    assert (selected / ".lifecase-runtime.json").is_file()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _json_call(
    base: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    expected: int = 200,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if body is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        assert response.status == expected
        return json.loads(response.read())


def _bytes_call(base: str, path: str) -> bytes:
    with urllib.request.urlopen(base + path, timeout=5) as response:  # noqa: S310
        assert response.status == 200
        return response.read()


def _multipart_upload(base: str, path: str, *, filename: str, content: bytes) -> Any:
    boundary = "lifecase-synthetic-boundary"
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = urllib.request.Request(
        base + path,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        assert response.status == 201
        return json.loads(response.read())
