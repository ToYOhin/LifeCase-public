"""Repeatable synthetic DeepSeek demo seed and acceptance coverage."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from lifecase.demo_readiness import (
    DEMO_CASE_SPECS,
    DemoReadinessReport,
    accept_seeded_cases,
    render_demo_report,
    seed_demo_cases,
)


class _MemoryDemoApi:
    def __init__(
        self,
        *,
        unsafe_actions: bool = False,
        stage_names: tuple[str, ...] = ("extract", "validate"),
        stage_order: tuple[str, ...] = ("extract", "validate"),
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self.goals: list[str] = []
        self.uploads: list[tuple[str, str, bytes]] = []
        self._unsafe_actions = unsafe_actions
        self._stage_names = stage_names
        self._stage_order = stage_order

    def create_case(self, *, title: str, goal: str, template_id: str) -> Mapping[str, object]:
        case_id = "case-answered" if "Answered" in title else "case-unknown"
        self.calls.append(("create", case_id))
        self.goals.append(goal)
        assert template_id == "warranty"
        assert goal
        return {"id": case_id, "type": "warranty"}

    def upload_source(self, case_id: str, *, filename: str, content: bytes) -> None:
        self.calls.append(("upload", case_id))
        self.uploads.append((case_id, filename, content))

    def preview_live_agent(self, case_id: str) -> Mapping[str, object]:
        self.calls.append(("preview", case_id))
        return {
            "capability": {
                "status": "ready",
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
            },
            "preview": {
                "case_type": "warranty",
                "preview_sha256": "a" * 64,
                "evidence": {"segment_count": 1, "utf8_bytes": 171},
                "budget": {"max_calls": 14, "max_input_tokens": 28000, "max_output_tokens": 28000},
            },
        }

    def authorize_live_agent(self, case_id: str, *, preview_sha256: str) -> Mapping[str, object]:
        self.calls.append(("authorize", case_id))
        assert preview_sha256 == "a" * 64
        return {"run": {"status": "queued"}}

    def execute_live_agent(self, case_id: str) -> Mapping[str, object]:
        self.calls.append(("execute", case_id))
        answer = "answered" if case_id == "case-answered" else "unknown"
        citations = [{"segment_id": "segment-synthetic"}] if answer == "answered" else []
        return {
            "run": {
                "status": "completed",
                "stage_order": list(self._stage_order),
                "stages": [
                    {"name": name, "status": "checkpointed", "error_code": None}
                    for name in self._stage_names
                ],
                "claims": [
                    {"field": "product_name", "status": "candidate"},
                    {"field": "serial_number", "status": "inferred"},
                ],
                "answer": {"status": answer, "citations": citations},
                "usage": {
                    "calls": 2,
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "total_tokens": 300,
                },
            }
        }

    def list_actions(self, case_id: str) -> list[Mapping[str, object]]:
        self.calls.append(("actions", case_id))
        return [{"id": "forbidden"}] if self._unsafe_actions else []

    def list_deadlines(self, case_id: str) -> list[Mapping[str, object]]:
        self.calls.append(("deadlines", case_id))
        return []


def test_seed_creates_two_fixed_reviewed_warranty_cases_without_real_data() -> None:
    api = _MemoryDemoApi()

    seeded = seed_demo_cases(api, port=8765)

    assert [item.slug for item in seeded] == ["answered", "unknown"]
    assert [item.expected_answer for item in seeded] == ["answered", "unknown"]
    assert api.goals == ["warranty marker alpha-7391", "zeta-no-match-0000"]
    assert [item.case_url for item in seeded] == [
        "http://127.0.0.1:8765/#case=case-answered",
        "http://127.0.0.1:8765/#case=case-unknown",
    ]
    assert len(api.uploads) == 2
    assert {filename for _, filename, _ in api.uploads} == {"synthetic-warranty.txt"}
    assert all(
        content == DEMO_CASE_SPECS[0].evidence.encode("utf-8") for _, _, content in api.uploads
    )
    assert all(b"@" not in content for _, _, content in api.uploads)


def test_acceptance_runs_answered_then_unknown_and_keeps_only_safe_aggregates() -> None:
    api = _MemoryDemoApi()
    seeded = seed_demo_cases(api, port=8765)

    report = accept_seeded_cases(api, seeded)
    rendered = render_demo_report(report)

    assert report.overall_pass is True
    assert report.schema_version == "p1-06-demo-readiness.v2"
    assert [item.actual_answer for item in report.cases] == ["answered", "unknown"]
    assert all(item.stages_completed == 2 for item in report.cases)
    assert all(item.claim_count == 2 for item in report.cases)
    assert [item.citation_count for item in report.cases] == [1, 0]
    assert all(item.calls == 2 for item in report.cases)
    assert all(item.safety_error_code is None for item in report.cases)
    assert "Merchant Alpha Store" not in rendered
    assert "alpha-7391" not in rendered
    assert "raw" not in rendered.lower()
    assert api.calls.index(("execute", "case-answered")) < api.calls.index(
        ("execute", "case-unknown")
    )


@pytest.mark.parametrize(
    ("stage_names", "stage_order"),
    [
        (("validate", "extract"), ("extract", "validate")),
        (("extract",), ("extract", "validate")),
        (("extract", "extract"), ("extract", "validate")),
        (("extract", "validate"), ("validate", "extract")),
        (
            (
                "extract",
                "normalize",
                "link_claims",
                "classify_case",
                "check_gaps",
                "propose_actions",
                "validate",
            ),
            ("extract", "validate"),
        ),
    ],
)
def test_acceptance_rejects_a_missing_reordered_or_legacy_live_stage_plan(
    stage_names: tuple[str, ...], stage_order: tuple[str, ...]
) -> None:
    api = _MemoryDemoApi(stage_names=stage_names, stage_order=stage_order)
    seeded = seed_demo_cases(api, port=8765)

    report = accept_seeded_cases(api, seeded)

    assert report.overall_pass is False
    assert len(report.cases) == 1
    assert report.cases[0].safety_error_code == "demo_two_stage_failure"
    assert ("execute", "case-unknown") not in api.calls


def test_acceptance_stops_after_first_authority_failure() -> None:
    api = _MemoryDemoApi(unsafe_actions=True)
    seeded = seed_demo_cases(api, port=8765)

    report = accept_seeded_cases(api, seeded)

    assert isinstance(report, DemoReadinessReport)
    assert report.overall_pass is False
    assert len(report.cases) == 1
    assert report.cases[0].safety_error_code == "demo_external_authority_failure"
    assert ("execute", "case-unknown") not in api.calls
