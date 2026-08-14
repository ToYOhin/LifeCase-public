"""Safe Evidence Pack failures."""


class EvidencePackError(RuntimeError):
    """Base class for M6-01 export failures."""


class EvidencePackValidationError(EvidencePackError):
    """Raised when an export identifier or archive contract is invalid."""


class EvidencePackCaseNotFoundError(EvidencePackError):
    """Raised when the requested owning Case does not exist."""


class EvidencePackArtifactNotFoundError(EvidencePackError):
    """Raised when a requested pack Artifact does not exist."""


class EvidencePackIntegrityError(EvidencePackError):
    """Raised when authority, a file, or an archive fails closed validation."""


class EvidencePackLimitError(EvidencePackError):
    """Raised when bounded export limits would be exceeded."""


class EvidencePackPersistenceError(EvidencePackError):
    """Raised when an atomic export cannot be read, written, or committed."""
