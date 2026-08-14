"""Safe M6-02 backup and restore failures."""


class BackupRestoreError(RuntimeError):
    """Base class for local backup and restore failures."""


class BackupValidationError(BackupRestoreError):
    """Raised when a command or portable backup contract is invalid."""


class BackupIntegrityError(BackupRestoreError):
    """Raised when authority, archive bytes, or restored state fail verification."""


class BackupLimitError(BackupRestoreError):
    """Raised when bounded archive limits would be exceeded."""


class BackupPersistenceError(BackupRestoreError):
    """Raised when a backup cannot be created, retained, or read atomically."""


class BackupNotFoundError(BackupRestoreError):
    """Raised when a retained backup or validation ticket does not exist."""


class RestoreTargetNotCleanError(BackupRestoreError):
    """Raised when restore would touch an existing target."""


class RestoreApplyError(BackupRestoreError):
    """Raised when a verified restore cannot be published atomically."""
