"""Typed values for the fixed M7 thirty-case evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EvaluationCaseType(StrEnum):
    """Closed case-type distribution required by M7-02."""

    RETURN_REFUND = "return_refund"
    WARRANTY = "warranty"
    REIMBURSEMENT = "reimbursement"
    SUBSCRIPTION_CANCELLATION = "subscription_cancellation"
    TRAVEL_LOGISTICS = "travel_logistics"


class EvaluationSupportLevel(StrEnum):
    """Whether a fixture maps to a reviewed product template."""

    REVIEWED_TEMPLATE = "reviewed_template"
    EVALUATION_ONLY = "evaluation_only"


class EvaluationProcessingStatus(StrEnum):
    """Truthful status of the recorded evaluation observation."""

    PRODUCT_PROCESSED = "product_processed"
    EVALUATION_ONLY_RECORDED = "evaluation_only_recorded"


class CriticalField(StrEnum):
    """The five cross-template release fields."""

    MERCHANT = "merchant"
    AMOUNT = "amount"
    PURCHASE_OR_EVENT_DATE = "purchase_or_event_date"
    ORDER_OR_REFERENCE = "order_or_reference"
    DESIRED_OUTCOME = "desired_outcome"


class ObservedFieldStatus(StrEnum):
    """Recorded output disposition before scoring."""

    FACT = "fact"
    UNCERTAIN = "uncertain"
    UNSUPPORTED = "unsupported"


class CriticalFieldClassification(StrEnum):
    """Documented scoring buckets; only the first three can pass."""

    EXACT = "exact"
    ACCEPTABLE = "acceptable"
    APPROPRIATELY_UNCERTAIN = "appropriately_uncertain"
    UNSUPPORTED_OR_WRONG = "unsupported_or_wrong"


class EvaluationDeadlineKind(StrEnum):
    """Closed Deadline vocabulary mirrored by the evaluation contract."""

    EXPLICIT = "explicit"
    CALCULATED = "calculated"
    SUGGESTED = "suggested"


_EXPECTED_DISTRIBUTION = {
    EvaluationCaseType.RETURN_REFUND: 8,
    EvaluationCaseType.WARRANTY: 8,
    EvaluationCaseType.REIMBURSEMENT: 7,
    EvaluationCaseType.SUBSCRIPTION_CANCELLATION: 4,
    EvaluationCaseType.TRAVEL_LOGISTICS: 3,
}


@dataclass(frozen=True, slots=True)
class EvaluationThresholds:
    """Frozen M7 release thresholds."""

    product_processed_cases: int = 27
    critical_field_score: float = 0.90
    evidence_validity: float = 1.00
    deadline_safety: float = 1.00
    action_quality_average: float = 10.0
    action_safety_failures: int = 0


@dataclass(frozen=True, slots=True)
class EvaluationCaseResult:
    """Safe, body-free result for one corpus case."""

    case_id: str
    case_type: EvaluationCaseType
    support_level: EvaluationSupportLevel
    processing_status: EvaluationProcessingStatus
    field_classifications: tuple[tuple[CriticalField, CriticalFieldClassification], ...]
    evidence_valid: int
    evidence_total: int
    deadline_correct: int
    deadline_total: int
    action_score: int
    action_safety_failures: int
    failure_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Deterministic aggregate report with no wall-clock or machine-path data."""

    schema_version: str
    corpus_version: str
    corpus_sha256: str
    case_results: tuple[EvaluationCaseResult, ...]
    thresholds: EvaluationThresholds
    product_processed_cases: int
    evaluation_only_cases: int
    critical_field_passes: int
    critical_field_total: int
    evidence_valid: int
    evidence_total: int
    deadline_correct: int
    deadline_total: int
    action_score_total: int
    action_safety_failures: int
    overall_pass: bool

    @property
    def distribution(self) -> dict[str, int]:
        counts = {case_type.value: 0 for case_type in EvaluationCaseType}
        for result in self.case_results:
            counts[result.case_type.value] += 1
        return counts

    @property
    def expected_distribution(self) -> dict[str, int]:
        return {case_type.value: count for case_type, count in _EXPECTED_DISTRIBUTION.items()}

    @property
    def critical_field_score(self) -> float:
        return self.critical_field_passes / self.critical_field_total

    @property
    def evidence_validity(self) -> float:
        return self.evidence_valid / self.evidence_total

    @property
    def deadline_safety(self) -> float:
        return self.deadline_correct / self.deadline_total

    @property
    def action_quality_average(self) -> float:
        return self.action_score_total / len(self.case_results)


EXPECTED_DISTRIBUTION = dict(_EXPECTED_DISTRIBUTION)
