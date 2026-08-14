"""Case-scoped local lexical retrieval over authoritative evidence segments."""

from lifecase.retrieval.errors import (
    RetrievalError,
    RetrievalIndexError,
    RetrievalIntegrityError,
    RetrievalLimitError,
    RetrievalQueryError,
    RetrievalRequestError,
)
from lifecase.retrieval.models import RetrievalConfig, RetrievalIndexStatus, RetrievalResult
from lifecase.retrieval.service import RetrievalService

__all__ = [
    "RetrievalConfig",
    "RetrievalError",
    "RetrievalIndexError",
    "RetrievalIndexStatus",
    "RetrievalIntegrityError",
    "RetrievalLimitError",
    "RetrievalQueryError",
    "RetrievalRequestError",
    "RetrievalResult",
    "RetrievalService",
]
