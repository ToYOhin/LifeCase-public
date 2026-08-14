import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { LiveAgentDossier } from "./LiveAgentDossier";
import type {
  AgentAnswer,
  AgentRun,
  AgentStageName,
  LiveAgentResult,
} from "./types";

describe("Live Agent evidence dossier", () => {
  it("puts an answered conclusion and readable citation before technical detail", () => {
    const markup = renderToStaticMarkup(
      <LiveAgentDossier result={liveResult(answered())} busy={false} />,
    );

    expect(markup).toContain("已回答");
    expect(markup).toContain("保修期为 24 个月。");
    expect(markup).toContain("证据 1");
    expect(markup).toContain("7 / 7");
    expect(markup).toContain("deepseek-v4-pro");
    expect(markup).not.toContain("deepseek-v4-flash");
    expect(markup.indexOf("保修期为 24 个月。")).toBeLessThan(
      markup.indexOf("候选事实"),
    );
    expect(markup.indexOf("候选事实")).toBeLessThan(markup.indexOf("运行详情"));
    expect(markup).toMatch(/<details[^>]*><summary>运行详情/);
    expect(markup).not.toMatch(/<details[^>]*open/);
  });

  it("renders unknown and claim values without raw JSON", () => {
    const result = liveResult(unknown());
    result.run!.claims[0] = {
      ...result.run!.claims[0],
      field: "amount",
      value: { amount_minor: 25900, currency: "CNY" },
    };
    const markup = renderToStaticMarkup(
      <LiveAgentDossier result={result} busy={false} />,
    );

    expect(markup).toContain("证据不足");
    expect(markup).toContain("当前材料不足以回答这个问题");
    expect(markup).not.toContain("No evidence items");
    expect(markup).toContain("0 条引用");
    expect(markup).toContain("金额");
    expect(markup).toContain("¥259.00");
    expect(markup).not.toContain("amount_minor");
    expect(markup).not.toContain("{&quot;");
  });
  it("offers formal reanalysis and exposes preserved run versions on demand", () => {
    const result = liveResult(answered());
    result.run_history = [
      { ...result.run_history[0], version: 2, id: "run_synthetic_2" },
      result.run_history[0],
    ];
    const markup = renderToStaticMarkup(
      <LiveAgentDossier result={result} busy={false} onReanalyze={() => undefined} />,
    );

    expect(markup).toContain("重新分析");
    expect(markup).toContain("历史分析");
    expect(markup).toContain("2 个版本");
    expect(markup).toContain("版本 2");
  });

  it("renders repeated Segment citations without duplicate React keys", () => {
    const answer = answered();
    answer.citations.push({ ...answer.citations[0] });
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);

    try {
      const markup = renderToStaticMarkup(
        <LiveAgentDossier result={liveResult(answer)} busy={false} />,
      );

      expect(markup).toContain("证据 2");
      expect(error).not.toHaveBeenCalled();
    } finally {
      error.mockRestore();
    }
  });
});

function liveResult(answer: AgentAnswer): LiveAgentResult {
  return {
    live_version: "live_agent_read_only.v1",
    capability: {
      status: "ready",
      provider: "deepseek",
      model_id: "deepseek-v4-flash",
      action: "ready",
      supported_case_types: ["warranty"],
    },
    preview: {
      case_type: "warranty",
      evidence: { segment_count: 1, utf8_bytes: 171 },
      budget: budget(),
      read_only_notice: "只产生候选事实和只读回答，不执行外部动作。",
      preview_sha256: "a".repeat(64),
    },
    run: {
      id: "run_synthetic",
      case_id: "case_synthetic",
      status: "completed",
      version: 1,
      current_stage: null,
      last_completed_stage: "validate",
      cancellation_requested: false,
      can_execute: false,
      can_retry: false,
      recovery_required: false,
      provider: "deepseek",
      model_id: "deepseek-v4-pro",
      budget: budget(),
      usage: {
        calls: 7,
        input_tokens: 4306,
        output_tokens: 1963,
        total_tokens: 6269,
        cost_minor: 49,
        cost_currency: "CNY",
      },
      stages: stages(),
      claims: [
        {
          id: "claim_synthetic",
          field: "product_name",
          value: "SynthPhone",
          status: "candidate",
          confidence: 0.9,
          created_by: "agent",
          version: 1,
        },
      ],
      answer,
      tool_calls: [
        {
          name: "evidence_context.search.v1",
          risk: "A",
          call_count: 1,
          result: answer.status === "answered" ? "match" : "no_match",
        },
      ],
      created_at: "2026-08-12T00:00:00Z",
      updated_at: "2026-08-12T00:00:01Z",
      completed_at: "2026-08-12T00:00:01Z",
    },
    run_history: [
      {
        version: 1,
        id: "run_synthetic",
        status: "completed",
        provider: "deepseek",
        model_id: "deepseek-v4-pro",
        answer_status: answer.status,
        calls: 7,
        total_tokens: 6269,
        created_at: "2026-08-12T00:00:00Z",
        completed_at: "2026-08-12T00:00:01Z",
      },
    ],
  };
}

function answered(): AgentAnswer {
  return {
    status: "answered",
    statements: ["保修期为 24 个月。"],
    uncertainty_reason: null,
    citations: [
      {
        case_id: "case_synthetic",
        source_id: "source_synthetic",
        segment_id: "segment_synthetic",
        sequence: 0,
        page: null,
        text_sha256: "b".repeat(64),
        quote_start: 0,
        quote_end: 171,
        quote_hash: "c".repeat(64),
      },
    ],
  };
}

function unknown(): AgentAnswer {
  return {
    status: "unknown",
    statements: [],
    uncertainty_reason: "未找到能够回答当前问题的证据。",
    citations: [],
  };
}

function budget() {
  return {
    max_calls: 14,
    max_input_tokens: 56000,
    max_output_tokens: 56000,
    max_total_tokens: 112000,
    max_cost_minor: 100,
    cost_currency: "CNY" as const,
  };
}

function stages() {
  const names: AgentStageName[] = [
    "extract",
    "normalize",
    "link_claims",
    "classify_case",
    "check_gaps",
    "propose_actions",
    "validate",
  ];
  return names.map((name) => ({
    name,
    status: "checkpointed" as const,
    version: 1,
    calls: 1,
    input_tokens: 1,
    output_tokens: 1,
    cost_minor: 0,
    provider: "deepseek",
    model_id: "deepseek-v4-pro",
    error_code: null,
    retryable: null,
    summary: null,
    updated_at: "2026-08-12T00:00:01Z",
  }));
}
