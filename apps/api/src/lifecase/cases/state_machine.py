"""Deterministic Case lifecycle transition rules."""

from collections.abc import Mapping

from lifecase.cases.models import CaseStatus

ALLOWED_TRANSITIONS: Mapping[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.INTAKE: frozenset({CaseStatus.NEEDS_REVIEW, CaseStatus.READY, CaseStatus.ABANDONED}),
    CaseStatus.NEEDS_REVIEW: frozenset({CaseStatus.INTAKE, CaseStatus.READY, CaseStatus.ABANDONED}),
    CaseStatus.READY: frozenset(
        {CaseStatus.NEEDS_REVIEW, CaseStatus.IN_PROGRESS, CaseStatus.ABANDONED}
    ),
    CaseStatus.IN_PROGRESS: frozenset(
        {
            CaseStatus.NEEDS_REVIEW,
            CaseStatus.WAITING_EXTERNAL,
            CaseStatus.BLOCKED,
            CaseStatus.RESOLVED,
            CaseStatus.ABANDONED,
        }
    ),
    CaseStatus.WAITING_EXTERNAL: frozenset(
        {
            CaseStatus.IN_PROGRESS,
            CaseStatus.BLOCKED,
            CaseStatus.RESOLVED,
            CaseStatus.ABANDONED,
        }
    ),
    CaseStatus.BLOCKED: frozenset(
        {
            CaseStatus.NEEDS_REVIEW,
            CaseStatus.IN_PROGRESS,
            CaseStatus.WAITING_EXTERNAL,
            CaseStatus.ABANDONED,
        }
    ),
    CaseStatus.RESOLVED: frozenset({CaseStatus.ARCHIVED}),
    CaseStatus.ABANDONED: frozenset({CaseStatus.ARCHIVED}),
    CaseStatus.ARCHIVED: frozenset(),
}


def is_transition_allowed(prior: CaseStatus, target: CaseStatus) -> bool:
    """Return whether the explicit lifecycle edge is permitted."""

    return target in ALLOWED_TRANSITIONS[prior]
