"""Transactional maintenance for the rebuildable FTS5 projection."""

import hashlib
import sqlite3

from lifecase.retrieval.errors import RetrievalIndexError, RetrievalIntegrityError
from lifecase.retrieval.models import RetrievalIndexStatus
from lifecase.retrieval.schema import (
    CREATE_FTS_TABLE,
    FTS_TABLE_NAME,
    POPULATE_FTS_TABLE,
    projection_exists,
)


def delete_source_projection(connection: sqlite3.Connection, source_id: str) -> None:
    """Delete one Source projection inside the caller's write transaction."""

    connection.execute(
        f"DELETE FROM {FTS_TABLE_NAME} WHERE source_id = ?",
        (source_id,),
    )


def insert_source_projection(connection: sqlite3.Connection, source_id: str) -> None:
    """Index current authoritative segments inside the caller's transaction."""

    connection.execute(
        f"""
        INSERT INTO {FTS_TABLE_NAME} (rowid, source_id, segment_id, text, text_sha256)
        SELECT rowid, source_id, id, text, text_sha256
        FROM source_segment
        WHERE source_id = ?
        ORDER BY sequence, id
        """,
        (source_id,),
    )
    _verify_source_projection(connection, source_id)


def rebuild_projection(connection: sqlite3.Connection) -> RetrievalIndexStatus:
    """Replace the complete FTS projection from authoritative SourceSegments."""

    if connection.in_transaction:
        raise RetrievalIndexError("Retrieval index rebuild requires an idle connection")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(f"DROP TABLE IF EXISTS {FTS_TABLE_NAME}")
        connection.execute(CREATE_FTS_TABLE)
        connection.execute(POPULATE_FTS_TABLE)
        status = verify_projection(connection)
        connection.execute("COMMIT")
        return status
    except RetrievalIndexError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error as error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise RetrievalIndexError("Retrieval index rebuild failed") from error


def verify_projection(connection: sqlite3.Connection) -> RetrievalIndexStatus:
    """Verify FTS internals and exact agreement with authoritative rows."""

    try:
        if not projection_exists(connection):
            raise RetrievalIndexError("Retrieval index is unavailable")
        connection.execute(
            f"INSERT INTO {FTS_TABLE_NAME}({FTS_TABLE_NAME}) VALUES ('integrity-check')"
        )
        authoritative = _count(connection, "source_segment")
        indexed = _count(connection, FTS_TABLE_NAME)
        if authoritative != indexed:
            raise RetrievalIntegrityError("Retrieval index does not match authoritative evidence")

        mismatch = connection.execute(
            f"""
            SELECT 1
            FROM {FTS_TABLE_NAME} AS projection
            LEFT JOIN source_segment AS segment
              ON segment.rowid = projection.rowid
             AND segment.id = projection.segment_id
             AND segment.source_id = projection.source_id
            WHERE segment.id IS NULL
               OR segment.text <> projection.text
               OR segment.text_sha256 <> projection.text_sha256
            LIMIT 1
            """
        ).fetchone()
        if mismatch is not None:
            raise RetrievalIntegrityError("Retrieval index does not match authoritative evidence")

        for row in connection.execute("SELECT text, text_sha256 FROM source_segment"):
            if hashlib.sha256(str(row[0]).encode()).hexdigest() != str(row[1]):
                raise RetrievalIntegrityError("Authoritative evidence hash validation failed")
        return RetrievalIndexStatus(
            authoritative_segments=authoritative,
            indexed_segments=indexed,
        )
    except RetrievalIndexError:
        raise
    except sqlite3.Error as error:
        raise RetrievalIndexError("Retrieval index verification failed") from error


def _verify_source_projection(connection: sqlite3.Connection, source_id: str) -> None:
    authoritative = connection.execute(
        "SELECT COUNT(*) FROM source_segment WHERE source_id = ?", (source_id,)
    ).fetchone()
    indexed = connection.execute(
        f"SELECT COUNT(*) FROM {FTS_TABLE_NAME} WHERE source_id = ?", (source_id,)
    ).fetchone()
    if authoritative is None or indexed is None or int(authoritative[0]) != int(indexed[0]):
        raise RetrievalIntegrityError("Source retrieval projection is incomplete")


def _count(connection: sqlite3.Connection, table_name: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    if row is None:
        raise RetrievalIndexError("Retrieval index count is unavailable")
    return int(row[0])
