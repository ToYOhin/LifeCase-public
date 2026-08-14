"""Safe typed failures for deterministic Evidence Context construction."""


class EvidenceContextError(RuntimeError):
    """Base class for Evidence Context failures."""


class EvidenceContextRequestError(EvidenceContextError):
    """Raised when a request violates its public boundary."""


class EvidenceContextCaseNotFoundError(EvidenceContextError):
    """Raised when the requested Case does not exist."""


class EvidenceContextConfigurationError(EvidenceContextError):
    """Raised when local retrieval is unavailable or misconfigured."""


class EvidenceContextIntegrityError(EvidenceContextError):
    """Raised when a citation or authoritative segment fails validation."""


class EvidenceContextPersistenceError(EvidenceContextError):
    """Raised when authoritative state cannot be read safely."""
