"""Explicit, local-only Ollama evaluation over the reviewed warranty Agent pipeline."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import cast

from lifecase.agent import (
    EVIDENCE_BACKED_RAG_STAGE_REGISTRY,
    AgentRunStatus,
    AgentServiceError,
    CallReservation,
    StageBudget,
    StageRegistry,
)
from lifecase.agent.demo import PortfolioAgentDemoService
from lifecase.agent.extraction import (
    WARRANTY_EXTRACTION_OUTPUT_SCHEMA_NAME,
    diagnose_warranty_extraction_candidate,
)
from lifecase.cases import CaseService
from lifecase.claims import ClaimRecord, ClaimService, ClaimStatus
from lifecase.evaluation.schema_diagnostics import (
    SchemaDiagnosticCategory,
    classify_schema_mismatches,
)
from lifecase.evidence import SourceImporter, SourceService
from lifecase.evidence.extraction import ExtractionConfig, ExtractionService
from lifecase.providers import (
    ModelProvider,
    ModelProviderConfig,
    ModelProviderKind,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    build_model_provider,
)
from lifecase.storage import (
    ContentAddressedObjectStore,
    ObjectStoreConfig,
    apply_migrations,
    connect_database,
)

OLLAMA_AGENT_EVALUATION_SCHEMA_VERSION = "p1-03-ollama-agent-evaluation.v4"
_SYNTHETIC_EVIDENCE = (
    "Merchant Alpha Store. Product SynthPhone. Purchase date 2026-07-14. "
    "Order ORD-001. Paid CNY 259.00. Serial prefix SYN. "
    "Warranty marker alpha-7391 lasts twenty-four months."
)
_EXPECTED_CLAIMS: dict[str, tuple[object, ClaimStatus]] = {
    "merchant_or_manufacturer": ("Alpha Store", ClaimStatus.CANDIDATE),
    "product_name": ("SynthPhone", ClaimStatus.CANDIDATE),
    "purchase_date": ("2026-07-14", ClaimStatus.CANDIDATE),
    "order_number": ("ORD-001", ClaimStatus.CANDIDATE),
    "amount": ({"amount_minor": 25_900, "currency": "CNY"}, ClaimStatus.CANDIDATE),
    "serial_number": ("SYN", ClaimStatus.INFERRED),
}
_CALL_RESERVATION = CallReservation(input_tokens=4_000, output_tokens=4_000, cost_minor=0)
_STAGE_BUDGET = StageBudget(
    max_calls=2,
    max_input_tokens=8_000,
    max_output_tokens=8_000,
    max_cost_minor=0,
)
_OLLAMA_EVALUATION_REGISTRY = StageRegistry(
    tuple(
        replace(definition, timeout_seconds=120.0)
        for definition in EVIDENCE_BACKED_RAG_STAGE_REGISTRY.definitions
    )
)


class OllamaAgentEvaluationError(RuntimeError):
    """Safe failure that does not expose synthetic bodies or temporary paths."""


@dataclass(frozen=True, slots=True)
class OllamaAgentEvaluationConfig:
    model_id: str
    runs: int = 3
    per_run_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not 2 <= self.runs <= 5:
            raise ValueError("Ollama Agent evaluation runs must be between 2 and 5")
        if self.per_run_seconds <= 0:
            raise ValueError("Ollama Agent evaluation time bound must be positive")
        provider = ModelProviderConfig(
            provider=ModelProviderKind.OLLAMA,
            model_id=self.model_id,
        )
        object.__setattr__(self, "model_id", provider.model_id)


@dataclass(frozen=True, slots=True)
class OllamaAgentRunEvidence:
    answer_expected: str
    failure_code: str | None
    elapsed_seconds: float
    calls: int
    input_tokens: int
    output_tokens: int
    claims_correct: int
    claims_expected: int
    citations_valid: int
    citations_checked: int
    unsupported_claims: int
    seven_stages_completed: bool
    answer_correct: bool
    recovered_after_restart: bool
    schema_diagnostic_categories: tuple[SchemaDiagnosticCategory, ...]
    business_failure_codes: tuple[str, ...]
    failure_stage: str | None


@dataclass(frozen=True, slots=True)
class OllamaAgentEvaluationReport:
    model_id: str
    per_run_seconds: float
    runs: tuple[OllamaAgentRunEvidence, ...]

    @property
    def overall_pass(self) -> bool:
        claims_expected = sum(run.claims_expected for run in self.runs)
        claims_correct = sum(run.claims_correct for run in self.runs)
        citations_checked = sum(run.citations_checked for run in self.runs)
        citations_valid = sum(run.citations_valid for run in self.runs)
        return (
            len(self.runs) >= 2
            and all(run.seven_stages_completed for run in self.runs)
            and all(run.answer_correct for run in self.runs)
            and all(run.recovered_after_restart for run in self.runs)
            and all(run.elapsed_seconds <= self.per_run_seconds for run in self.runs)
            and sum(run.elapsed_seconds for run in self.runs)
            <= self.per_run_seconds * len(self.runs)
            and claims_expected > 0
            and claims_correct / claims_expected >= 0.90
            and citations_checked > 0
            and citations_valid == citations_checked
            and sum(run.unsupported_claims for run in self.runs) == 0
        )

    def to_safe_dict(self) -> dict[str, object]:
        elapsed = tuple(run.elapsed_seconds for run in self.runs)
        claims_expected = sum(run.claims_expected for run in self.runs)
        claims_correct = sum(run.claims_correct for run in self.runs)
        citations_checked = sum(run.citations_checked for run in self.runs)
        citations_valid = sum(run.citations_valid for run in self.runs)
        diagnostic_categories = tuple(
            category
            for category in SchemaDiagnosticCategory
            if any(category in run.schema_diagnostic_categories for run in self.runs)
        )
        business_failure_codes = tuple(
            sorted({code for run in self.runs for code in run.business_failure_codes})
        )
        return {
            "schema_version": OLLAMA_AGENT_EVALUATION_SCHEMA_VERSION,
            "provider": "ollama",
            "model_id": self.model_id,
            "runs": len(self.runs),
            "answered_runs": sum(run.answer_expected == "answered" for run in self.runs),
            "unknown_runs": sum(run.answer_expected == "unknown" for run in self.runs),
            "failed_runs": sum(run.failure_code is not None for run in self.runs),
            "failure_codes": sorted(
                {run.failure_code for run in self.runs if run.failure_code is not None}
            ),
            "failure_stages": sorted(
                {run.failure_stage for run in self.runs if run.failure_stage is not None}
            ),
            "schema_diagnostic_categories": [category.value for category in diagnostic_categories],
            "schema_diagnostic_runs": {
                category.value: sum(
                    category in run.schema_diagnostic_categories for run in self.runs
                )
                for category in diagnostic_categories
            },
            "business_failure_codes": list(business_failure_codes),
            "business_failure_runs": {
                code: sum(code in run.business_failure_codes for run in self.runs)
                for code in business_failure_codes
            },
            "seven_stage_runs": sum(run.seven_stages_completed for run in self.runs),
            "recovered_runs": sum(run.recovered_after_restart for run in self.runs),
            "claims_correct": claims_correct,
            "claims_expected": claims_expected,
            "field_disposition_accuracy": round(claims_correct / claims_expected, 6),
            "citations_valid": citations_valid,
            "citations_checked": citations_checked,
            "unsupported_claims": sum(run.unsupported_claims for run in self.runs),
            "answer_correct_runs": sum(run.answer_correct for run in self.runs),
            "provider_calls": sum(run.calls for run in self.runs),
            "input_tokens": sum(run.input_tokens for run in self.runs),
            "output_tokens": sum(run.output_tokens for run in self.runs),
            "total_tokens": sum(run.input_tokens + run.output_tokens for run in self.runs),
            "elapsed_seconds": [round(value, 6) for value in elapsed],
            "aggregate_seconds": round(sum(elapsed), 6),
            "median_seconds": round(median(elapsed), 6),
            "maximum_seconds": round(max(elapsed), 6),
            "per_run_bound_seconds": self.per_run_seconds,
            "aggregate_bound_seconds": self.per_run_seconds * len(self.runs),
            "overall_pass": self.overall_pass,
        }


async def evaluate_ollama_agent(
    config: OllamaAgentEvaluationConfig,
    *,
    provider: ModelProvider | None = None,
) -> OllamaAgentEvaluationReport:
    """Run fresh temporary Cases without exposing Ollama through product HTTP/UI."""

    selected_provider = provider or build_model_provider(
        ModelProviderConfig(
            provider=ModelProviderKind.OLLAMA,
            model_id=config.model_id,
            timeout_seconds=120.0,
            max_input_bytes=100_000,
            max_output_bytes=64_000,
        )
    )
    outcomes: list[OllamaAgentRunEvidence] = []
    with TemporaryDirectory(prefix="lifecase-ollama-agent-evaluation-") as temporary:
        root = Path(temporary)
        for index in range(config.runs):
            answer_expected = "unknown" if index == config.runs - 1 else "answered"
            outcomes.append(
                await _evaluate_one(
                    root / f"run-{index + 1}",
                    selected_provider,
                    answer_expected=answer_expected,
                )
            )
    return OllamaAgentEvaluationReport(
        model_id=config.model_id,
        per_run_seconds=config.per_run_seconds,
        runs=tuple(outcomes),
    )


async def _evaluate_one(
    root: Path,
    provider: ModelProvider,
    *,
    answer_expected: str,
) -> OllamaAgentRunEvidence:
    connection, case_id = _prepare_case(root, answer_expected=answer_expected)
    diagnostic_provider = _SchemaDiagnosticProvider(provider)
    try:
        service = PortfolioAgentDemoService(
            connection,
            enabled=True,
            evaluation_provider=diagnostic_provider,
            call_reservation=_CALL_RESERVATION,
            stage_budget=_STAGE_BUDGET,
            stage_registry=_OLLAMA_EVALUATION_REGISTRY,
        )
        service.create(case_id)
        started = perf_counter()
        failure_code: str | None = None
        try:
            state = await service.execute(case_id)
        except AgentServiceError:
            state = service.get_state(case_id)
            if state.run is not None and state.run.stages:
                failure_code = state.run.stages[-1].error_code
            if failure_code is None:
                failure_code = "agent_failure"
        elapsed_seconds = perf_counter() - started
        run = state.run
        if run is None:
            raise OllamaAgentEvaluationError("Ollama Agent evaluation produced no run")
        claims = ClaimService(connection).list_case_claims(case_id)
        claims_correct, unsupported_claims, citations_valid, citations_checked = _score_claims(
            connection, claims
        )
        answer = state.answer
        answer_citations = (
            () if answer is None else tuple(cast(list[dict[str, object]], answer["citations"]))
        )
        answer_correct = answer is not None and answer["status"] == answer_expected
        if answer_expected == "unknown":
            answer_correct = answer_correct and not answer_citations
        else:
            citations_checked += len(answer_citations)
            valid = sum(
                item.get("case_id") == case_id and item.get("quote_hash") is not None
                for item in answer_citations
            )
            citations_valid += valid
            answer_correct = (
                answer_correct and bool(answer_citations) and valid == len(answer_citations)
            )
        snapshot = state
        diagnostics = diagnostic_provider.categories
        business_failure_codes: tuple[str, ...] = ()
        if failure_code == "agent_schema_invalid" and diagnostic_provider.last_categories == ():
            diagnostics = (*diagnostics, SchemaDiagnosticCategory.BUSINESS_VALIDATION)
            business_failure_codes = diagnostic_provider.last_business_failure_codes
    finally:
        connection.close()

    restored_connection = connect_database(root / "lifecase.sqlite3")
    try:
        restored = PortfolioAgentDemoService(restored_connection, enabled=True).get_state(case_id)
    finally:
        restored_connection.close()

    return OllamaAgentRunEvidence(
        answer_expected=answer_expected,
        failure_code=failure_code,
        elapsed_seconds=elapsed_seconds,
        calls=run.usage.calls,
        input_tokens=run.usage.input_tokens,
        output_tokens=run.usage.output_tokens,
        claims_correct=claims_correct,
        claims_expected=len(_EXPECTED_CLAIMS),
        citations_valid=citations_valid,
        citations_checked=citations_checked,
        unsupported_claims=unsupported_claims,
        seven_stages_completed=(
            run.status is AgentRunStatus.COMPLETED
            and len(run.stages) == 7
            and all(stage.checkpoint is not None for stage in run.stages)
        ),
        answer_correct=answer_correct,
        recovered_after_restart=restored == snapshot,
        schema_diagnostic_categories=tuple(dict.fromkeys(diagnostics)),
        business_failure_codes=business_failure_codes,
        failure_stage=(
            run.stages[-1].stage.value if failure_code is not None and run.stages else None
        ),
    )


class _SchemaDiagnosticProvider:
    """Observe only bounded structural categories around an evaluation Provider."""

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider
        self._call_categories: list[tuple[SchemaDiagnosticCategory, ...]] = []
        self._business_failure_codes: list[tuple[str, ...]] = []

    @property
    def categories(self) -> tuple[SchemaDiagnosticCategory, ...]:
        return tuple(category for call in self._call_categories for category in call)

    @property
    def last_categories(self) -> tuple[SchemaDiagnosticCategory, ...]:
        return self._call_categories[-1] if self._call_categories else ()

    @property
    def last_business_failure_codes(self) -> tuple[str, ...]:
        return self._business_failure_codes[-1] if self._business_failure_codes else ()

    async def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResult:
        result = await self._provider.generate_structured(request)
        categories = classify_schema_mismatches(result.output_json, request.output_schema_json)
        self._call_categories.append(categories)
        business_failure_codes: tuple[str, ...] = ()
        if not categories and request.output_schema_name == WARRANTY_EXTRACTION_OUTPUT_SCHEMA_NAME:
            business_failure_codes = diagnose_warranty_extraction_candidate(
                request.input_json,
                result.output_json,
            )
        self._business_failure_codes.append(business_failure_codes)
        return result


def _prepare_case(root: Path, *, answer_expected: str) -> tuple[sqlite3.Connection, str]:
    input_root = root / "input"
    data_root = root / "data"
    input_root.mkdir(parents=True)
    source_path = input_root / "synthetic-warranty.txt"
    source_path.write_text(_SYNTHETIC_EVIDENCE, encoding="utf-8")
    connection = connect_database(root / "lifecase.sqlite3")
    try:
        apply_migrations(connection)
        goal = (
            "warranty marker alpha-7391" if answer_expected == "answered" else "zeta-no-match-0000"
        )
        case = CaseService(connection).create_case(
            title="Synthetic Ollama Agent Evaluation",
            goal=goal,
            template_id="warranty",
        )
        object_store = ContentAddressedObjectStore(
            ObjectStoreConfig(
                data_root=data_root,
                allowed_source_roots=(input_root,),
                max_file_size_bytes=1_000_000,
                allowed_media_types=frozenset({"text/plain"}),
            )
        )
        SourceService(
            connection,
            SourceImporter(connection, object_store),
            ExtractionService(connection, ExtractionConfig(data_root=data_root)),
        ).import_source(
            case_id=case.id,
            selected_path=source_path,
            display_name="synthetic-warranty.txt",
        )
    except Exception:
        connection.close()
        raise
    return connection, case.id


def _score_claims(
    connection: sqlite3.Connection,
    claims: tuple[ClaimRecord, ...],
) -> tuple[int, int, int, int]:
    service = ClaimService(connection)
    correct = 0
    unsupported = 0
    citations_valid = 0
    citations_checked = 0
    for claim in claims:
        expected = _EXPECTED_CLAIMS.get(claim.field)
        if expected is None:
            unsupported += 1
            continue
        expected_value, expected_status = expected
        if claim.value.to_python() == expected_value and claim.status is expected_status:
            correct += 1
        else:
            unsupported += 1
        sources = service.list_claim_sources(claim.id)
        citations_checked += len(sources)
        expected_relation = "context" if claim.status is ClaimStatus.INFERRED else "supports"
        citations_valid += sum(
            source.case_id == claim.case_id and source.relation.value == expected_relation
            for source in sources
        )
    return correct, unsupported, citations_valid, citations_checked


def render_safe_report(report: OllamaAgentEvaluationReport) -> str:
    return json.dumps(report.to_safe_dict(), ensure_ascii=False, sort_keys=True) + "\n"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the local-only Ollama warranty Agent evaluation"
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--per-run-seconds", type=float, default=300.0)
    arguments = parser.parse_args()
    try:
        report = asyncio.run(
            evaluate_ollama_agent(
                OllamaAgentEvaluationConfig(
                    model_id=arguments.model,
                    runs=arguments.runs,
                    per_run_seconds=arguments.per_run_seconds,
                )
            )
        )
    except Exception:
        print("ollama_agent_evaluation_failed")
        return 2
    print(render_safe_report(report), end="")
    return 0 if report.overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
