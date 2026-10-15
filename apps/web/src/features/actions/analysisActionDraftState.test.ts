import { describe, expect, it } from "vitest";

import { canPrepareAnalysisAction, reconcileAnalysisDraft, analysisDraftStorageKey, readRetainedAnalysisEdits } from "./analysisActionDraftState";
import type { AnalysisActionDraft } from "./analysisActionDraftApi";
import type { AgentRun } from "../agentDemo/types";

const initial: AnalysisActionDraft = {
  case_id: "case_a", run_id: "portfolio-live-q1-a", run_version: 4, question: "本次问题",
  basis_sha256: "a".repeat(64), answer_sha256: "b".repeat(64), draft_text: "默认短草案", saved_action: null,
};

describe("analysis action draft editing decisions", () => {
  it("reads only old runs known to the current Case and never promotes their local text into authority", () => {
    const reads: string[] = [];
    const values = new Map([
      [analysisDraftStorageKey("case_a", "run_current"), "当前编辑不能被替换"],
      [analysisDraftStorageKey("case_a", "run_previous"), "旧分析尚未保存的正文"],
      [analysisDraftStorageKey("case_a", "run_unknown"), "不在案件运行历史中"],
      [analysisDraftStorageKey("case_b", "run_previous"), "别案正文"],
    ]);
    const result = readRetainedAnalysisEdits("case_a", "run_current", ["run_current", "run_previous", "run_previous"], (key) => {
      reads.push(key); return values.get(key) ?? null;
    });
    expect(reads).toEqual([analysisDraftStorageKey("case_a", "run_previous")]);
    expect(result).toEqual([{ runId: "run_previous", text: "旧分析尚未保存的正文" }]);
    expect(values.get(analysisDraftStorageKey("case_a", "run_current"))).toBe("当前编辑不能被替换");
  });

  it("retains already read old edits if a later browser-storage read fails", () => {
    const previous = [{ runId: "run_previous", text: "仍可回看的旧编辑" }];
    expect(readRetainedAnalysisEdits("case_a", "run_current", ["run_previous"], () => { throw new Error("temporarily unavailable"); }, previous))
      .toEqual(previous);
    expect(readRetainedAnalysisEdits("case_a", "run_current", [], () => { throw new Error("unavailable"); }, previous))
      .toEqual([]);
    expect(readRetainedAnalysisEdits("case_a", "run_current", ["run_previous"], () => "x".repeat(501)))
      .toEqual([]);
  });

  it("keeps a known same-Case editor in read-only memory when storage could not retain it", () => {
    const currentEdit = { caseId: "case_a", runId: "run_previous", text: "存储被拒绝时仍保留的人工编辑" };
    for (const read of [() => null, () => { throw new Error("storage denied"); }]) {
      expect(readRetainedAnalysisEdits("case_a", "run_current", ["run_previous"], read, [], currentEdit))
        .toEqual([{ runId: currentEdit.runId, text: currentEdit.text }]);
    }
    expect(readRetainedAnalysisEdits("case_b", "run_current", ["run_previous"], () => null, [], currentEdit)).toEqual([]);
    expect(readRetainedAnalysisEdits("case_a", "run_current", [], () => null, [], currentEdit)).toEqual([]);
    expect(readRetainedAnalysisEdits("case_a", "run_previous", ["run_previous"], () => null, [], currentEdit)).toEqual([]);
  });

  it("keeps user edits when the same analysis is refreshed", () => {
    expect(reconcileAnalysisDraft("我正在编辑的内容", initial, { ...initial })).toEqual({
      input: "我正在编辑的内容", needsChoice: false,
    });
  });

  it("requires an explicit choice when another window saved a new Action version", () => {
    const next = { ...initial, draft_text: "其他窗口已保存", saved_action: {
      id: "action_a", version: 1, draft_text: "其他窗口已保存", status: "ready" as const,
    } };
    expect(reconcileAnalysisDraft("我的未保存编辑", initial, next)).toEqual({ input: "我的未保存编辑", needsChoice: true });
    expect(reconcileAnalysisDraft(initial.draft_text, initial, next)).toEqual({ input: next.draft_text, needsChoice: false });
  });

  it("does not silently attach restored text or old analysis text to a new authority", () => {
    expect(reconcileAnalysisDraft("刷新前保留的编辑", null, initial)).toEqual({ input: "刷新前保留的编辑", needsChoice: true });
    expect(reconcileAnalysisDraft("我的编辑", initial, { ...initial, run_id: "portfolio-live-q1-b" }))
      .toEqual({ input: "我的编辑", needsChoice: true });
    expect(analysisDraftStorageKey("case_a", "run_b")).not.toBe(analysisDraftStorageKey("case_b", "run_a"));
  });

  it("only offers preparation from a completed, answered, current q1 Run", () => {
    const run = { id: "portfolio-live-q1-a", case_id: "case_a", status: "completed",
      analysis: { basis_status: "current" }, answer: { status: "answered" } } as AgentRun;
    expect(canPrepareAnalysisAction(run, true)).toBe(true);
    expect(canPrepareAnalysisAction(run, false)).toBe(false);
    expect(canPrepareAnalysisAction({ ...run, id: "portfolio-live-v2-legacy", analysis: null }, true)).toBe(false);
    for (const basis_status of ["changed", "unavailable"] as const) {
      expect(canPrepareAnalysisAction({ ...run, analysis: { ...run.analysis!, basis_status } }, true)).toBe(false);
    }
    expect(canPrepareAnalysisAction({ ...run, status: "cancelled" }, true)).toBe(false);
    expect(canPrepareAnalysisAction({ ...run, answer: { ...run.answer!, status: "unknown" } }, true)).toBe(false);
  });
});
