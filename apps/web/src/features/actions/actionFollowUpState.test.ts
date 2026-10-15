import { describe, expect, it } from "vitest";
import type { LiveAgentResult } from "../agentDemo/types";
import type { ActionRecord } from "./types";
import { changedActionTarget, originalActionQuestion } from "./actionFollowUpState";

const oldAction = { id: "old_action", case_id: "case_a", kind: "prepare_communication", status: "approved",
  version: 2, payload: { analysis_run_id: "old_run" }, analysis_basis_status: "changed" } as unknown as ActionRecord;
const target = { caseId: "case_a", actionId: "old_action", runId: "old_run" };
const result = { run: { id: "newer_run", case_id: "case_a", analysis: { question: "另一条本次问题" } },
  run_history: [{ id: "old_run", question: "原待办明确绑定的问题" }, { id: "newer_run", question: "另一条本次问题" }] } as LiveAgentResult;

describe("changed Action navigation carries identity without authority", () => {
  it("targets only a freshly read changed analysis Action, without version or approval", () => {
    expect(changedActionTarget(oldAction, true)).toEqual(target);
    expect(changedActionTarget(oldAction, false)).toBeNull();
    for (const analysis_basis_status of ["current", "unavailable", undefined] as const) {
      expect(changedActionTarget({ ...oldAction, analysis_basis_status }, true)).toBeNull();
    }
    expect(changedActionTarget({ ...oldAction, payload: {} }, true)).toBeNull();
  });

  it("finds the original question by exact Run identity even if a newer question exists", () => {
    expect(originalActionQuestion(target, "case_a", result, true)).toBe("原待办明确绑定的问题");
    expect(originalActionQuestion(target, "case_a", { ...result, run: { ...result.run!, id: "old_run",
      analysis: { ...result.run!.analysis!, question: "原运行仍是当前运行" } } }, true)).toBe("原运行仍是当前运行");
  });

  it("never falls back to the latest question, Case goal or stale data after failed/cross-Case reads", () => {
    expect(originalActionQuestion(target, "case_b", result, true)).toBeNull();
    expect(originalActionQuestion(target, "case_a", result, false)).toBeNull();
    expect(originalActionQuestion(target, "case_a", null, true)).toBeNull();
    expect(originalActionQuestion(target, "case_a", { ...result, run_history: [] }, true)).toBeNull();
    expect(originalActionQuestion(target, "case_a", { ...result, run: { ...result.run!, case_id: "case_b" } }, true)).toBeNull();
    expect(originalActionQuestion(target, "case_a", { ...result, run_history: [{ ...result.run_history[0]!, question: null }] }, true)).toBeNull();
  });
});
