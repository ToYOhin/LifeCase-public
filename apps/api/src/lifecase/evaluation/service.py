"""Strict corpus loading, deterministic scoring, and safe M7 reporting."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml  # type: ignore[import-untyped]

from lifecase.evaluation.models import (
    EXPECTED_DISTRIBUTION,
    CriticalField,
    CriticalFieldClassification,
    EvaluationCaseResult,
    EvaluationCaseType,
    EvaluationDeadlineKind,
    EvaluationProcessingStatus,
    EvaluationReport,
    EvaluationSupportLevel,
    EvaluationThresholds,
    ObservedFieldStatus,
)

_CORPUS_SCHEMA = "lifecase.m7.corpus.v1"
_CASE_SCHEMA = "lifecase.m7.synthetic_case.v1"
_EXPECTED_CLAIMS_SCHEMA = "lifecase.m7.expected_claims.v1"
_EXPECTED_GAPS_SCHEMA = "lifecase.m7.expected_gaps.v1"
_EXPECTED_DEADLINES_SCHEMA = "lifecase.m7.expected_deadlines.v1"
_EXPECTED_ACTIONS_SCHEMA = "lifecase.m7.expected_actions.v1"
_OBSERVED_SCHEMA = "lifecase.m7.observed.v1"
_REPORT_SCHEMA = "lifecase.m7.evaluation_report.v1"
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9-]{0,99}\Z")
_FIELD_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ACTION_RUBRIC_KEYS = (
    "addresses_goal",
    "uses_confirmed_facts",
    "orders_prerequisites",
    "identifies_blockers",
    "respects_risk_and_approval",
    "communication_is_concise_and_truthful",
)
_REVIEWED_TYPES = frozenset(
    {
        EvaluationCaseType.RETURN_REFUND,
        EvaluationCaseType.WARRANTY,
        EvaluationCaseType.REIMBURSEMENT,
        EvaluationCaseType.SUBSCRIPTION_CANCELLATION,
    }
)
_CASE_REQUIRED_FILES = frozenset(
    {
        "metadata.yaml",
        "input/evidence.txt",
        "expected/claims.json",
        "expected/gaps.json",
        "expected/deadlines.json",
        "expected/actions.json",
        "observed.json",
    }
)
_MAX_CORPUS_FILES = 256
_MAX_MEMBER_BYTES = 1_000_000
_MAX_CORPUS_BYTES = 16_000_000


class _StrictSafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Safe YAML loader that rejects duplicate metadata keys."""


def _construct_yaml_mapping(loader: Any, node: Any, deep: bool = False) -> dict[str, object]:
    loader.flatten_mapping(node)
    result: dict[str, object] = {}
    for key, value in loader.construct_pairs(node, deep=deep):
        if not isinstance(key, str) or key in result:
            raise yaml.constructor.ConstructorError(None, None, "duplicate key", node.start_mark)
        result[key] = value
    return result


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_yaml_mapping,
)


class EvaluationCorpusError(RuntimeError):
    """Safe typed failure that never includes fixture content or machine paths."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EvaluationService:
    """Evaluate one immutable local corpus without writing business authority."""

    def __init__(self, thresholds: EvaluationThresholds | None = None) -> None:
        self._thresholds = thresholds or EvaluationThresholds()

    def evaluate(self, corpus_root: Path) -> EvaluationReport:
        root = _validated_root(corpus_root)
        manifest = _load_json(root / "corpus.json", "corpus_manifest_invalid")
        _require_keys(
            manifest,
            frozenset({"schema_version", "corpus_version", "distribution", "cases"}),
            "corpus_manifest_invalid",
        )
        if manifest["schema_version"] != _CORPUS_SCHEMA:
            _fail("corpus_version_unsupported", "Corpus format is not supported")
        corpus_version = _bounded_text(manifest["corpus_version"], "corpus_manifest_invalid", 40)
        distribution = _distribution(manifest["distribution"])
        expected_distribution = {
            case_type.value: count for case_type, count in EXPECTED_DISTRIBUTION.items()
        }
        if distribution != expected_distribution:
            _fail("corpus_distribution_invalid", "Corpus distribution is invalid")
        case_paths = _case_paths(manifest["cases"])
        if len(case_paths) != 30:
            _fail("corpus_case_count_invalid", "Corpus must contain exactly thirty cases")
        if len({path.casefold() for path in case_paths}) != len(case_paths):
            _fail("corpus_case_id_collision", "Corpus Case paths collide")

        _validate_corpus_members(root, case_paths)

        actual_dirs = tuple(
            sorted(item.name for item in root.iterdir() if item.is_dir() and not item.is_symlink())
        )
        if tuple(sorted(case_paths)) != actual_dirs:
            _fail("corpus_membership_invalid", "Corpus Case membership is invalid")

        results = tuple(self._evaluate_case(root, relative) for relative in case_paths)
        if len({result.case_id.casefold() for result in results}) != len(results):
            _fail("corpus_case_id_collision", "Corpus Case identifiers collide")
        result_distribution = {case_type.value: 0 for case_type in EvaluationCaseType}
        for result in results:
            result_distribution[result.case_type.value] += 1
        if result_distribution != expected_distribution:
            _fail("corpus_distribution_invalid", "Corpus Case metadata distribution is invalid")

        product_processed = sum(
            result.processing_status is EvaluationProcessingStatus.PRODUCT_PROCESSED
            for result in results
        )
        evaluation_only = len(results) - product_processed
        critical_passes = sum(
            classification is not CriticalFieldClassification.UNSUPPORTED_OR_WRONG
            for result in results
            for _field, classification in result.field_classifications
        )
        critical_total = sum(len(result.field_classifications) for result in results)
        evidence_valid = sum(result.evidence_valid for result in results)
        evidence_total = sum(result.evidence_total for result in results)
        deadline_correct = sum(result.deadline_correct for result in results)
        deadline_total = sum(result.deadline_total for result in results)
        action_score_total = sum(result.action_score for result in results)
        action_safety_failures = sum(result.action_safety_failures for result in results)
        if min(critical_total, evidence_total, deadline_total, len(results)) <= 0:
            _fail("evaluation_denominator_invalid", "Evaluation denominator is invalid")

        critical_score = critical_passes / critical_total
        evidence_score = evidence_valid / evidence_total
        deadline_score = deadline_correct / deadline_total
        action_average = action_score_total / len(results)
        thresholds = self._thresholds
        overall_pass = all(
            (
                product_processed >= thresholds.product_processed_cases,
                critical_score >= thresholds.critical_field_score,
                evidence_score >= thresholds.evidence_validity,
                deadline_score >= thresholds.deadline_safety,
                action_average >= thresholds.action_quality_average,
                action_safety_failures == thresholds.action_safety_failures,
            )
        )
        return EvaluationReport(
            schema_version=_REPORT_SCHEMA,
            corpus_version=corpus_version,
            corpus_sha256=_corpus_sha256(root),
            case_results=results,
            thresholds=thresholds,
            product_processed_cases=product_processed,
            evaluation_only_cases=evaluation_only,
            critical_field_passes=critical_passes,
            critical_field_total=critical_total,
            evidence_valid=evidence_valid,
            evidence_total=evidence_total,
            deadline_correct=deadline_correct,
            deadline_total=deadline_total,
            action_score_total=action_score_total,
            action_safety_failures=action_safety_failures,
            overall_pass=overall_pass,
        )

    def _evaluate_case(self, root: Path, relative: str) -> EvaluationCaseResult:
        case_root = root / relative
        if not case_root.is_dir() or case_root.is_symlink():
            _fail("corpus_case_missing", "Corpus Case is missing")
        members = frozenset(
            item.relative_to(case_root).as_posix()
            for item in case_root.rglob("*")
            if item.is_file() and not item.is_symlink()
        )
        if members != _CASE_REQUIRED_FILES:
            _fail("corpus_case_membership_invalid", "Corpus Case members are invalid")
        if any(item.is_symlink() for item in case_root.rglob("*")):
            _fail("corpus_path_invalid", "Corpus path is invalid")

        metadata = _load_yaml(case_root / "metadata.yaml", "case_metadata_invalid")
        _require_keys(
            metadata,
            frozenset(
                {
                    "schema_version",
                    "case_id",
                    "case_type",
                    "support_level",
                    "title",
                    "goal",
                    "input_files",
                }
            ),
            "case_metadata_invalid",
        )
        if metadata["schema_version"] != _CASE_SCHEMA:
            _fail("case_schema_unsupported", "Case fixture format is not supported")
        case_id = _identifier(metadata["case_id"], "case_metadata_invalid")
        if case_id != relative:
            _fail("case_scope_invalid", "Case fixture scope is invalid")
        case_type = _enum(EvaluationCaseType, metadata["case_type"], "case_metadata_invalid")
        support_level = _enum(
            EvaluationSupportLevel,
            metadata["support_level"],
            "case_metadata_invalid",
        )
        expected_support = (
            EvaluationSupportLevel.REVIEWED_TEMPLATE
            if case_type in _REVIEWED_TYPES
            else EvaluationSupportLevel.EVALUATION_ONLY
        )
        if support_level is not expected_support:
            _fail("case_support_level_invalid", "Case support level is invalid")
        _bounded_text(metadata["title"], "case_metadata_invalid", 160)
        _bounded_text(metadata["goal"], "case_metadata_invalid", 500)
        input_files = _portable_paths(metadata["input_files"], "case_metadata_invalid")
        if input_files != ("input/evidence.txt",):
            _fail("case_input_membership_invalid", "Case input membership is invalid")

        expected_claims = _load_json(
            case_root / "expected" / "claims.json", "expected_claims_invalid"
        )
        expected_gaps = _load_json(case_root / "expected" / "gaps.json", "expected_gaps_invalid")
        expected_deadlines = _load_json(
            case_root / "expected" / "deadlines.json", "expected_deadlines_invalid"
        )
        expected_actions = _load_json(
            case_root / "expected" / "actions.json", "expected_actions_invalid"
        )
        observed = _load_json(case_root / "observed.json", "observed_result_invalid")
        expected_gap_projection = _validate_expected_gaps(expected_gaps, case_id)
        gap_projection_valid = _score_gaps(
            case_id,
            expected_gap_projection,
            observed.get("gaps"),
        )

        (
            classifications,
            evidence_valid,
            evidence_total,
            field_failures,
            expected_claim_values,
        ) = _score_fields(
            case_root,
            case_id,
            input_files,
            expected_claims,
            observed,
        )
        deadline_correct, deadline_total, deadline_failures = _score_deadlines(
            case_id,
            expected_deadlines,
            observed,
            expected_claim_values,
        )
        action_score, action_failures, action_failure_codes = _score_actions(
            case_id,
            expected_actions,
            observed,
        )
        if not gap_projection_valid:
            action_failures += 1
            action_failure_codes = (*action_failure_codes, "gap_projection")
        _require_keys(
            observed,
            frozenset(
                {
                    "schema_version",
                    "case_id",
                    "processing_status",
                    "critical_fields",
                    "gaps",
                    "deadlines",
                    "actions",
                    "operational",
                }
            ),
            "observed_result_invalid",
        )
        if observed["schema_version"] != _OBSERVED_SCHEMA or observed["case_id"] != case_id:
            _fail("observed_scope_invalid", "Observed result scope is invalid")
        processing_status = _enum(
            EvaluationProcessingStatus,
            observed["processing_status"],
            "observed_result_invalid",
        )
        expected_status = (
            EvaluationProcessingStatus.PRODUCT_PROCESSED
            if support_level is EvaluationSupportLevel.REVIEWED_TEMPLATE
            else EvaluationProcessingStatus.EVALUATION_ONLY_RECORDED
        )
        if processing_status is not expected_status:
            _fail("observed_support_claim_invalid", "Observed product support claim is invalid")
        _validate_operational(observed["operational"])
        return EvaluationCaseResult(
            case_id=case_id,
            case_type=case_type,
            support_level=support_level,
            processing_status=processing_status,
            field_classifications=classifications,
            evidence_valid=evidence_valid,
            evidence_total=evidence_total,
            deadline_correct=deadline_correct,
            deadline_total=deadline_total,
            action_score=action_score,
            action_safety_failures=action_failures,
            failure_codes=tuple(
                sorted((*field_failures, *deadline_failures, *action_failure_codes))
            ),
        )


def render_report_json(report: EvaluationReport) -> str:
    """Render canonical UTF-8 JSON with stable ordering and no machine paths."""

    payload = _report_payload(report)
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def render_report_markdown(report: EvaluationReport) -> str:
    """Render a concise deterministic human report."""

    status = "PASS" if report.overall_pass else "FAIL"
    product_gate = (
        "PASS"
        if report.product_processed_cases >= report.thresholds.product_processed_cases
        else "FAIL"
    )
    lines = [
        "# M7-02 Thirty-case Evaluation",
        "",
        f"- Result: **{status}**",
        f"- Corpus: `{report.corpus_version}` (`{report.corpus_sha256}`)",
        "- Mode: offline recorded synthetic outputs; this is not live-model quality evidence.",
        f"- Cases: {len(report.case_results)}; distribution: "
        + ", ".join(f"{key}={value}" for key, value in report.distribution.items()),
        f"- Product-processed coverage: {report.product_processed_cases}/30 "
        f"(target >= {report.thresholds.product_processed_cases}) — {product_gate}",
        f"- Evaluation-only recorded cases: {report.evaluation_only_cases}/30; "
        "travel/logistics is not a reviewed UI template.",
        f"- Critical fields: {report.critical_field_passes}/{report.critical_field_total} "
        f"({report.critical_field_score:.2%}; target >= "
        f"{report.thresholds.critical_field_score:.0%})",
        f"- Evidence validity: {report.evidence_valid}/{report.evidence_total} "
        f"({report.evidence_validity:.2%}; target 100%)",
        f"- Deadline safety: {report.deadline_correct}/{report.deadline_total} "
        f"({report.deadline_safety:.2%}; target 100%)",
        f"- Action quality: {report.action_quality_average:.2f}/12 average "
        f"(target >= {report.thresholds.action_quality_average:.0f}/12); "
        f"safety failures={report.action_safety_failures}",
        "- Action rubric values are fixture-authored synthetic review records, "
        "not an independent user study.",
        "",
        "## Unsupported product capabilities",
        "",
        "- `travel_logistics`: evaluation record only; no reviewed Case template or Web selector.",
        "",
        "## Failures",
        "",
    ]
    failures = [
        f"- `{result.case_id}`: {', '.join(result.failure_codes)}"
        for result in report.case_results
        if result.failure_codes
    ]
    lines.extend(failures or ["- No per-case scoring failures."])
    if product_gate == "FAIL":
        lines.append("- `product_processing_coverage`: only reviewed product templates count.")
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```text",
            "uv run python -m lifecase.evaluation fixtures/synthetic-cases/m7-v1",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _score_fields(
    case_root: Path,
    case_id: str,
    input_files: tuple[str, ...],
    expected_document: Mapping[str, object],
    observed_document: Mapping[str, object],
) -> tuple[
    tuple[tuple[CriticalField, CriticalFieldClassification], ...],
    int,
    int,
    tuple[str, ...],
    dict[str, object],
]:
    _require_keys(
        expected_document,
        frozenset({"schema_version", "case_id", "critical_fields"}),
        "expected_claims_invalid",
    )
    if (
        expected_document["schema_version"] != _EXPECTED_CLAIMS_SCHEMA
        or expected_document["case_id"] != case_id
    ):
        _fail("expected_claims_scope_invalid", "Expected Claim scope is invalid")
    expected_items = _object_list(expected_document["critical_fields"], "expected_claims_invalid")
    observed_items = _object_list(
        observed_document.get("critical_fields"), "observed_result_invalid"
    )
    if len(expected_items) != len(CriticalField) or len(observed_items) != len(CriticalField):
        _fail("critical_field_membership_invalid", "Critical field membership is invalid")
    expected_by_field = _items_by_enum(expected_items, CriticalField, "expected_claims_invalid")
    observed_by_field = _items_by_enum(observed_items, CriticalField, "observed_result_invalid")
    classifications: list[tuple[CriticalField, CriticalFieldClassification]] = []
    evidence_valid = 0
    evidence_total = 0
    failures: list[str] = []
    expected_claim_values: dict[str, object] = {}
    for field in CriticalField:
        expected = expected_by_field[field]
        observed = observed_by_field[field]
        _require_keys(
            expected,
            frozenset(
                {"claim_id", "field", "expected_value", "acceptable_values", "allow_unknown"}
            ),
            "expected_claims_invalid",
        )
        _require_keys(
            observed,
            frozenset({"claim_id", "field", "status", "value", "citations"}),
            "observed_result_invalid",
        )
        claim_id = _identifier(expected["claim_id"], "expected_claims_invalid")
        if not claim_id.startswith(f"claim-{case_id}-"):
            _fail("expected_claims_scope_invalid", "Expected Claim scope is invalid")
        expected_claim_values[claim_id] = expected["expected_value"]
        if observed["claim_id"] != claim_id:
            _fail("observed_claim_scope_invalid", "Observed Claim scope is invalid")
        status = _enum(
            ObservedFieldStatus,
            observed["status"],
            "observed_result_invalid",
        )
        allow_unknown = expected["allow_unknown"]
        if not isinstance(allow_unknown, bool):
            _fail("expected_claims_invalid", "Expected Claim is invalid")
        acceptable = _value_list(expected["acceptable_values"], "expected_claims_invalid")
        classification = CriticalFieldClassification.UNSUPPORTED_OR_WRONG
        citations_valid = False
        if status is ObservedFieldStatus.UNCERTAIN and allow_unknown and observed["value"] is None:
            classification = CriticalFieldClassification.APPROPRIATELY_UNCERTAIN
            if observed["citations"] != []:
                _fail("uncertain_citation_invalid", "Uncertain output citation is invalid")
        elif status is ObservedFieldStatus.FACT:
            evidence_total += 1
            citations_valid = _citations_valid(
                case_root,
                case_id,
                input_files,
                observed["citations"],
                observed["value"],
            )
            if citations_valid:
                evidence_valid += 1
                normalized = _normalize_value(observed["value"])
                if normalized == _normalize_value(expected["expected_value"]):
                    classification = CriticalFieldClassification.EXACT
                elif any(normalized == _normalize_value(value) for value in acceptable):
                    classification = CriticalFieldClassification.ACCEPTABLE
        classifications.append((field, classification))
        if classification is CriticalFieldClassification.UNSUPPORTED_OR_WRONG:
            failures.append(f"critical_field_{field.value}")
        if status is ObservedFieldStatus.FACT and not citations_valid:
            failures.append(f"evidence_{field.value}")
    return (
        tuple(classifications),
        evidence_valid,
        evidence_total,
        tuple(failures),
        expected_claim_values,
    )


def _citations_valid(
    case_root: Path,
    case_id: str,
    input_files: tuple[str, ...],
    value: object,
    observed_value: object,
) -> bool:
    citations = _object_list(value, "observed_result_invalid")
    if not citations:
        return False
    for citation in citations:
        _require_keys(
            citation,
            frozenset(
                {
                    "case_id",
                    "source_id",
                    "segment_id",
                    "input_path",
                    "segment_sha256",
                    "quote_start",
                    "quote_end",
                    "quote_sha256",
                    "relation",
                }
            ),
            "observed_result_invalid",
        )
        if citation["case_id"] != case_id:
            return False
        if citation["source_id"] != f"source-{case_id}":
            return False
        if citation["segment_id"] != f"segment-{case_id}-000":
            return False
        input_path = _portable_path(citation["input_path"], "observed_result_invalid")
        if input_path not in input_files:
            return False
        path = case_root / input_path
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return False
        segment_sha256 = citation["segment_sha256"]
        quote_sha256 = citation["quote_sha256"]
        if not _is_sha256(segment_sha256) or not _is_sha256(quote_sha256):
            return False
        if hashlib.sha256(text.encode()).hexdigest() != segment_sha256:
            return False
        start = citation["quote_start"]
        end = citation["quote_end"]
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end <= start
            or end > len(text)
        ):
            return False
        quote = text[start:end]
        if hashlib.sha256(quote.encode()).hexdigest() != quote_sha256:
            return False
        if citation["relation"] != "supports":
            return False
        if not _quote_supports_value(quote, observed_value):
            return False
    return True


def _quote_supports_value(quote: str, value: object) -> bool:
    if isinstance(value, str):
        return cast(str, _normalize_value(value)) in cast(str, _normalize_value(quote))
    if isinstance(value, Mapping):
        typed = cast(Mapping[str, object], value)
        if set(typed) != {"amount_minor", "currency"}:
            return False
        amount_minor = typed["amount_minor"]
        currency = typed["currency"]
        if (
            not isinstance(amount_minor, int)
            or isinstance(amount_minor, bool)
            or not isinstance(currency, str)
        ):
            return False
        expected = f"{currency.upper()} {amount_minor / 100:.2f}"
        return expected.casefold() in quote.casefold()
    return False


def _score_deadlines(
    case_id: str,
    expected_document: Mapping[str, object],
    observed_document: Mapping[str, object],
    expected_claim_values: Mapping[str, object],
) -> tuple[int, int, tuple[str, ...]]:
    _require_keys(
        expected_document,
        frozenset({"schema_version", "case_id", "items"}),
        "expected_deadlines_invalid",
    )
    if (
        expected_document["schema_version"] != _EXPECTED_DEADLINES_SCHEMA
        or expected_document["case_id"] != case_id
    ):
        _fail("expected_deadlines_scope_invalid", "Expected Deadline scope is invalid")
    expected_items = _object_list(expected_document["items"], "expected_deadlines_invalid")
    observed_items = _object_list(observed_document.get("deadlines"), "observed_result_invalid")
    if not expected_items:
        _fail("evaluation_denominator_invalid", "Deadline denominator is invalid")
    observed_by_id = _items_by_identifier(observed_items, "deadline_id", "observed_result_invalid")
    correct = 0
    failures: list[str] = []
    for expected in expected_items:
        _require_deadline_shape(expected, "expected_deadlines_invalid")
        deadline_id = _identifier(expected["deadline_id"], "expected_deadlines_invalid")
        if not deadline_id.startswith(f"deadline-{case_id}"):
            _fail("expected_deadlines_scope_invalid", "Expected Deadline scope is invalid")
        if not _deadline_semantics_valid(expected):
            _fail("expected_deadlines_invalid", "Expected Deadline semantics are invalid")
        if not _deadline_basis_result_valid(expected, expected_claim_values):
            _fail("expected_deadlines_invalid", "Expected Deadline basis is invalid")
        observed = observed_by_id.get(deadline_id)
        if observed is None:
            failures.append(f"deadline_{deadline_id}")
            continue
        _require_deadline_shape(observed, "observed_result_invalid")
        if _deadline_semantics_valid(observed) and _normalize_value(observed) == _normalize_value(
            expected
        ):
            correct += 1
        else:
            failures.append(f"deadline_{deadline_id}")
    if len(observed_by_id) != len(expected_items):
        _fail("deadline_membership_invalid", "Deadline membership is invalid")
    return correct, len(expected_items), tuple(failures)


def _require_deadline_shape(item: Mapping[str, object], code: str) -> None:
    _require_keys(
        item,
        frozenset(
            {
                "deadline_id",
                "kind",
                "date_kind",
                "due_value",
                "timezone",
                "basis_claim_ids",
                "rule",
                "external_obligation",
            }
        ),
        code,
    )
    _identifier(item["deadline_id"], code)
    _enum(EvaluationDeadlineKind, item["kind"], code)
    if item["date_kind"] not in {"local_date", "utc_instant"}:
        _fail(code, "Deadline result is invalid")
    _bounded_text(item["due_value"], code, 40)
    zone = _bounded_text(item["timezone"], code, 100)
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        _fail(code, "Deadline timezone is invalid")
    _identifier_list(item["basis_claim_ids"], code)
    if not isinstance(item["external_obligation"], bool):
        _fail(code, "Deadline result is invalid")


def _deadline_semantics_valid(item: Mapping[str, object]) -> bool:
    kind = _enum(EvaluationDeadlineKind, item["kind"], "observed_result_invalid")
    basis = _identifier_list(item["basis_claim_ids"], "observed_result_invalid")
    rule = item["rule"]
    if not _deadline_value_valid(item):
        return False
    if kind is EvaluationDeadlineKind.EXPLICIT:
        return len(basis) == 1 and rule is None and item["external_obligation"] is True
    if kind is EvaluationDeadlineKind.CALCULATED:
        if len(basis) != 1 or not isinstance(rule, Mapping):
            return False
        typed_rule = cast(Mapping[str, object], rule)
        return (
            set(typed_rule) == {"rule_id", "rule_version", "parameters"}
            and typed_rule["rule_id"] == "calendar_days_after_basis"
            and typed_rule["rule_version"] == 1
            and isinstance(typed_rule["parameters"], Mapping)
            and item["external_obligation"] is False
        )
    return not basis and rule is None and item["external_obligation"] is False


def _deadline_value_valid(item: Mapping[str, object]) -> bool:
    value = cast(str, item["due_value"])
    try:
        if item["date_kind"] == "local_date":
            return date.fromisoformat(value).isoformat() == value
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and value.endswith("Z")


def _deadline_basis_result_valid(
    item: Mapping[str, object], expected_claim_values: Mapping[str, object]
) -> bool:
    kind = _enum(EvaluationDeadlineKind, item["kind"], "expected_deadlines_invalid")
    basis = _identifier_list(item["basis_claim_ids"], "expected_deadlines_invalid")
    if kind is EvaluationDeadlineKind.SUGGESTED:
        return True
    if any(claim_id not in expected_claim_values for claim_id in basis):
        return False
    basis_value = expected_claim_values[basis[0]]
    if not isinstance(basis_value, str):
        return False
    expected_due = _canonical_basis_value(basis_value, cast(str, item["date_kind"]))
    if expected_due is None:
        return False
    if kind is EvaluationDeadlineKind.CALCULATED:
        rule = cast(Mapping[str, object], item["rule"])
        parameters = cast(Mapping[str, object], rule["parameters"])
        days = parameters.get("days")
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 366:
            return False
        expected_due = _add_days(expected_due, cast(str, item["date_kind"]), days)
    return item["due_value"] == expected_due


def _canonical_basis_value(value: str, date_kind: str) -> str | None:
    try:
        if date_kind == "local_date":
            return date.fromisoformat(value).isoformat()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _add_days(value: str, date_kind: str, days: int) -> str:
    if date_kind == "local_date":
        return (date.fromisoformat(value) + timedelta(days=days)).isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(days=days)
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _score_actions(
    case_id: str,
    expected_document: Mapping[str, object],
    observed_document: Mapping[str, object],
) -> tuple[int, int, tuple[str, ...]]:
    _require_keys(
        expected_document,
        frozenset({"schema_version", "case_id", "items"}),
        "expected_actions_invalid",
    )
    if (
        expected_document["schema_version"] != _EXPECTED_ACTIONS_SCHEMA
        or expected_document["case_id"] != case_id
    ):
        _fail("expected_actions_scope_invalid", "Expected Action scope is invalid")
    expected_items = _object_list(expected_document["items"], "expected_actions_invalid")
    observed_items = _object_list(observed_document.get("actions"), "observed_result_invalid")
    if len(expected_items) != 1 or len(observed_items) != 1:
        _fail("action_membership_invalid", "Action membership is invalid")
    expected = expected_items[0]
    observed = observed_items[0]
    expected_keys = frozenset(
        {
            "action_id",
            "kind",
            "risk_level",
            "requires_approval",
            "may_execute",
            "blockers",
        }
    )
    _require_keys(expected, expected_keys, "expected_actions_invalid")
    _require_keys(
        observed,
        expected_keys | frozenset({"rubric", "safety_boundary_failure"}),
        "observed_result_invalid",
    )
    action_id = _identifier(expected["action_id"], "expected_actions_invalid")
    if not action_id.startswith(f"action-{case_id}"):
        _fail("expected_actions_scope_invalid", "Expected Action scope is invalid")
    if observed["action_id"] != action_id:
        _fail("observed_action_scope_invalid", "Observed Action scope is invalid")
    _field_identifier_list(expected["blockers"], "expected_actions_invalid")
    _field_identifier_list(observed["blockers"], "observed_result_invalid")
    if not isinstance(observed["rubric"], Mapping):
        _fail("action_rubric_invalid", "Action rubric is invalid")
    rubric = cast(Mapping[str, object], observed["rubric"])
    if set(rubric) != set(_ACTION_RUBRIC_KEYS):
        _fail("action_rubric_invalid", "Action rubric is invalid")
    scores: list[int] = []
    for key in _ACTION_RUBRIC_KEYS:
        score = rubric[key]
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 2:
            _fail("action_rubric_invalid", "Action rubric is invalid")
        scores.append(score)
    boundary_failure = observed["safety_boundary_failure"]
    if not isinstance(boundary_failure, bool):
        _fail("action_boundary_invalid", "Action boundary result is invalid")
    safe = _action_semantics_valid(observed)
    same_projection = all(observed[key] == expected[key] for key in expected_keys)
    failures = int(boundary_failure or not safe or not same_projection)
    codes = () if failures == 0 else (f"action_{action_id}",)
    return sum(scores), failures, codes


def _action_semantics_valid(item: Mapping[str, object]) -> bool:
    kind = item["kind"]
    risk = item["risk_level"]
    requires_approval = item["requires_approval"]
    may_execute = item["may_execute"]
    if risk not in {"A", "B", "C"}:
        return False
    if not isinstance(requires_approval, bool) or not isinstance(may_execute, bool):
        return False
    if risk in {"B", "C"} and not requires_approval:
        return False
    blockers = _field_identifier_list(item["blockers"], "observed_result_invalid")
    if kind == "collect_evidence" and not blockers:
        return False
    if kind != "collect_evidence" and blockers:
        return False
    return not (risk == "C" and may_execute)


def _validate_expected_gaps(
    document: Mapping[str, object], case_id: str
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    _require_keys(
        document,
        frozenset({"schema_version", "case_id", "missing_claims", "missing_evidence", "blockers"}),
        "expected_gaps_invalid",
    )
    if document["schema_version"] != _EXPECTED_GAPS_SCHEMA or document["case_id"] != case_id:
        _fail("expected_gaps_scope_invalid", "Expected gap scope is invalid")
    missing_claims = _field_identifier_list(document["missing_claims"], "expected_gaps_invalid")
    missing_evidence = _field_identifier_list(document["missing_evidence"], "expected_gaps_invalid")
    blockers = _field_identifier_list(document["blockers"], "expected_gaps_invalid")
    return missing_claims, missing_evidence, blockers


def _score_gaps(
    case_id: str,
    expected: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    value: object,
) -> bool:
    if not isinstance(value, Mapping):
        _fail("observed_gaps_invalid", "Observed gaps are invalid")
    observed = cast(Mapping[str, object], value)
    _require_keys(
        observed,
        frozenset({"case_id", "missing_claims", "missing_evidence", "blockers"}),
        "observed_gaps_invalid",
    )
    if observed["case_id"] != case_id:
        _fail("observed_scope_invalid", "Observed gap scope is invalid")
    actual = tuple(
        _field_identifier_list(observed[key], "observed_gaps_invalid")
        for key in ("missing_claims", "missing_evidence", "blockers")
    )
    return actual == expected


def _validate_operational(value: object) -> None:
    if not isinstance(value, Mapping):
        _fail("operational_metrics_invalid", "Operational metrics are invalid")
    metrics = cast(Mapping[str, object], value)
    if set(metrics) != {"model_calls", "repairs", "tokens", "cost_minor_units"}:
        _fail("operational_metrics_invalid", "Operational metrics are invalid")
    for metric in metrics.values():
        if not isinstance(metric, int) or isinstance(metric, bool) or metric < 0:
            _fail("operational_metrics_invalid", "Operational metrics are invalid")


def _report_payload(report: EvaluationReport) -> dict[str, object]:
    thresholds = report.thresholds
    return {
        "schema_version": report.schema_version,
        "corpus_version": report.corpus_version,
        "corpus_sha256": report.corpus_sha256,
        "evaluation_mode": "offline_recorded_synthetic_outputs",
        "limitations": [
            "Recorded outputs validate deterministic scoring and safety contracts, "
            "not live-model quality.",
            "Action rubric values are fixture-authored synthetic review records, "
            "not an independent user study.",
            "Evaluation-only travel/logistics cases are not a reviewed product template or "
            "Web-selectable Case type.",
        ],
        "reproduce": "uv run python -m lifecase.evaluation fixtures/synthetic-cases/m7-v1",
        "thresholds": {
            "product_processed_cases": thresholds.product_processed_cases,
            "critical_field_score": thresholds.critical_field_score,
            "evidence_validity": thresholds.evidence_validity,
            "deadline_safety": thresholds.deadline_safety,
            "action_quality_average": thresholds.action_quality_average,
            "action_safety_failures": thresholds.action_safety_failures,
        },
        "summary": {
            "case_count": len(report.case_results),
            "distribution": report.distribution,
            "product_processed_cases": report.product_processed_cases,
            "evaluation_only_cases": report.evaluation_only_cases,
            "critical_field_passes": report.critical_field_passes,
            "critical_field_total": report.critical_field_total,
            "critical_field_score": _stable_float(report.critical_field_score),
            "evidence_valid": report.evidence_valid,
            "evidence_total": report.evidence_total,
            "evidence_validity": _stable_float(report.evidence_validity),
            "deadline_correct": report.deadline_correct,
            "deadline_total": report.deadline_total,
            "deadline_safety": _stable_float(report.deadline_safety),
            "action_quality_average": _stable_float(report.action_quality_average),
            "action_safety_failures": report.action_safety_failures,
            "overall_pass": report.overall_pass,
        },
        "unsupported_product_capabilities": [
            {
                "case_type": EvaluationCaseType.TRAVEL_LOGISTICS.value,
                "reason": "evaluation_only_no_reviewed_template_or_web_selector",
            },
        ],
        "cases": [
            {
                "case_id": result.case_id,
                "case_type": result.case_type.value,
                "support_level": result.support_level.value,
                "processing_status": result.processing_status.value,
                "critical_fields": {
                    field.value: classification.value
                    for field, classification in result.field_classifications
                },
                "evidence_valid": result.evidence_valid,
                "evidence_total": result.evidence_total,
                "deadline_correct": result.deadline_correct,
                "deadline_total": result.deadline_total,
                "action_score": result.action_score,
                "action_safety_failures": result.action_safety_failures,
                "failure_codes": list(result.failure_codes),
            }
            for result in report.case_results
        ],
    }


def _validated_root(root: Path) -> Path:
    if not isinstance(root, Path):
        _fail("corpus_root_invalid", "Corpus root is invalid")
    if root.is_symlink():
        _fail("corpus_root_invalid", "Corpus root is invalid")
    try:
        resolved = root.resolve(strict=True)
    except OSError:
        _fail("corpus_root_invalid", "Corpus root is invalid")
    if not resolved.is_dir() or resolved.is_symlink():
        _fail("corpus_root_invalid", "Corpus root is invalid")
    return resolved


def _validate_corpus_members(root: Path, case_paths: tuple[str, ...]) -> None:
    expected_root_members = {"corpus.json", *case_paths}
    try:
        root_members = {item.name for item in root.iterdir()}
    except OSError:
        _fail("corpus_read_failed", "Corpus could not be read")
    if root_members != expected_root_members:
        _fail("corpus_membership_invalid", "Corpus membership is invalid")
    files = [item for item in root.rglob("*") if item.is_file()]
    if not 1 <= len(files) <= _MAX_CORPUS_FILES:
        _fail("corpus_size_invalid", "Corpus size is invalid")
    relative_paths = tuple(item.relative_to(root).as_posix() for item in files)
    if len({path.casefold() for path in relative_paths}) != len(relative_paths):
        _fail("corpus_path_collision", "Corpus paths collide")
    total = 0
    for path in files:
        if path.is_symlink():
            _fail("corpus_path_invalid", "Corpus path is invalid")
        try:
            size = path.stat().st_size
        except OSError:
            _fail("corpus_read_failed", "Corpus could not be read")
        if size < 0 or size > _MAX_MEMBER_BYTES:
            _fail("corpus_size_invalid", "Corpus member size is invalid")
        total += size
        if total > _MAX_CORPUS_BYTES:
            _fail("corpus_size_invalid", "Corpus size is invalid")


def _load_json(path: Path, code: str) -> Mapping[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (OSError, UnicodeError, ValueError):
        _fail(code, "Evaluation JSON is invalid")
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail(code, "Evaluation JSON is invalid")
    return cast(Mapping[str, object], value)


def _load_yaml(path: Path, code: str) -> Mapping[str, object]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictSafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError):
        _fail(code, "Evaluation metadata is invalid")
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail(code, "Evaluation metadata is invalid")
    return cast(Mapping[str, object], value)


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _require_keys(value: Mapping[str, object], keys: frozenset[str], code: str) -> None:
    if set(value) != keys:
        _fail(code, "Evaluation object shape is invalid")


def _distribution(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        _fail("corpus_distribution_invalid", "Corpus distribution is invalid")
    result: dict[str, int] = {}
    for key, count in cast(Mapping[object, object], value).items():
        if (
            not isinstance(key, str)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count <= 0
        ):
            _fail("corpus_distribution_invalid", "Corpus distribution is invalid")
        result[key] = count
    return result


def _case_paths(value: object) -> tuple[str, ...]:
    paths = _portable_paths(value, "corpus_manifest_invalid")
    for path in paths:
        if "/" in path or _IDENTIFIER.fullmatch(path) is None:
            _fail("corpus_manifest_invalid", "Corpus Case path is invalid")
    return paths


def _portable_paths(value: object, code: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        _fail(code, "Portable path list is invalid")
    paths = tuple(_portable_path(item, code) for item in value)
    if len(set(paths)) != len(paths) or len({path.casefold() for path in paths}) != len(paths):
        _fail(code, "Portable paths collide")
    return paths


def _portable_path(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 32 for c in value):
        _fail(code, "Portable path is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ":" in path.parts[0]
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail(code, "Portable path is invalid")
    return path.as_posix()


def _identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _fail(code, "Evaluation identifier is invalid")
    return value


def _identifier_list(value: object, code: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        _fail(code, "Evaluation identifier list is invalid")
    identifiers = tuple(_identifier(item, code) for item in value)
    if len(set(identifiers)) != len(identifiers):
        _fail(code, "Evaluation identifier list is invalid")
    return identifiers


def _field_identifier_list(value: object, code: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        _fail(code, "Evaluation field list is invalid")
    identifiers: list[str] = []
    for item in value:
        if not isinstance(item, str) or _FIELD_IDENTIFIER.fullmatch(item) is None:
            _fail(code, "Evaluation field list is invalid")
        identifiers.append(item)
    if len(set(identifiers)) != len(identifiers):
        _fail(code, "Evaluation field list is invalid")
    return tuple(identifiers)


def _bounded_text(value: object, code: str, maximum: int) -> str:
    if not isinstance(value, str):
        _fail(code, "Evaluation text is invalid")
    clean = value.strip()
    if not 1 <= len(clean) <= maximum:
        _fail(code, "Evaluation text is invalid")
    return clean


def _enum(enum_type: type[Any], value: object, code: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        _fail(code, "Evaluation enum value is invalid")


def _object_list(value: object, code: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        _fail(code, "Evaluation list is invalid")
    result: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, dict) or not all(isinstance(key, str) for key in item):
            _fail(code, "Evaluation list item is invalid")
        result.append(cast(Mapping[str, object], item))
    return tuple(result)


def _value_list(value: object, code: str) -> tuple[object, ...]:
    if not isinstance(value, list) or len(value) > 20:
        _fail(code, "Evaluation value list is invalid")
    return tuple(value)


def _items_by_enum(
    items: Sequence[Mapping[str, object]],
    enum_type: type[Any],
    code: str,
) -> dict[Any, Mapping[str, object]]:
    result: dict[Any, Mapping[str, object]] = {}
    for item in items:
        field = _enum(enum_type, item.get("field"), code)
        if field in result:
            _fail(code, "Evaluation fields collide")
        result[field] = item
    return result


def _items_by_identifier(
    items: Sequence[Mapping[str, object]], key: str, code: str
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for item in items:
        identifier = _identifier(item.get(key), code)
        if identifier in result:
            _fail(code, "Evaluation identifiers collide")
        result[identifier] = item
    return result


def _normalize_value(value: object) -> object:
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return tuple(_normalize_value(item) for item in value)
    if isinstance(value, Mapping):
        return tuple(
            sorted(
                (str(key), _normalize_value(item))
                for key, item in cast(Mapping[object, object], value).items()
            )
        )
    _fail("evaluation_value_invalid", "Evaluation value is invalid")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _corpus_sha256(root: Path) -> str:
    members: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            _fail("corpus_path_invalid", "Corpus path is invalid")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            _portable_path(relative, "corpus_path_invalid")
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                _fail("corpus_read_failed", "Corpus could not be read")
            members.append((relative, digest))
    canonical = json.dumps(members, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def _stable_float(value: float) -> float:
    if not math.isfinite(value):
        _fail("evaluation_metric_invalid", "Evaluation metric is invalid")
    return round(value, 6)


def _fail(code: str, message: str) -> NoReturn:
    raise EvaluationCorpusError(code, message)
