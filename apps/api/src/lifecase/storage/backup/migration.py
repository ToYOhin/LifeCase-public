"""Pre-migration backup ordering for existing LifeCase authority."""

import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

from lifecase.storage.migrations import (
    MIGRATIONS,
    Migration,
    apply_migrations,
    has_applied_migrations,
    pending_migrations,
)

from .models import BackupArtifact
from .service import BackupService

BackupFactory = Callable[[], BackupArtifact]


def apply_migrations_with_backup(
    connection: sqlite3.Connection,
    database_path: Path,
    data_root: Path,
    migrations: Sequence[Migration] = MIGRATIONS,
    *,
    backup_factory: BackupFactory | None = None,
) -> BackupArtifact | None:
    """Back up existing authority iff migrations are pending, then migrate."""

    pending = pending_migrations(connection, migrations)
    backup: BackupArtifact | None = None
    if pending and has_applied_migrations(connection):
        create_backup = backup_factory or BackupService(connection, database_path, data_root).create
        backup = create_backup()
    apply_migrations(connection, migrations)
    return backup
