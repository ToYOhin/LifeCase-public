"""Direct-only DeepSeek JSON Object evaluation over the warranty Agent pipeline."""

from __future__ import annotations

import asyncio
import getpass
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, runtime_checkable

from lifecase.evaluation.ollama_agent import (
    OllamaAgentEvaluationReport,
    OllamaAgentRunEvidence,
    _evaluate_one,
)
from lifecase.providers.deepseek_chat import (
    DeepSeekHttpClient,
    DeepSeekJsonProvider,
)
from lifecase.providers.models import (
    DEEPSEEK_CHAT_MODEL_ID,
)
from lifecase.providers.protocol import ModelProvider

DEEPSEEK_AGENT_EVALUATION_SCHEMA_VERSION = "p1-05d-deepseek-agent-evaluation.v1"


@runtime_checkable
class CostedModelProvider(ModelProvider, Protocol):
    @property
    def nominal_cost_usd(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class DeepSeekAgentEvaluationConfig:
    runs: int = 2
    per_run_seconds: float = 300.0
    max_cost_usd: Decimal = Decimal("0.02")

    def __post_init__(self) -> None:
        if self.runs != 2:
            raise ValueError("DeepSeek evaluation requires exactly two runs")
        if self.per_run_seconds <= 0:
            raise ValueError("DeepSeek evaluation time bound is invalid")
        if self.max_cost_usd <= 0 or self.max_cost_usd > Decimal("0.02"):
            raise ValueError("DeepSeek evaluation cost cap is invalid")


@dataclass(frozen=True, slots=True)
class DeepSeekAgentEvaluationReport:
    config: DeepSeekAgentEvaluationConfig
    runs: tuple[OllamaAgentRunEvidence, ...]
    nominal_cost_usd: Decimal
    stop_reason: str | None

    @property
    def overall_pass(self) -> bool:
        if len(self.runs) != self.config.runs or self.stop_reason is not None:
            return False
        return OllamaAgentEvaluationReport(
            model_id=DEEPSEEK_CHAT_MODEL_ID,
            per_run_seconds=self.config.per_run_seconds,
            runs=self.runs,
        ).overall_pass

    def to_safe_dict(self) -> dict[str, object]:
        base = OllamaAgentEvaluationReport(
            model_id=DEEPSEEK_CHAT_MODEL_ID,
            per_run_seconds=self.config.per_run_seconds,
            runs=self.runs,
        ).to_safe_dict()
        base.update(
            {
                "schema_version": DEEPSEEK_AGENT_EVALUATION_SCHEMA_VERSION,
                "provider": "deepseek",
                "runs_requested": self.config.runs,
                "runs_completed": len(self.runs),
                "stopped_early": len(self.runs) < self.config.runs,
                "stop_reason": self.stop_reason,
                "nominal_cost_usd": _decimal_text(self.nominal_cost_usd),
                "max_cost_usd": _decimal_text(self.config.max_cost_usd),
                "overall_pass": self.overall_pass,
            }
        )
        return base


async def evaluate_deepseek_agent(
    config: DeepSeekAgentEvaluationConfig,
    *,
    provider: CostedModelProvider,
    sensitive_markers: tuple[str, ...] = (),
) -> DeepSeekAgentEvaluationReport:
    """Run at most two temporary Cases and stop immediately on the first unsafe result."""

    outcomes: list[OllamaAgentRunEvidence] = []
    stop_reason: str | None = None
    with TemporaryDirectory(prefix="lifecase-deepseek-agent-evaluation-") as temporary:
        root = Path(temporary)
        for index in range(config.runs):
            answer_expected = "answered" if index == 0 else "unknown"
            run_root = root / f"run-{index + 1}"
            outcome = await _evaluate_one(
                run_root,
                provider,
                answer_expected=answer_expected,
            )
            outcomes.append(outcome)
            if _tree_contains_any(run_root, sensitive_markers):
                stop_reason = "sensitive_information_failure"
            elif (
                outcome.failure_code == "agent_schema_invalid"
                or outcome.schema_diagnostic_categories
                or outcome.business_failure_codes
            ):
                stop_reason = "schema_or_authority_failure"
            elif outcome.failure_code is not None:
                stop_reason = "provider_or_runtime_failure"
            elif not (
                outcome.seven_stages_completed
                and outcome.answer_correct
                and outcome.recovered_after_restart
                and outcome.claims_correct == outcome.claims_expected
                and outcome.citations_valid == outcome.citations_checked
                and outcome.unsupported_claims == 0
            ):
                stop_reason = "quality_or_authority_failure"
            elif provider.nominal_cost_usd > config.max_cost_usd:
                stop_reason = "cost_cap_exceeded"
            if stop_reason is not None:
                break
    return DeepSeekAgentEvaluationReport(
        config=config,
        runs=tuple(outcomes),
        nominal_cost_usd=provider.nominal_cost_usd,
        stop_reason=stop_reason,
    )


def render_safe_report(report: DeepSeekAgentEvaluationReport) -> str:
    return json.dumps(report.to_safe_dict(), ensure_ascii=False, sort_keys=True) + "\n"


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.00000001")), "f")


def _tree_contains_any(root: Path, markers: tuple[str, ...]) -> bool:
    encoded = tuple(marker.encode("utf-8") for marker in markers if marker)
    if not encoded:
        return False
    for path in root.rglob("*"):
        if path.is_file() and any(marker in path.read_bytes() for marker in encoded):
            return True
    return False


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run direct-only DeepSeek warranty evaluation")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--per-run-seconds", type=float, default=300.0)
    parser.add_argument("--max-cost-usd", default="0.02")
    arguments = parser.parse_args()
    try:
        max_cost = Decimal(arguments.max_cost_usd)
        config = DeepSeekAgentEvaluationConfig(
            runs=arguments.runs,
            per_run_seconds=arguments.per_run_seconds,
            max_cost_usd=max_cost,
        )
    except (InvalidOperation, ValueError):
        print("deepseek_agent_evaluation_configuration_invalid")
        return 2
    api_key = getpass.getpass("DeepSeek API key (hidden): ").strip()
    if not api_key:
        print("deepseek_agent_evaluation_credential_missing")
        return 2
    try:
        client = DeepSeekHttpClient(api_key, timeout_seconds=120.0)
        provider = DeepSeekJsonProvider(client, max_cost_usd=config.max_cost_usd)
        report = asyncio.run(
            evaluate_deepseek_agent(
                config,
                provider=provider,
                sensitive_markers=(api_key,),
            )
        )
    except Exception:
        print("deepseek_agent_evaluation_failed")
        return 2
    print(render_safe_report(report), end="")
    return 0 if report.overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
