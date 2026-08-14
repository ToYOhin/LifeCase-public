"""SQLite connection configuration for LifeCase."""

import sqlite3
from contextlib import suppress
from pathlib import Path


class DatabaseConfigurationError(RuntimeError):
    """Raised when a SQLite connection cannot satisfy LifeCase invariants."""


class Fts5UnavailableError(DatabaseConfigurationError):
    """Raised when SQLite cannot provide the required FTS5 search features."""


def connect_database(path: Path) -> sqlite3.Connection:
    """Open a configured SQLite database owned by LifeCase.

    Transaction control remains explicit so the migration runner can use
    ``BEGIN IMMEDIATE`` and guarantee a rollback on a failed migration.
    """

    database_path = path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        database_path,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row

    try:
        connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or foreign_keys[0] != 1:
            raise DatabaseConfigurationError("SQLite foreign keys could not be enabled")

        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
        if journal_mode is None or str(journal_mode[0]).lower() != "wal":
            raise DatabaseConfigurationError("SQLite WAL journal mode could not be enabled")

        connection.execute("PRAGMA busy_timeout = 5000")
        require_fts5(connection)
    except Exception:
        connection.close()
        raise

    return connection


def require_fts5(connection: sqlite3.Connection) -> None:
    """Exercise the exact FTS5 features used by the retrieval projection."""

    table_name = "lifecase_fts5_probe"
    try:
        connection.execute(
            f"CREATE VIRTUAL TABLE temp.{table_name} USING fts5(text, tokenize='trigram')"
        )
        connection.execute(
            f"INSERT INTO temp.{table_name}(text) VALUES ('runtime capability probe')"
        )
        row = connection.execute(
            f"SELECT bm25({table_name}) FROM temp.{table_name} "
            f"WHERE {table_name} MATCH 'capability'"
        ).fetchone()
        if row is None:
            raise sqlite3.OperationalError
    except sqlite3.Error as error:
        raise Fts5UnavailableError("SQLite FTS5 with the trigram tokenizer is required") from error
    finally:
        with suppress(sqlite3.Error):
            connection.execute(f"DROP TABLE IF EXISTS temp.{table_name}")
