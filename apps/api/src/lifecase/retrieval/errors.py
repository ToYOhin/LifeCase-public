"""Safe typed failures for local retrieval."""


class RetrievalError(RuntimeError):
    """Base class for retrieval failures."""


class RetrievalRequestError(RetrievalError):
    """Raised when a retrieval request violates a public boundary."""


class RetrievalQueryError(RetrievalRequestError):
    """Raised when a query is empty, out of bounds, or invalid FTS syntax."""


class RetrievalLimitError(RetrievalRequestError):
    """Raised when a requested result limit is outside configured bounds."""


class RetrievalIndexError(RetrievalError):
    """Raised when the rebuildable FTS projection is unavailable."""


class RetrievalIntegrityError(RetrievalIndexError):
    """Raised when a projection entry disagrees with authoritative evidence."""
