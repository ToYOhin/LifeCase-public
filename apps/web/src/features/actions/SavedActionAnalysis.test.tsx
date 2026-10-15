import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SavedActionAnalysis } from "./SavedActionAnalysis";
import { HistoryAnswerContent } from "../agentDemo/LiveAgentDossier";
import type { ActionRecord } from "./types";
import type { LiveAgentRunHistory } from "../agentDemo/types";

describe("saved Action source entry", () => {
  it("offers a compact explicit read entry even for changed history, without a model command", () => {
    const item = { id: "action_a", case_id: "case_a", kind: "prepare_communication", version: 1,
      payload: { analysis_run_id: "run_original" }, analysis_basis_status: "changed", status: "ready", risk_level: "B",
      payload_sha256: "a".repeat(64), blockers: [], blockers_sha256: "b".repeat(64), requirements: [], dependencies: [], deadline: null,
      communication_draft: null, completion_evidence: [], topological_position: 0, created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" } satisfies ActionRecord;
    const html = renderToStaticMarkup(<SavedActionAnalysis item={item} available />);
    expect(html).toContain("查看来源分析与引用"); expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("disabled"); expect(html).not.toContain("重新分析");
    expect(renderToStaticMarkup(<SavedActionAnalysis item={item} available={false} />)).toContain('disabled=""');
    expect(renderToStaticMarkup(<SavedActionAnalysis item={{ ...item, payload: {} }} available />)).toBe("");
  });

  it("reuses historical question/answer/citations and keeps changed or uncheckable basis explicit", () => {
    for (const basis_status of ["changed", "unavailable", "legacy"] as const) {
      const item = { id: "run_a", version: 1, stage_order: ["validate"], status: "completed", provider: "disabled", model_id: null,
        answer_status: "answered", calls: 1, total_tokens: 10, created_at: "2026-09-24T00:00:00Z", completed_at: "2026-09-24T00:00:01Z",
        question: "原始问题", basis_status, answer: { status: "answered", statements: ["原始回答"], uncertainty_reason: null,
        citations: [{ case_id: "case_a", source_id: "source_a", segment_id: "segment_a", sequence: 0, page: null,
          text_sha256: "b".repeat(64), quote_start: 0, quote_end: 1, quote_hash: "c".repeat(64) }], fact_references: [] } } satisfies LiveAgentRunHistory;
      const html = renderToStaticMarkup(<HistoryAnswerContent item={item} caseId="case_a" />);
      expect(html).toContain("原始问题"); expect(html).toContain("原始回答"); expect(html).toContain("证据 1");
      expect(html).toContain(basis_status === "changed" ? "依据已变化" : basis_status === "unavailable" ? "无法核对" : "旧运行");
      expect(html).not.toContain("<button");
    }
  });
});
