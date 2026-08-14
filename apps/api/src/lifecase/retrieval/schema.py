"""SQLite schema constants for the rebuildable retrieval projection."""

import sqlite3

FTS_TABLE_NAME = "source_segment_fts"

CREATE_FTS_TABLE = f"""
CREATE VIRTUAL TABLE {FTS_TABLE_NAME} USING fts5(
    source_id UNINDEXED,
    segment_id UNINDEXED,
    text,
    text_sha256 UNINDEXED,
    tokenize='trigram'
)
"""

POPULATE_FTS_TABLE = f"""
INSERT INTO {FTS_TABLE_NAME} (rowid, source_id, segment_id, text, text_sha256)
SELECT rowid, source_id, id, text, text_sha256
FROM source_segment
ORDER BY source_id, sequence, id
"""


def projection_exists(connection: sqlite3.Connection) -> bool:
    """Return whether the configured FTS virtual table is present."""

    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (FTS_TABLE_NAME,),
    ).fetchone()
    return row is not None
