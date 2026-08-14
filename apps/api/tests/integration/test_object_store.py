"""Integration tests for immutable content-addressed source storage."""

import hashlib
import sqlite3
from pathlib import Path
from typing import BinaryIO

import pytest

import lifecase.evidence.source_import as source_import_module
import lifecase.storage.object_store as object_store_module
from lifecase.evidence import CaseNotFoundError, SourceImporter, SourceMetadataError
from lifecase.storage import apply_migrations, connect_database
from lifecase.storage.object_store import (
    ContentAddressedObjectStore,
    InvalidSourcePathError,
    ObjectIntegrityError,
    ObjectStoreConfig,
    ObjectWriteError,
    SourceOutsideAllowedRootError,
    SourceSizeLimitError,
    UnsupportedMediaTypeError,
)


def make_store(
    tmp_path: Path,
    *,
    max_file_size_bytes: int = 1024,
    allowed_media_types: frozenset[str] = frozenset({"text/plain"}),
) -> tuple[ContentAddressedObjectStore, Path, Path]:
    source_root = tmp_path / "selected"
    source_root.mkdir()
    data_root = tmp_path / "data"
    store = ContentAddressedObjectStore(
        ObjectStoreConfig(
            data_root=data_root,
            allowed_source_roots=(source_root,),
            max_file_size_bytes=max_file_size_bytes,
            allowed_media_types=allowed_media_types,
            chunk_size_bytes=4,
        )
    )
    return store, source_root, data_root


def object_files(data_root: Path) -> list[Path]:
    return [path for path in data_root.rglob("*") if path.is_file()]


def add_case(connection: sqlite3.Connection, case_id: str = "case_test") -> None:
    connection.execute(
        """
        INSERT INTO case_record (
            id, type, template_version, title, goal, status,
            priority, version, created_at, updated_at
        ) VALUES (?, 'warranty', 1, 'Synthetic case', 'Test import', 'intake',
                  0, 1, ?, ?)
        """,
        (
            case_id,
            "2026-08-01T00:00:00.000000Z",
            "2026-08-01T00:00:00.000000Z",
        ),
    )


def test_stores_streamed_source_without_modifying_original(tmp_path: Path) -> None:
    store, source_root, data_root = make_store(tmp_path)
    source = source_root / "收据.txt"
    original_bytes = "合成测试收据，不含个人资料。".encode()
    source.write_bytes(original_bytes)
    original_stat = source.stat()
    expected_sha256 = hashlib.sha256(original_bytes).hexdigest()

    stored = store.store(source)

    assert stored.sha256 == expected_sha256
    assert stored.size_bytes == len(original_bytes)
    assert stored.media_type == "text/plain"
    assert stored.relative_path == (
        f"objects/{expected_sha256[:2]}/{expected_sha256[2:4]}/{expected_sha256}"
    )
    assert stored.created_new_object is True
    assert stored.absolute_path.read_bytes() == original_bytes
    assert object_files(data_root) == [stored.absolute_path]
    assert source.read_bytes() == original_bytes
    assert source.stat().st_size == original_stat.st_size
    assert source.stat().st_mtime_ns == original_stat.st_mtime_ns


def test_duplicate_bytes_reuse_one_object(tmp_path: Path) -> None:
    store, source_root, data_root = make_store(tmp_path)
    first_source = source_root / "first.txt"
    second_source = source_root / "second.txt"
    first_source.write_bytes(b"same synthetic bytes")
    second_source.write_bytes(b"same synthetic bytes")

    first = store.store(first_source)
    second = store.store(second_source)

    assert first.absolute_path == second.absolute_path
    assert first.created_new_object is True
    assert second.created_new_object is False
    assert object_files(data_root) == [first.absolute_path]


def test_rejects_missing_directory_relative_and_traversing_paths(tmp_path: Path) -> None:
    store, source_root, _ = make_store(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(InvalidSourcePathError):
        store.store(source_root / "missing.txt")
    with pytest.raises(InvalidSourcePathError):
        store.store(source_root)
    with pytest.raises(InvalidSourcePathError):
        store.store(Path("relative.txt"))
    with pytest.raises(SourceOutsideAllowedRootError):
        store.store(source_root / "nested" / ".." / ".." / "outside.txt")


def test_enforces_size_and_media_type_limits(tmp_path: Path) -> None:
    store, source_root, data_root = make_store(tmp_path, max_file_size_bytes=4)
    oversized = source_root / "oversized.txt"
    oversized.write_bytes(b"12345")
    unsupported = source_root / "program.exe"
    unsupported.write_bytes(b"MZ synthetic")

    with pytest.raises(SourceSizeLimitError):
        store.store(oversized)
    with pytest.raises(UnsupportedMediaTypeError):
        store.store(unsupported)

    assert object_files(data_root) == []


def test_interrupted_copy_removes_temporary_and_final_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, source_root, data_root = make_store(tmp_path)
    source = source_root / "source.txt"
    source.write_bytes(b"synthetic source")

    def interrupt_copy(
        source_path: Path,
        output: BinaryIO,
        *,
        chunk_size_bytes: int,
        max_size_bytes: int,
    ) -> tuple[str, int]:
        del source_path, chunk_size_bytes, max_size_bytes
        output.write(b"partial")
        raise OSError("simulated interrupted copy")

    monkeypatch.setattr(object_store_module, "_copy_source", interrupt_copy)

    with pytest.raises(ObjectWriteError):
        store.store(source)

    assert object_files(data_root) == []


def test_existing_object_is_verified_and_never_silently_overwritten(tmp_path: Path) -> None:
    store, source_root, data_root = make_store(tmp_path)
    source = source_root / "source.txt"
    source_bytes = b"expected synthetic bytes"
    source.write_bytes(source_bytes)
    expected_sha256 = hashlib.sha256(source_bytes).hexdigest()
    destination = (
        data_root / "objects" / expected_sha256[:2] / expected_sha256[2:4] / expected_sha256
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"tampered")

    with pytest.raises(ObjectIntegrityError):
        store.store(source)

    assert destination.read_bytes() == b"tampered"


def test_import_records_source_and_artifact_metadata(tmp_path: Path) -> None:
    store, source_root, _ = make_store(tmp_path)
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    add_case(connection)
    source = source_root / "receipt.txt"
    source.write_bytes(b"synthetic receipt")

    try:
        imported = SourceImporter(connection, store).import_selected(
            case_id="case_test", selected_path=source
        )

        source_row = connection.execute(
            """
            SELECT id, case_id, original_name, media_type, sha256,
                   size_bytes, object_path, trust_label, imported_at
            FROM source WHERE id = ?
            """,
            (imported.source_id,),
        ).fetchone()
        artifact_row = connection.execute(
            """
            SELECT id, case_id, source_id, type, relative_path,
                   sha256, size_bytes, created_by_run, created_at
            FROM artifact WHERE id = ?
            """,
            (imported.artifact_id,),
        ).fetchone()

        assert source_row is not None
        assert tuple(source_row[:8]) == (
            imported.source_id,
            "case_test",
            "receipt.txt",
            imported.media_type,
            imported.sha256,
            imported.size_bytes,
            imported.object_path,
            "untrusted",
        )
        assert str(source_row[8]).endswith("Z")
        assert artifact_row is not None
        assert tuple(artifact_row[:8]) == (
            imported.artifact_id,
            "case_test",
            imported.source_id,
            "source",
            imported.object_path,
            imported.sha256,
            imported.size_bytes,
            None,
        )
        assert str(artifact_row[8]).endswith("Z")
        assert imported.trust_label == "untrusted"
    finally:
        connection.close()


def test_repeated_explicit_imports_create_two_records_but_one_object(tmp_path: Path) -> None:
    store, source_root, data_root = make_store(tmp_path)
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    add_case(connection)
    source = source_root / "receipt.txt"
    source.write_bytes(b"same explicit selection")
    importer = SourceImporter(connection, store)

    try:
        first = importer.import_selected(case_id="case_test", selected_path=source)
        second = importer.import_selected(case_id="case_test", selected_path=source)

        assert first.source_id != second.source_id
        assert first.artifact_id != second.artifact_id
        assert first.object_path == second.object_path
        assert first.created_new_object is True
        assert second.created_new_object is False
        assert connection.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[0] == 2
        assert len(object_files(data_root)) == 1
    finally:
        connection.close()


def test_missing_case_fails_before_object_publication(tmp_path: Path) -> None:
    store, source_root, data_root = make_store(tmp_path)
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    source = source_root / "receipt.txt"
    source.write_bytes(b"synthetic receipt")

    try:
        with pytest.raises(CaseNotFoundError):
            SourceImporter(connection, store).import_selected(
                case_id="case_missing", selected_path=source
            )

        assert object_files(data_root) == []
        assert connection.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0
    finally:
        connection.close()


def test_metadata_failure_rolls_back_and_removes_new_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, source_root, data_root = make_store(tmp_path)
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    add_case(connection)
    connection.execute(
        """
        INSERT INTO artifact (
            id, case_id, source_id, type, relative_path,
            sha256, size_bytes, created_by_run, created_at
        ) VALUES ('art_collision', 'case_test', NULL, 'source',
                  'objects/existing', ?, 0, NULL, ?)
        """,
        ("0" * 64, "2026-08-01T00:00:00.000000Z"),
    )
    source = source_root / "new.txt"
    source.write_bytes(b"new synthetic bytes")

    def colliding_id(prefix: str) -> str:
        return "src_should_rollback" if prefix == "src" else "art_collision"

    monkeypatch.setattr(source_import_module, "_new_id", colliding_id)

    try:
        with pytest.raises(SourceMetadataError):
            SourceImporter(connection, store).import_selected(
                case_id="case_test", selected_path=source
            )

        assert object_files(data_root) == []
        assert connection.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[0] == 1
        assert connection.in_transaction is False
    finally:
        connection.close()
