"""M7-02 fixed-corpus integrity, scoring, and truthful-report coverage."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lifecase.evaluation import (
    EvaluationCorpusError,
    EvaluationService,
    render_report_json,
    render_report_markdown,
)
from lifecase.evaluation.models import (
    CriticalField,
    CriticalFieldClassification,
    EvaluationCaseType,
)
from lifecase.templates import BUILT_IN_TEMPLATE_REGISTRY

_CORPUS_ROOT = Path(__file__).resolve().parents[4] / "fixtures" / "synthetic-cases" / "m7-v1"


def test_fixed_corpus_has_exact_distribution_and_truthful_product_coverage() -> None:
    report = EvaluationService().evaluate(_CORPUS_ROOT)

    assert len(report.case_results) == 30
    assert report.distribution == {
        "return_refund": 8,
        "warranty": 8,
        "reimbursement": 7,
        "subscription_cancellation": 4,
        "travel_logistics": 3,
    }
    assert report.product_processed_cases == 27
    assert report.evaluation_only_cases == 3
    assert report.critical_field_passes == 148
    assert report.critical_field_total == 150
    assert report.critical_field_score == pytest.approx(148 / 150)
    assert report.evidence_validity == 1.0
    assert report.evidence_valid == report.evidence_total == 144
    assert report.deadline_safety == 1.0
    assert report.action_quality_average == 11.6
    assert report.action_safety_failures == 0
    assert report.overall_pass is True
    assert {
        classification
        for result in report.case_results
        for _field, classification in result.field_classifications
    } == set(CriticalFieldClassification)
    assert [result.case_id for result in report.case_results if result.failure_codes] == [
        "m7-warranty-03",
        "m7-reimbursement-06",
    ]


def test_reports_are_byte_stable_safe_and_keep_travel_evaluation_only() -> None:
    first = EvaluationService().evaluate(_CORPUS_ROOT)
    second = EvaluationService().evaluate(_CORPUS_ROOT)
    first_json = render_report_json(first)
    first_markdown = render_report_markdown(first)

    assert first == second
    assert first_json == render_report_json(second)
    assert first_markdown == render_report_markdown(second)
    assert str(_CORPUS_ROOT) not in first_json
    assert str(_CORPUS_ROOT) not in first_markdown
    assert "not live-model quality" in first_json
    assert "Product-processed coverage: 27/30" in first_markdown
    assert [item.case_type for item in BUILT_IN_TEMPLATE_REGISTRY.catalog()] == [
        "warranty",
        "return_refund",
        "reimbursement",
        "subscription_cancellation",
    ]
    assert "subscription_cancellation" in {
        item.case_type for item in BUILT_IN_TEMPLATE_REGISTRY.catalog()
    }
    assert "travel_logistics" not in {
        item.case_type for item in BUILT_IN_TEMPLATE_REGISTRY.catalog()
    }


def test_five_type_sample_keeps_scope_citation_deadline_and_action_semantics() -> None:
    report = EvaluationService().evaluate(_CORPUS_ROOT)
    samples = {
        case_type: next(item for item in report.case_results if item.case_type is case_type)
        for case_type in EvaluationCaseType
    }

    assert set(samples) == set(EvaluationCaseType)
    for result in samples.values():
        assert result.evidence_valid == result.evidence_total == 5
        assert result.deadline_correct == result.deadline_total == 1
        assert result.action_score == 12
        assert result.action_safety_failures == 0
        assert dict(result.field_classifications) == {
            field: CriticalFieldClassification.EXACT for field in CriticalField
        }


def test_scoring_distinguishes_acceptable_uncertain_and_unsupported(tmp_path: Path) -> None:
    acceptable_root = _copy_corpus(tmp_path / "acceptable")
    acceptable_case = acceptable_root / "m7-return-refund-01"
    expected = _read_json(acceptable_case / "expected" / "claims.json")
    merchant = expected["critical_fields"][0]
    merchant["expected_value"] = "Different expected merchant"
    merchant["acceptable_values"] = ["Synthetic Return Organization 01"]
    _write_json(acceptable_case / "expected" / "claims.json", expected)
    acceptable_report = EvaluationService().evaluate(acceptable_root)
    acceptable_fields = dict(acceptable_report.case_results[0].field_classifications)
    assert acceptable_fields[CriticalField.MERCHANT] is CriticalFieldClassification.ACCEPTABLE

    uncertain_root = _copy_corpus(tmp_path / "uncertain")
    uncertain_case = uncertain_root / "m7-return-refund-01"
    expected = _read_json(uncertain_case / "expected" / "claims.json")
    expected["critical_fields"][0]["allow_unknown"] = True
    _write_json(uncertain_case / "expected" / "claims.json", expected)
    observed = _read_json(uncertain_case / "observed.json")
    observed["critical_fields"][0].update({"status": "uncertain", "value": None, "citations": []})
    _write_json(uncertain_case / "observed.json", observed)
    uncertain_report = EvaluationService().evaluate(uncertain_root)
    assert dict(uncertain_report.case_results[0].field_classifications)[CriticalField.MERCHANT] is (
        CriticalFieldClassification.APPROPRIATELY_UNCERTAIN
    )

    unsupported_root = _copy_corpus(tmp_path / "unsupported")
    unsupported_case = unsupported_root / "m7-return-refund-01"
    observed = _read_json(unsupported_case / "observed.json")
    observed["critical_fields"][0].update({"status": "unsupported", "value": None, "citations": []})
    _write_json(unsupported_case / "observed.json", observed)
    unsupported_report = EvaluationService().evaluate(unsupported_root)
    result = unsupported_report.case_results[0]
    assert dict(result.field_classifications)[CriticalField.MERCHANT] is (
        CriticalFieldClassification.UNSUPPORTED_OR_WRONG
    )
    assert "critical_field_merchant" in result.failure_codes


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("unsafe_path", "observed_result_invalid"),
        ("unknown_schema", "case_schema_unsupported"),
        ("extra_member", "corpus_case_membership_invalid"),
        ("extra_root_member", "corpus_membership_invalid"),
        ("oversized_member", "corpus_size_invalid"),
        ("duplicate_json_key", "expected_claims_invalid"),
        ("duplicate_yaml_key", "case_metadata_invalid"),
    ],
)
def test_invalid_paths_schema_and_membership_fail_with_safe_errors(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    root = _copy_corpus(tmp_path / mutation)
    case_root = root / "m7-return-refund-01"
    if mutation == "unsafe_path":
        observed = _read_json(case_root / "observed.json")
        observed["critical_fields"][0]["citations"][0]["input_path"] = "../evidence.txt"
        _write_json(case_root / "observed.json", observed)
    elif mutation == "unknown_schema":
        metadata = (case_root / "metadata.yaml").read_text(encoding="utf-8")
        (case_root / "metadata.yaml").write_text(
            metadata.replace("lifecase.m7.synthetic_case.v1", "future.v99"),
            encoding="utf-8",
        )
    elif mutation == "extra_member":
        (case_root / "unexpected.txt").write_text("synthetic", encoding="utf-8")
    elif mutation == "extra_root_member":
        (root / "unexpected.txt").write_text("synthetic", encoding="utf-8")
    elif mutation == "oversized_member":
        (case_root / "input" / "evidence.txt").write_bytes(b"x" * 1_000_001)
    elif mutation == "duplicate_json_key":
        claims = case_root / "expected" / "claims.json"
        content = claims.read_text(encoding="utf-8")
        claims.write_text(
            content.replace(
                '  "case_id":',
                '  "schema_version": "duplicate",\n  "case_id":',
                1,
            ),
            encoding="utf-8",
        )
    else:
        metadata = case_root / "metadata.yaml"
        metadata.write_text(
            metadata.read_text(encoding="utf-8") + "case_id: duplicate-case\n",
            encoding="utf-8",
        )

    with pytest.raises(EvaluationCorpusError) as captured:
        EvaluationService().evaluate(root)

    assert captured.value.code == expected_code
    safe = f"{captured.value.code} {captured.value}"
    assert str(tmp_path) not in safe
    assert "Synthetic Return Organization" not in safe
    assert "SELECT" not in safe
    assert "credential" not in safe.lower()


def test_cross_case_stale_hash_deadline_rule_and_action_boundary_are_scored_closed(
    tmp_path: Path,
) -> None:
    root = _copy_corpus(tmp_path / "unsafe-observations")
    case_root = root / "m7-return-refund-01"
    observed = _read_json(case_root / "observed.json")
    observed["critical_fields"][0]["citations"][0]["case_id"] = "m7-warranty-01"
    observed["critical_fields"][1]["citations"][0]["segment_sha256"] = "0" * 64
    observed["critical_fields"][2]["value"] = "2099-01-01"
    observed["critical_fields"][3]["citations"][0]["relation"] = "context"
    observed["deadlines"][0].update(
        {
            "kind": "calculated",
            "external_obligation": False,
            "rule": {
                "rule_id": "unknown_rule",
                "rule_version": 99,
                "parameters": {"days": 7},
            },
        }
    )
    observed["actions"][0].update(
        {"risk_level": "C", "requires_approval": True, "may_execute": True}
    )
    _write_json(case_root / "observed.json", observed)

    result = EvaluationService().evaluate(root).case_results[0]

    assert result.evidence_valid == 1
    assert result.evidence_total == 5
    assert result.deadline_correct == 0
    assert result.action_safety_failures == 1
    assert "evidence_merchant" in result.failure_codes
    assert "evidence_amount" in result.failure_codes
    assert "evidence_purchase_or_event_date" in result.failure_codes
    assert "evidence_order_or_reference" in result.failure_codes
    assert any(code.startswith("deadline_") for code in result.failure_codes)
    assert any(code.startswith("action_") for code in result.failure_codes)


def test_zero_evidence_denominator_cannot_pass(tmp_path: Path) -> None:
    root = _copy_corpus(tmp_path / "zero-denominator")
    manifest = _read_json(root / "corpus.json")
    for case_id in manifest["cases"]:
        case_root = root / case_id
        expected = _read_json(case_root / "expected" / "claims.json")
        observed = _read_json(case_root / "observed.json")
        for item in expected["critical_fields"]:
            item["allow_unknown"] = True
        for item in observed["critical_fields"]:
            item.update({"status": "uncertain", "value": None, "citations": []})
        _write_json(case_root / "expected" / "claims.json", expected)
        _write_json(case_root / "observed.json", observed)

    with pytest.raises(EvaluationCorpusError) as captured:
        EvaluationService().evaluate(root)

    assert captured.value.code == "evaluation_denominator_invalid"


def _copy_corpus(destination: Path) -> Path:
    shutil.copytree(_CORPUS_ROOT, destination)
    return destination


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
