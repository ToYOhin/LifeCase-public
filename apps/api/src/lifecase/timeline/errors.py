"""Safe typed Timeline failures."""


class TimelineError(RuntimeError):
    """Base Timeline error."""


class TimelineValidationError(TimelineError):
    """Timeline request bounds are invalid."""


class TimelineCaseNotFoundError(TimelineError):
    """Requested Case does not exist."""


class TimelineIntegrityError(TimelineError):
    """Persisted Timeline authority is inconsistent."""


class TimelinePersistenceError(TimelineError):
    """Timeline could not be read safely."""
