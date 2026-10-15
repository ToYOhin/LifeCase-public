"""Marker-bound filesystem helpers for the synthetic DeepSeek demo runtime."""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Protocol

DEMO_MARKER_FILENAME = ".lifecase-deepseek-demo.json"
_DEMO_MARKER = {
    "schema": "lifecase.deepseek-demo.v1",
    "purpose": "synthetic-read-only-demo",
}
_MAX_API_RESPONSE_BYTES = 2_000_000
_MULTIPART_BOUNDARY = "----LifeCaseSyntheticDemoBoundary"
_LIVE_STAGE_ORDER = ("extract", "validate")


@dataclass(frozen=True, slots=True)
class DemoCaseSpec:
    slug: str
    title: str
    goal: str
    expected_answer: str
    evidence: str


_SYNTHETIC_WARRANTY_EVIDENCE = (
    "Merchant Alpha Store. Product SynthPhone. Purchase date 2026-07-14. "
    "Order ORD-001. Paid CNY 259.00. Serial prefix SYN. "
    "Warranty marker alpha-7391 lasts twenty-four months."
)
DEMO_CASE_SPECS = (
    DemoCaseSpec(
        slug="answered",
        title="DeepSeek Demo · Answered Warranty",
        goal="warranty marker alpha-7391",
        expected_answer="answered",
        evidence=_SYNTHETIC_WARRANTY_EVIDENCE,
    ),
    DemoCaseSpec(
        slug="unknown",
        title="DeepSeek Demo · Unknown Warranty",
        goal="zeta-no-match-0000",
        expected_answer="unknown",
        evidence=_SYNTHETIC_WARRANTY_EVIDENCE,
    ),
)


class DemoApi(Protocol):
    def create_case(self, *, title: str, goal: str, template_id: str) -> Mapping[str, object]: ...

    def upload_source(self, case_id: str, *, filename: str, content: bytes) -> None: ...

    def preview_live_agent(self, case_id: str) -> Mapping[str, object]: ...

    def authorize_live_agent(
        self, case_id: str, *, preview_sha256: str
    ) -> Mapping[str, object]: ...

    def execute_live_agent(self, case_id: str) -> Mapping[str, object]: ...

    def list_actions(self, case_id: str) -> list[Mapping[str, object]]: ...

    def list_deadlines(self, case_id: str) -> list[Mapping[str, object]]: ...


@dataclass(frozen=True, slots=True)
class SeededDemoCase:
    slug: str
    case_id: str
    case_url: str
    expected_answer: str


@dataclass(frozen=True, slots=True)
class DemoCaseAcceptance:
    slug: str
    case_type: str
    case_url: str
    expected_answer: str
    actual_answer: str | None
    stages_completed: int
    claim_count: int
    citation_count: int
    calls: int
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    safety_error_code: str | None


@dataclass(frozen=True, slots=True)
class DemoReadinessReport:
    schema_version: str
    provider: str
    model_id: str
    cases: tuple[DemoCaseAcceptance, ...]

    @property
    def overall_pass(self) -> bool:
        return (
            len(self.cases) == len(DEMO_CASE_SPECS)
            and [item.actual_answer for item in self.cases] == ["answered", "unknown"]
            and all(item.stages_completed == len(_LIVE_STAGE_ORDER) for item in self.cases)
            and all(item.safety_error_code is None for item in self.cases)
        )


class DemoApiError(RuntimeError):
    """A content-free failure at the loopback demo API boundary."""


class DemoRuntimeError(RuntimeError):
    """Raised when a path is not safe to use as a disposable demo runtime."""


class HttpDemoApi:
    """Small fixed-loopback client used only by the local demo command."""

    def __init__(self, *, port: int, timeout_seconds: float = 900.0) -> None:
        if not 1_024 <= port <= 65_535:
            raise ValueError("Demo API port is invalid")
        self._base_url = f"http://127.0.0.1:{port}/api/v1"
        self._timeout_seconds = timeout_seconds

    def create_case(self, *, title: str, goal: str, template_id: str) -> Mapping[str, object]:
        return self._json_request(
            "/cases",
            method="POST",
            body=json.dumps(
                {"title": title, "goal": goal, "template_id": template_id},
                ensure_ascii=False,
            ).encode("utf-8"),
            content_type="application/json",
        )

    def upload_source(self, case_id: str, *, filename: str, content: bytes) -> None:
        disposition = (
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        ).encode("ascii")
        body = b"".join(
            (
                f"--{_MULTIPART_BOUNDARY}\r\n".encode(),
                disposition,
                b"Content-Type: text/plain\r\n\r\n",
                content,
                f"\r\n--{_MULTIPART_BOUNDARY}--\r\n".encode(),
            )
        )
        self._json_request(
            f"/cases/{_url_part(case_id)}/sources",
            method="POST",
            body=body,
            content_type=f"multipart/form-data; boundary={_MULTIPART_BOUNDARY}",
        )

    def preview_live_agent(self, case_id: str) -> Mapping[str, object]:
        return self._json_request(f"/cases/{_url_part(case_id)}/live-agent")

    def authorize_live_agent(self, case_id: str, *, preview_sha256: str) -> Mapping[str, object]:
        return self._json_request(
            f"/cases/{_url_part(case_id)}/live-agent",
            method="POST",
            body=json.dumps({"preview_sha256": preview_sha256, "confirm_read_only": True}).encode(
                "ascii"
            ),
            content_type="application/json",
        )

    def execute_live_agent(self, case_id: str) -> Mapping[str, object]:
        return self._json_request(
            f"/cases/{_url_part(case_id)}/live-agent/execute",
            method="POST",
            body=b"",
        )

    def list_actions(self, case_id: str) -> list[Mapping[str, object]]:
        return self._list_request(f"/cases/{_url_part(case_id)}/actions")

    def list_deadlines(self, case_id: str) -> list[Mapping[str, object]]:
        return self._list_request(f"/cases/{_url_part(case_id)}/deadlines")

    def _json_request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> Mapping[str, object]:
        headers = {} if content_type is None else {"Content-Type": content_type}
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                encoded = response.read(_MAX_API_RESPONSE_BYTES + 1)
            if len(encoded) > _MAX_API_RESPONSE_BYTES:
                raise DemoApiError("demo_api_response_too_large")
            payload = json.loads(encoded)
        except DemoApiError:
            raise
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            raise DemoApiError("demo_api_request_failed") from error
        return _mapping(payload)

    def _list_request(self, path: str) -> list[Mapping[str, object]]:
        request = urllib.request.Request(f"{self._base_url}{path}", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                encoded = response.read(_MAX_API_RESPONSE_BYTES + 1)
            if len(encoded) > _MAX_API_RESPONSE_BYTES:
                raise DemoApiError("demo_api_response_too_large")
            payload = json.loads(encoded)
            if not isinstance(payload, list):
                raise DemoApiError("demo_api_contract_invalid")
            return [_mapping(item) for item in payload]
        except DemoApiError:
            raise
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            raise DemoApiError("demo_api_request_failed") from error


def seed_demo_cases(api: DemoApi, *, port: int) -> tuple[SeededDemoCase, ...]:
    """Create two fresh synthetic Cases through the existing product API."""

    seeded: list[SeededDemoCase] = []
    for spec in DEMO_CASE_SPECS:
        case = api.create_case(title=spec.title, goal=spec.goal, template_id="warranty")
        case_id = _text(case.get("id"))
        if case.get("type") != "warranty":
            raise DemoApiError("demo_case_contract_invalid")
        api.upload_source(
            case_id,
            filename="synthetic-warranty.txt",
            content=spec.evidence.encode("utf-8"),
        )
        seeded.append(
            SeededDemoCase(
                slug=spec.slug,
                case_id=case_id,
                case_url=f"http://127.0.0.1:{port}/#case={urllib.parse.quote(case_id, safe='')}",
                expected_answer=spec.expected_answer,
            )
        )
    return tuple(seeded)


def accept_seeded_cases(
    api: DemoApi,
    cases: Sequence[SeededDemoCase],
) -> DemoReadinessReport:
    """Run answered then unknown, stopping on the first safety or contract failure."""

    outcomes: list[DemoCaseAcceptance] = []
    provider = "deepseek"
    model_id = "deepseek-v4-flash"
    for seeded in cases:
        try:
            preview = api.preview_live_agent(seeded.case_id)
            capability = _mapping(preview.get("capability"))
            preview_body = _mapping(preview.get("preview"))
            provider = _text(capability.get("provider"))
            model_id = _text(capability.get("model_id"))
            if capability.get("status") != "ready":
                raise DemoApiError("demo_live_agent_unavailable")
            api.authorize_live_agent(
                seeded.case_id,
                preview_sha256=_text(preview_body.get("preview_sha256")),
            )
            started = perf_counter()
            completed = api.execute_live_agent(seeded.case_id)
            elapsed_seconds = perf_counter() - started
            actions = api.list_actions(seeded.case_id)
            deadlines = api.list_deadlines(seeded.case_id)
            outcome = _acceptance_from_result(
                seeded,
                completed,
                elapsed_seconds=elapsed_seconds,
                actions=actions,
                deadlines=deadlines,
            )
        except (DemoApiError, TypeError, ValueError):
            outcome = DemoCaseAcceptance(
                slug=seeded.slug,
                case_type="warranty",
                case_url=seeded.case_url,
                expected_answer=seeded.expected_answer,
                actual_answer=None,
                stages_completed=0,
                claim_count=0,
                citation_count=0,
                calls=0,
                input_tokens=0,
                output_tokens=0,
                elapsed_seconds=0.0,
                safety_error_code="demo_api_or_contract_failure",
            )
        outcomes.append(outcome)
        if outcome.safety_error_code is not None:
            break
    return DemoReadinessReport(
        schema_version="p1-06-demo-readiness.v2",
        provider=provider,
        model_id=model_id,
        cases=tuple(outcomes),
    )


def render_demo_report(report: DemoReadinessReport) -> str:
    payload = {
        "schema_version": report.schema_version,
        "provider": report.provider,
        "model_id": report.model_id,
        "overall_pass": report.overall_pass,
        "cases": [asdict(item) for item in report.cases],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"


def prepare_demo_root(runtime_root: Path) -> Path:
    """Create or verify one explicitly marked, non-root demo directory."""

    root = _safe_absolute_root(runtime_root)
    marker = root / DEMO_MARKER_FILENAME
    if root.exists():
        if not root.is_dir():
            raise DemoRuntimeError("Demo runtime path is not a directory")
        if marker.exists():
            _verify_marker(marker)
            return root
        if any(root.iterdir()):
            raise DemoRuntimeError("Path is not an initialized LifeCase DeepSeek demo runtime")
    else:
        root.mkdir(parents=True)
    marker.write_text(
        json.dumps(_DEMO_MARKER, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def reset_demo_root(runtime_root: Path) -> Path:
    """Delete exactly one verified demo root and nothing outside it."""

    root = _safe_absolute_root(runtime_root)
    marker = root / DEMO_MARKER_FILENAME
    if not root.is_dir() or not marker.is_file():
        raise DemoRuntimeError("Path is not an initialized LifeCase DeepSeek demo runtime")
    _verify_marker(marker)
    shutil.rmtree(root)
    return root


def _safe_absolute_root(runtime_root: Path) -> Path:
    try:
        root = runtime_root.expanduser().resolve(strict=False)
    except OSError as error:
        raise DemoRuntimeError("Demo runtime path is invalid") from error
    drive_root = Path(root.anchor).resolve(strict=False)
    if root == drive_root or root == Path.home().resolve(strict=False):
        raise DemoRuntimeError("Demo runtime path is unsafe")
    return root


def _verify_marker(marker: Path) -> None:
    try:
        observed = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DemoRuntimeError("Demo runtime marker is invalid") from error
    if observed != _DEMO_MARKER:
        raise DemoRuntimeError("Demo runtime marker is invalid")


def _acceptance_from_result(
    seeded: SeededDemoCase,
    result: Mapping[str, object],
    *,
    elapsed_seconds: float,
    actions: Sequence[Mapping[str, object]],
    deadlines: Sequence[Mapping[str, object]],
) -> DemoCaseAcceptance:
    run = _mapping(result.get("run"))
    stages = _mapping_list(run.get("stages"))
    claims = _mapping_list(run.get("claims"))
    answer = _mapping(run.get("answer"))
    citations = _mapping_list(answer.get("citations"))
    usage = _mapping(run.get("usage"))
    stage_errors = tuple(
        code for stage in stages if (code := _safe_error_code(stage.get("error_code"))) is not None
    )
    invalid_claim_status = any(
        claim.get("status") not in {"candidate", "inferred"} for claim in claims
    )
    stages_completed = sum(stage.get("status") == "checkpointed" for stage in stages)
    actual_answer = _optional_text(answer.get("status"))

    safety_error_code: str | None = None
    if actions or deadlines:
        safety_error_code = "demo_external_authority_failure"
    elif invalid_claim_status:
        safety_error_code = "demo_claim_authority_failure"
    elif stage_errors:
        safety_error_code = stage_errors[0]
    elif (
        run.get("status") != "completed"
        or run.get("stage_order") != list(_LIVE_STAGE_ORDER)
        or tuple(stage.get("name") for stage in stages) != _LIVE_STAGE_ORDER
        or stages_completed != len(_LIVE_STAGE_ORDER)
    ):
        safety_error_code = "demo_two_stage_failure"
    elif actual_answer != seeded.expected_answer:
        safety_error_code = "demo_answer_mismatch"
    elif (actual_answer == "answered" and not citations) or (
        actual_answer == "unknown" and citations
    ):
        safety_error_code = "demo_citation_failure"

    return DemoCaseAcceptance(
        slug=seeded.slug,
        case_type="warranty",
        case_url=seeded.case_url,
        expected_answer=seeded.expected_answer,
        actual_answer=actual_answer,
        stages_completed=stages_completed,
        claim_count=len(claims),
        citation_count=len(citations),
        calls=_integer(usage.get("calls")),
        input_tokens=_integer(usage.get("input_tokens")),
        output_tokens=_integer(usage.get("output_tokens")),
        elapsed_seconds=round(elapsed_seconds, 6),
        safety_error_code=safety_error_code,
    )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise DemoApiError("demo_api_contract_invalid")
    return value


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise DemoApiError("demo_api_contract_invalid")
    return [_mapping(item) for item in value]


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise DemoApiError("demo_api_contract_invalid")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value)


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DemoApiError("demo_api_contract_invalid")
    return value


def _safe_error_code(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 64:
        return "demo_stage_failure"
    if not all(
        character.islower() or character.isdigit() or character == "_" for character in value
    ):
        return "demo_stage_failure"
    return value


def _url_part(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def main(arguments: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="Create the two fixed synthetic warranty Cases")
    seed.add_argument("--port", type=int, default=8765)
    seed.add_argument("--execute", action="store_true")
    reset = commands.add_parser("reset", help="Delete one verified synthetic demo runtime")
    reset.add_argument("--runtime-root", type=Path, required=True)
    reset.add_argument("--confirm-reset", action="store_true")
    parsed = parser.parse_args(arguments)

    if parsed.command == "reset":
        if not parsed.confirm_reset:
            print("demo_reset_confirmation_required")
            return 2
        try:
            reset_demo_root(parsed.runtime_root)
        except DemoRuntimeError:
            print("demo_reset_refused")
            return 2
        print("demo_reset_completed")
        return 0

    try:
        api = HttpDemoApi(port=parsed.port)
        seeded = seed_demo_cases(api, port=parsed.port)
        if parsed.execute:
            report = accept_seeded_cases(api, seeded)
            print(render_demo_report(report), end="")
            return 0 if report.overall_pass else 1
        print(
            json.dumps(
                {
                    "schema_version": "p1-06-demo-seed.v1",
                    "synthetic_data": True,
                    "read_only": True,
                    "cases": [asdict(item) for item in seeded],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except (DemoApiError, ValueError):
        print("demo_seed_or_acceptance_failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
