"""Case-scoped preparation intent persistence without changing fact authority."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lifecase.cases import CaseService
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.storage import MIGRATIONS, apply_migrations, connect_database
from lifecase.storage.backup import BackupService
from lifecase.timeline import TimelineService


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _case(client: TestClient, case_type: str = "warranty") -> dict[str, Any]:
    response = client.post(
        "/api/v1/cases",
        json={"title": "合成报修", "goal": "案件目标不作为本人诉求", "template_id": case_type},
    )
    assert response.status_code == 201
    return dict(response.json())


def _url(case_id: str) -> str:
    return f"/api/v1/cases/{case_id}/preparation-intent"


def _authority(
    config: ApplicationConfig, case_id: str, *, include_timeline: bool = True
) -> dict[str, object]:
    connection = connect_database(config.database_path)
    try:
        result: dict[str, object] = {
            "case": CaseService(connection).get_case(case_id),
        }
        if include_timeline:
            result["timeline"] = TimelineService(connection).list_case_timeline(case_id)
        for table in ("claim", "action", "action_event", "approval_request"):
            result[table] = tuple(
                tuple(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} WHERE case_id = ?", (case_id,)
                )
            )
        result["claim_event"] = tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT event.* FROM claim_event AS event
                JOIN claim ON claim.id = event.claim_id
                WHERE claim.case_id = ? ORDER BY event.id
                """,
                (case_id,),
            )
        )
        return result
    finally:
        connection.close()


def test_get_is_read_only_and_saved_intent_survives_new_app_without_changing_authority(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case = _case(client)
        case_id = case["id"]
        before = _authority(config, case_id)
        empty = client.get(_url(case_id))
        assert empty.status_code == 200
        assert empty.json() == {"case_id": case_id, "intent": "", "version": 0, "updated_at": None}
        connection = connect_database(config.database_path)
        try:
            assert (
                connection.execute("SELECT COUNT(*) FROM warranty_preparation_intent").fetchone()[0]
                == 0
            )
        finally:
            connection.close()
        saved = client.put(
            _url(case_id),
            json={"intent": "请说明检测流程。\n请先告知费用。", "expected_version": 0},
        )
        assert saved.status_code == 200
        assert saved.json()["version"] == 1
        assert saved.json()["intent"] == "请说明检测流程。\n请先告知费用。"
        assert saved.json()["updated_at"].endswith("Z")
        assert _authority(config, case_id) == before
        assert client.get(_url(case_id)).json() == saved.json()

    with TestClient(create_app(config)) as restarted:
        assert restarted.get(_url(case_id)).json() == saved.json()
        assert restarted.get(f"/api/v1/cases/{case_id}").json() == case
        assert _authority(config, case_id) == before


def test_update_clear_and_stale_versions_cannot_overwrite_or_resurrect_intent(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        case_id = _case(client)["id"]
        url = _url(case_id)
        first = client.put(url, json={"intent": "询问检测", "expected_version": 0})
        assert first.status_code == 200
        second = client.put(url, json={"intent": "仅先咨询费用", "expected_version": 1})
        assert second.status_code == 200
        assert second.json()["version"] == 2
        stale = client.put(url, json={"intent": "陈旧页面", "expected_version": 1})
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "preparation_intent_version_conflict"
        assert stale.json()["error"]["details"] == {"current_version": 2}
        assert client.get(url).json() == second.json()
        cleared = client.put(url, json={"intent": "", "expected_version": 2})
        assert cleared.status_code == 200
        assert (cleared.json()["intent"], cleared.json()["version"]) == ("", 3)
        assert cleared.json()["updated_at"] is not None
        for old_version in (0, 1, 2):
            assert (
                client.put(
                    url, json={"intent": "不得复活", "expected_version": old_version}
                ).status_code
                == 409
            )
        assert client.get(url).json() == cleared.json()


def test_concurrent_first_save_has_one_winner(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        url = _url(_case(client)["id"])

        def save(intent: str) -> tuple[int, dict[str, Any]]:
            response = client.put(url, json={"intent": intent, "expected_version": 0})
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, ("第一个页面", "第二个页面")))
        assert sorted(status for status, _ in results) == [200, 409]
        winner = next(body for status, body in results if status == 200)
        assert winner["version"] == 1
        assert client.get(url).json() == winner


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "x", "expected_version": -1},
        {"intent": "x", "expected_version": True},
        {"intent": "x", "expected_version": "0"},
        {"intent": "x", "expected_version": 0.0},
        {"intent": "x" * 2001, "expected_version": 0},
        {"intent": None, "expected_version": 0},
        {"intent": 123, "expected_version": 0},
        {"intent": "无法可靠保存\u0000", "expected_version": 0},
        {"intent": "\ud800", "expected_version": 0},
        {"expected_version": 0},
        {"intent": "x", "expected_version": 0, "case_id": "other"},
    ],
)
def test_invalid_requests_do_not_save(tmp_path: Path, payload: dict[str, Any]) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        url = _url(_case(client)["id"])
        response = client.put(
            url, content=json.dumps(payload), headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert client.get(url).json()["version"] == 0


def test_case_scope_and_unsupported_cases(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        first = _case(client)["id"]
        second = _case(client)["id"]
        unsupported = _case(client, "return_refund")["id"]
        saved = client.put(_url(first), json={"intent": "仅属于第一案", "expected_version": 0})
        assert saved.status_code == 200
        assert client.get(_url(second)).json()["intent"] == ""
        assert (
            client.put(_url(second), json={"intent": "第二案", "expected_version": 0}).status_code
            == 200
        )
        assert client.get(_url(first)).json() == saved.json()
        for case_id, code, status in (
            ("case_missing", "case_not_found", 404),
            (unsupported, "preparation_intent_unsupported", 422),
        ):
            for response in (
                client.get(_url(case_id)),
                client.put(_url(case_id), json={"intent": "合成", "expected_version": 0}),
            ):
                assert response.status_code == status
                assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("corruption", ("text", "version", "timestamp", "missing_table"))
def test_invalid_storage_fails_safely_and_is_not_overwritten(
    tmp_path: Path, corruption: str
) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        case_id = _case(client)["id"]
        url = _url(case_id)
        assert client.put(url, json={"intent": "正常", "expected_version": 0}).status_code == 200
        connection = connect_database(config.database_path)
        try:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            if corruption == "text":
                connection.execute(
                    "UPDATE warranty_preparation_intent SET intent = ?", ("synthetic-secret" * 200,)
                )
            elif corruption == "version":
                connection.execute("UPDATE warranty_preparation_intent SET version = 0")
            elif corruption == "timestamp":
                connection.execute(
                    "UPDATE warranty_preparation_intent SET updated_at = 'synthetic-secret'"
                )
            else:
                connection.execute("DROP TABLE warranty_preparation_intent")
        finally:
            connection.close()
        for response in (
            client.get(url),
            client.put(url, json={"intent": "不得覆盖损坏记录", "expected_version": 1}),
        ):
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "preparation_intent_unavailable"
            assert "synthetic-secret" not in response.text


def test_v16_upgrade_preserves_authority_and_old_backup_restores_with_empty_intent(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.data_root.mkdir(parents=True)
    connection = connect_database(config.database_path)
    try:
        apply_migrations(connection, MIGRATIONS[:16])
        case = CaseService(connection).create_case(
            title="旧案件", goal="保持目标", template_id="warranty"
        )
        old_catalog = tuple(
            tuple(row)
            for row in connection.execute("SELECT * FROM schema_migration ORDER BY version")
        )
        old_backup = BackupService(connection, config.database_path, config.data_root).create()
    finally:
        connection.close()
    # Current projections require the current schema. Capture the old stored
    # authority before startup performs the forward migration, then read Timeline.
    before = _authority(config, case.id, include_timeline=False)

    with TestClient(create_app(config)) as client:
        empty = client.get(_url(case.id))
        assert empty.status_code == 200
        assert empty.json()["version"] == 0
        assert _authority(config, case.id, include_timeline=False) == before
        assert client.get(f"/api/v1/cases/{case.id}/timeline").json()["events"] == []
        assert (
            client.put(
                _url(case.id), json={"intent": "升级后保存", "expected_version": 0}
            ).status_code
            == 200
        )
    connection = connect_database(config.database_path)
    try:
        assert (
            tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM schema_migration WHERE version <= 16 ORDER BY version"
                )
            )
            == old_catalog
        )
        assert (
            connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
            == MIGRATIONS[-1].version
        )
        service = BackupService(connection, config.database_path, config.data_root)
        validation = service.validate(service.get_download(old_backup.id).path)
        result = service.apply(validation.validation_id, "old-backup", "RESTORE INTO NEW DIRECTORY")
        assert result.migrated is True
    finally:
        connection.close()
    restored_root = config.database_path.parent / "recovery" / "restored" / "old-backup"
    restored_config = ApplicationConfig(
        database_path=restored_root / "lifecase.sqlite3",
        data_root=restored_root / "data",
        upload_root=restored_root / "uploads",
    )
    with TestClient(create_app(restored_config)) as restored:
        assert restored.get(_url(case.id)).json()["version"] == 0
        assert _authority(restored_config, case.id, include_timeline=False) == before
        assert restored.get(f"/api/v1/cases/{case.id}/timeline").json()["events"] == []


def test_backup_restore_retains_saved_and_cleared_versions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_app(config)) as client:
        first, second = _case(client)["id"], _case(client)["id"]
        saved = client.put(
            _url(first), json={"intent": "恢复后仍保留本人诉求", "expected_version": 0}
        )
        assert saved.status_code == 200
        assert (
            client.put(_url(second), json={"intent": "稍后清空", "expected_version": 0}).status_code
            == 200
        )
        cleared = client.put(_url(second), json={"intent": "", "expected_version": 1})
        assert cleared.status_code == 200
    connection = connect_database(config.database_path)
    try:
        service = BackupService(connection, config.database_path, config.data_root)
        backup = service.create()
        validation = service.validate(service.get_download(backup.id).path)
        assert (
            service.apply(
                validation.validation_id, "saved-copy", "RESTORE INTO NEW DIRECTORY"
            ).migrated
            is False
        )
    finally:
        connection.close()
    target = config.database_path.parent / "recovery" / "restored" / "saved-copy"
    restored_config = ApplicationConfig(
        database_path=target / "lifecase.sqlite3",
        data_root=target / "data",
        upload_root=target / "uploads",
    )
    with TestClient(create_app(restored_config)) as client:
        assert client.get(_url(first)).json() == saved.json()
        assert client.get(_url(second)).json() == cleared.json()
        assert (
            client.put(_url(second), json={"intent": "旧标签页", "expected_version": 0}).status_code
            == 409
        )
