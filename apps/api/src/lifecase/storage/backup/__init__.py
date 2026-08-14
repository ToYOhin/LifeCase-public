"""M6-02 versioned whole-authority backup and clean-target restore."""

from .errors import (
    BackupIntegrityError,
    BackupLimitError,
    BackupNotFoundError,
    BackupPersistenceError,
    BackupRestoreError,
    BackupValidationError,
    RestoreApplyError,
    RestoreTargetNotCleanError,
)
from .migration import apply_migrations_with_backup
from .models import (
    BACKUP_FORMAT_VERSION,
    BackupArtifact,
    BackupDownload,
    BackupPolicy,
    RestoreResult,
    RestoreValidation,
)
from .service import BackupService, verify_backup

__all__ = [
    "BACKUP_FORMAT_VERSION",
    "BackupArtifact",
    "BackupDownload",
    "BackupIntegrityError",
    "BackupLimitError",
    "BackupNotFoundError",
    "BackupPersistenceError",
    "BackupPolicy",
    "BackupRestoreError",
    "BackupService",
    "BackupValidationError",
    "apply_migrations_with_backup",
    "RestoreApplyError",
    "RestoreResult",
    "RestoreTargetNotCleanError",
    "RestoreValidation",
    "verify_backup",
]
