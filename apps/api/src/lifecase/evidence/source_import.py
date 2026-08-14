"""Evidence intake service for immutable local source files."""

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from lifecase.storage.object_store import ContentAddressedObjectStore, StoredObject

_IMPORT_LOCK = threading.Lock()


class SourceImportError(RuntimeError):
    """Base class for source metadata import failures."""


class CaseNotFoundError(SourceImportError):
    """Raised when the requested owning case does not exist."""


class SourceMetadataError(SourceImportError):
    """Raised when Source and Artifact metadata cannot be committed."""


class SourceDisplayNameError(SourceImportError):
    """Raised when an untrusted upload name is unsafe for display metadata."""


@dataclass(frozen=True, slots=True)
class ImportedSource:
    """Portable result returned after source bytes and metadata are durable."""

    source_id: str
    artifact_id: str
    sha256: str
    media_type: str
    size_bytes: int
    object_path: str
    trust_label: str
    created_new_object: bool


class SourceImporter:
    """Coordinate object publication with Source and Artifact metadata."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        object_store: ContentAddressedObjectStore,
    ) -> None:
        self._connection = connection
        self._object_store = object_store

    def import_selected(
        self,
        *,
        case_id: str,
        selected_path: Path,
        display_name: str | None = None,
    ) -> ImportedSource:
        """Import one explicit selection as one Source and one source Artifact.

        Repeated explicit imports intentionally create separate metadata records while
        identical bytes continue to share one content-addressed object.
        """

        _require_ready_connection(self._connection)
        _require_case(self._connection, case_id)
        original_name = _validate_display_name(display_name or selected_path.name)

        with _IMPORT_LOCK:
            stored = self._object_store.store(selected_path)
            try:
                return self._record_metadata(case_id, stored, original_name)
            except Exception:
                self._discard_unreferenced_new_object(stored)
                raise

    def _record_metadata(
        self, case_id: str, stored: StoredObject, original_name: str
    ) -> ImportedSource:
        source_id = _new_id("src")
        artifact_id = _new_id("art")
        imported_at = _utc_now()

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            _require_case(self._connection, case_id)
            self._connection.execute(
                """
                INSERT INTO source (
                    id, case_id, original_name, media_type, sha256,
                    size_bytes, object_path, trust_label, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'untrusted', ?)
                """,
                (
                    source_id,
                    case_id,
                    original_name,
                    stored.media_type,
                    stored.sha256,
                    stored.size_bytes,
                    stored.relative_path,
                    imported_at,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO artifact (
                    id, case_id, source_id, type, relative_path,
                    sha256, size_bytes, created_by_run, created_at
                ) VALUES (?, ?, ?, 'source', ?, ?, ?, NULL, ?)
                """,
                (
                    artifact_id,
                    case_id,
                    source_id,
                    stored.relative_path,
                    stored.sha256,
                    stored.size_bytes,
                    imported_at,
                ),
            )
            self._connection.execute("COMMIT")
        except SourceImportError:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SourceMetadataError("Source metadata transaction was rolled back") from error

        return ImportedSource(
            source_id=source_id,
            artifact_id=artifact_id,
            sha256=stored.sha256,
            media_type=stored.media_type,
            size_bytes=stored.size_bytes,
            object_path=stored.relative_path,
            trust_label="untrusted",
            created_new_object=stored.created_new_object,
        )

    def _discard_unreferenced_new_object(self, stored: StoredObject) -> None:
        if not stored.created_new_object:
            return
        try:
            reference = self._connection.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM source WHERE object_path = ?
                    UNION ALL
                    SELECT 1 FROM artifact WHERE relative_path = ?
                )
                """,
                (stored.relative_path, stored.relative_path),
            ).fetchone()
        except sqlite3.Error as error:
            raise SourceMetadataError(
                "Metadata failed and the new object could not be checked for cleanup"
            ) from error
        if reference is None or reference[0] == 0:
            self._object_store.discard_new_object(stored)


def _require_ready_connection(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise SourceMetadataError("Source import requires a connection outside a transaction")
    try:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
    except sqlite3.Error as error:
        raise SourceMetadataError("Source database connection is unavailable") from error
    if foreign_keys is None or foreign_keys[0] != 1:
        raise SourceMetadataError("SQLite foreign keys must be enabled for source import")


def _require_case(connection: sqlite3.Connection, case_id: str) -> None:
    try:
        row = connection.execute(
            "SELECT 1 FROM case_record WHERE id = ?",
            (case_id,),
        ).fetchone()
    except sqlite3.Error as error:
        raise SourceMetadataError("Case lookup failed during source import") from error
    if row is None:
        raise CaseNotFoundError(f"Case does not exist: {case_id}")


def _new_id(prefix: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"{prefix}_{timestamp}_{uuid4().hex}"


def _validate_display_name(value: str) -> str:
    name = value.strip()
    if (
        not name
        or len(name) > 255
        or name in {".", ".."}
        or "\x00" in name
        or "/" in name
        or "\\" in name
    ):
        raise SourceDisplayNameError("Source display name is invalid")
    return name


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
