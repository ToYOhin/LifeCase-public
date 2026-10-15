import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { HistoryAnswerContent, LiveAgentDossier } from "./LiveAgentDossier";
import type {
  AgentAnswer,
  AgentStageName,
  LiveAgentResult,
} from "./types";

describe("Live Agent evidence dossier", () => {
  it("folds completed execution details and fact snapshots while keeping the answer and stale notice visible", () => {
    const result = liveResult(answered());
    result.run!.status = "completed";
    result.run!.analysis = { question: "合成问题", basis_sha256: "a".repeat(64), basis_status: "changed", facts: [] };
    const markup = renderToStaticMarkup(<LiveAgentDossier result={result} busy={false} />);
    expect(markup).toMatch(/<details class="dossier-facts"/);
    expect(markup).toMatch(/<details class="stage-rail"[^>]*>/);
    expect(markup).not.toMatch(/<details class="stage-rail"[^>]*open/);
    expect(markup.slice(0, markup.indexOf('<details class="dossier-facts"'))).toContain("依据已变化");
    expect(markup.slice(0, markup.indexOf('<details class="dossier-facts"'))).toContain(result.run!.answer!.statements[0]);
  });
  it("reads the saved answer when current evidence cannot produce a new preview", () => {
    const result = liveResult(answered());
    result.preview = null;
    result.run!.analysis = { question: "原问题不变", basis_sha256: "a".repeat(64), basis_status: "unavailable", facts: [] };
    const markup = renderToStaticMarkup(<LiveAgentDossier result={result} busy={false} />);
    expect(markup).toContain("原问题不变");
    expect(markup).toContain("依据暂时无法核对");
    expect(markup).toContain(result.run!.answer!.statements[0]);
  });
  it("shows the immutable question, corrected fact authority, and changed-basis warning for question analysis", () => {
    const result = liveResult(answered());
    Object.assign(result.run!, {
      stage_order: ["validate"], stages: stages(["validate"]), claims: [],
      analysis: {
        question: "修正后的购买日期是什么？", basis_sha256: "d".repeat(64), basis_status: "changed",
        facts: [{ claim_id: "claim_date", field: "purchase_date", value: "2026-08-03", version: 3,
          status: "corrected", confirmation_basis: "correction", evidence_status: "valid", conflict: false,
          usable_as_fact: true, confirmation: { actor: "synthetic_reviewer", reason: "核对合成付款单后修正", occurred_at: "2026-09-19T01:00:00Z" }, citations: [] }],
      },
    });
    const markup = renderToStaticMarkup(<LiveAgentDossier result={result} busy={false} onReanalyze={() => undefined} />);
    expect(markup).toContain("修正后的购买日期是什么？");
    expect(markup).toContain("依据已变化");
    expect(markup).toContain("查看分析时的事实");
    expect(markup).toContain("2026-08-03");
    expect(markup).toContain("版本 3");
    expect(markup).toContain("synthetic_reviewer");
    expect(markup).toContain("人工修正");
    expect(markup).toContain("核对合成付款单后修正");
    expect(markup).toContain("问题证据分析");
    expect(markup).toContain("重新分析");
    expect(markup).not.toContain("提取候选事实");
    expect(markup).not.toContain("本次没有生成候选事实");
  });

  it("lets prior answers remain readable with their original questions and basis status", () => {
    const result = liveResult(answered());
    result.run_history = [
      { ...result.run_history[0], version: 2, id: "run_new" },
      { ...result.run_history[0], question: "旧问题：当时登记了什么？", basis_status: "changed",
        answer: { ...answered(), statements: ["旧答案保留原记录。"] } } as LiveAgentResult["run_history"][number],
    ];
    const markup = renderToStaticMarkup(<LiveAgentDossier result={result} busy={false} />);
    expect(markup).toContain("旧问题：当时登记了什么？");
    expect(markup).toContain("旧答案保留原记录。");
    expect(markup).toContain("依据已变化");
  });

  it("does not present uncheckable or legacy answers as current evidence", () => {
    const result = liveResult(answered());
    Object.assign(result.run!, { analysis: {
      question: "原材料目前可用吗？", basis_sha256: "d".repeat(64), basis_status: "unavailable", facts: [],
    } });
    let markup = renderToStaticMarkup(<LiveAgentDossier result={result} busy={false} />);
    expect(markup).toContain("依据暂时无法核对");
    expect(markup).not.toContain("依据与当前记录一致");
    delete result.run!.analysis;
    markup = renderToStaticMarkup(<LiveAgentDossier result={result} busy={false} />);
    expect(markup).toContain("旧运行未保存独立问题与事实快照");
    expect(markup).not.toContain("依据与当前记录一致");
  });
  it.each([
    ["candidate", "候选", "待人工复核"],
    ["inferred", "推断", "待人工复核"],
    ["confirmed", "已确认", "已按来源或人工记录确认"],
    ["corrected", "已修正", "已采用修正后的事实"],
    ["rejected", "已拒绝", "不作为已确认事实"],
  ])("shows the current %s claim status without a blanket unconfirmed label", (status, label, note) => {
    const result = liveResult(answered());
    result.run!.claims[0].status = status;
    const markup = renderToStaticMarkup(<LiveAgentDossier result={result} busy={false} />);

    expect(markup).toContain("分析事实");
    expect(markup).toContain(label);
    expect(markup).toContain(note);
    expect(markup).not.toContain("模型生成，尚未确认");
    expect(markup).not.toContain("未经确认");
  });

  it("puts an answered conclusion and readable citation before technical detail", () => {
    const markup = renderToStaticMarkup(
      <LiveAgentDossier result={liveResult(answered())} busy={false} />,
    );

    expect(markup).toContain("已回答");
    expect(markup).toContain("保修期为 24 个月。");
    expect(markup).toContain("证据 1");
    expect(markup).toContain("2 / 2");
    expect(markup).toContain("提取候选事实");
    expect(markup).toContain("证据检索 / 回答");
    expect(markup).not.toMatch(/七阶段|规范化|关联事实|识别案件类型|检查缺件|提出只读建议/);
    expect(markup).toContain("deepseek-v4-pro");
    expect(markup).not.toContain("deepseek-v4-flash");
    expect(markup.indexOf("保修期为 24 个月。")).toBeLessThan(
      markup.indexOf("分析事实"),
    );
    expect(markup.indexOf("分析事实")).toBeLessThan(markup.indexOf("运行详情"));
    expect(markup).toMatch(/<details[^>]*><summary>运行详情/);
    expect(markup).not.toMatch(/<details[^>]*open/);
  });

  it("shows only the persisted two-stage plan before any stage has completed", () => {
    const result = liveResult(answered());
    Object.assign(result.run!, {
      status: "queued",
      answer: null,
      stages: [],
      last_completed_stage: null,
      can_execute: true,
    });
    const markup = renderToStaticMarkup(
      <LiveAgentDossier result={result} busy={false} onExecute={() => undefined} />,
    );

    expect(markup).toContain("0 / 2");
    expect(markup).toContain("提取候选事实");
    expect(markup).toContain("证据检索 / 回答");
    expect(markup).toContain("显式发起真实只读运行");
    expect(markup).not.toMatch(/七阶段|规范化|关联事实|识别案件类型|检查缺件|提出只读建议/);
  });

  it("keeps the saved seven-stage plan and recovery command explicit for a legacy run", () => {
    const result = liveResult(answered());
    Object.assign(result.run!, {
      stage_order: legacyStageOrder,
      status: "needs_review",
      answer: null,
      stages: stages(legacyStageOrder).slice(0, 2),
      last_completed_stage: "normalize",
      recovery_required: true,
    });
    const markup = renderToStaticMarkup(
      <LiveAgentDossier result={result} busy={false} onRecover={() => undefined} />,
    );

    expect(markup).toContain("旧版七阶段");
    expect(markup).toContain("2 / 7");
    expect(markup).toContain("规范化");
    expect(markup).toContain("关联事实");
    expect(markup).toContain("恢复中断预留");
    expect(markup.indexOf("规范化")).toBeLessThan(markup.indexOf("关联事实"));
    expect(markup.indexOf("提出只读建议")).toBeLessThan(markup.indexOf("证据检索 / 回答"));
  });

  it("renders unknown and claim values without raw JSON", () => {
    const result = liveResult(unknown());
    result.run!.answer!.uncertainty_reason = "  当前型号 H8 与 H9 冲突，请先核实。  ";
    result.run!.claims[0] = {
      ...result.run!.claims[0],
      field: "amount",
      value: { amount_minor: 25900, currency: "CNY" },
    };
    const markup = renderToStaticMarkup(
      <LiveAgentDossier result={result} busy={false} />,
    );

    expect(markup).toContain("证据不足");
    expect(markup.slice(0, markup.indexOf('<details class="dossier-facts"'))).toContain(
      "<p>当前型号 H8 与 H9 冲突，请先核实。</p>",
    );
    expect(markup).not.toContain("当前材料不足以回答这个问题");
    expect(markup).not.toContain("No evidence items");
    expect(markup).toContain("0 条引用");
    expect(markup).toContain("金额");
    expect(markup).toContain("¥259.00");
    expect(markup).not.toContain("amount_minor");
    expect(markup).not.toContain("{&quot;");
  });

  it.each(["changed", "unavailable"] as const)("reads the saved historical unknown reason while basis is %s", (basis_status) => {
    const result = liveResult({ ...unknown(), uncertainty_reason: "当前缺少序列号。" });
    const item = { ...result.run_history[0], question: "当时能否确定截止日期？", basis_status,
      answer: { ...unknown(), uncertainty_reason: "  当时缺少保修起算日期，无法计算截止日期。  " } };
    const markup = renderToStaticMarkup(<HistoryAnswerContent item={item} caseId="case_synthetic" />);

    expect(markup).toContain("当时能否确定截止日期？");
    expect(markup).toContain("<p>当时缺少保修起算日期，无法计算截止日期。</p>");
    expect(markup).toContain(basis_status === "changed" ? "依据已变化" : "依据暂时无法核对");
    expect(markup).not.toContain("当前缺少序列号");
    expect(markup).not.toContain("当时材料不足以回答");
  });

  it.each([null, "", " \n\t "])("keeps current and historical fallback text for an empty reason: %j", (uncertainty_reason) => {
    const answer = { ...unknown(), uncertainty_reason };
    const result = liveResult(answer);
    const current = renderToStaticMarkup(<LiveAgentDossier result={result} busy={false} />);
    const historical = renderToStaticMarkup(
      <HistoryAnswerContent item={{ ...result.run_history[0], answer }} caseId="case_synthetic" />,
    );
    expect(current).toContain("<p>当前材料不足以回答这个问题。</p>");
    expect(historical).toContain("<p>当时材料不足以回答。</p>");
    expect(historical).toContain("旧运行没有绑定事实快照");
  });

  it("escapes unknown reasons as text in both current and historical answers", () => {
    const answer = { ...unknown(), uncertainty_reason: "缺少 <script>alert(1)</script> 所述材料 & 购买凭证。" };
    const result = liveResult(answer);
    const current = renderToStaticMarkup(<LiveAgentDossier result={result} busy={false} />);
    const historical = renderToStaticMarkup(
      <HistoryAnswerContent item={{ ...result.run_history[0], answer }} caseId="case_synthetic" />,
    );
    for (const markup of [current, historical]) {
      expect(markup).toContain("缺少 &lt;script&gt;alert(1)&lt;/script&gt; 所述材料 &amp; 购买凭证。");
      expect(markup).not.toContain("<script>");
    }
  });

  it("keeps answered statements and ignores a leftover unknown reason in both views", () => {
    const answer = { ...answered(), uncertainty_reason: "不应展示的残留理由。" };
    const result = liveResult(answer);
    const current = renderToStaticMarkup(<LiveAgentDossier result={result} busy={false} />);
    const historical = renderToStaticMarkup(
      <HistoryAnswerContent item={{ ...result.run_history[0], answer }} caseId="case_synthetic" loadCitations={false} />,
    );
    for (const markup of [current, historical]) {
      expect(markup).toContain("保修期为 24 个月。");
      expect(markup).not.toContain("不应展示的残留理由");
    }
  });
  it("offers formal reanalysis and exposes preserved run versions on demand", () => {
    const result = liveResult(answered());
    result.run_history = [
      { ...result.run_history[0], version: 2, id: "run_synthetic_2" },
      { ...result.run_history[0], stage_order: legacyStageOrder },
    ];
    const markup = renderToStaticMarkup(
      <LiveAgentDossier result={result} busy={false} onReanalyze={() => undefined} />,
    );

    expect(markup).toContain("重新分析");
    expect(markup).toContain("历史分析");
    expect(markup).toContain("2 个版本");
    expect(markup).toContain("版本 2");
    expect(markup).toContain("旧版七阶段");
    expect(markup).toContain("提取与证据回答");
  });

  it.each(["disabled", "unavailable"] as const)("keeps existing answers readable without an unusable reanalysis button when capability is %s", (status) => {
    const result = liveResult(answered());
    result.capability.status = status;
    const markup = renderToStaticMarkup(
      <LiveAgentDossier result={result} busy={false} onReanalyze={() => undefined} />,
    );

    expect(markup).toContain("保修期为 24 个月。");
    expect(markup).toContain("旧运行未保存独立问题与事实快照");
    expect(markup).not.toMatch(/<button\b[^>]*>重新分析<\/button>/);
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
    live_version: "live_agent_read_only.v2",
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
      stage_order: ["extract", "validate"],
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
        calls: 2,
        input_tokens: 4306,
        output_tokens: 1963,
        total_tokens: 6269,
        cost_minor: 49,
        cost_currency: "CNY",
      },
      stages: stages(["extract", "validate"]),
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
        stage_order: ["extract", "validate"],
        status: "completed",
        provider: "deepseek",
        model_id: "deepseek-v4-pro",
        answer_status: answer.status,
        calls: 2,
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

const legacyStageOrder: AgentStageName[] = [
  "extract",
  "normalize",
  "link_claims",
  "classify_case",
  "check_gaps",
  "propose_actions",
  "validate",
];

function stages(names: AgentStageName[]) {
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
