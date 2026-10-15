import { describe, expect, it } from "vitest";
import { canRecordProgress, canCompletePreparedAction, actionProgressStorageKey, restoreProgressEdit, matchesProgressRead } from "./actionProgressState";
import type { ActionRecord } from "./types";

const action = { id: "action_a", case_id: "case_a", kind: "prepare_communication", status: "running", version: 4,
  payload: { analysis_run_id: "portfolio-live-q1-a" }, payload_sha256: "a".repeat(64), analysis_basis_status: "current" } as unknown as ActionRecord;

describe("manual progress authority and retained edits", () => {
  it("does not confuse recording a result with approval or completion", () => {
    for (const status of ["blocked", "ready", "approved", "running", "failed"] as const) expect(canRecordProgress({ ...action, status }, true)).toBe(true);
    for (const status of ["done", "cancelled"] as const) expect(canRecordProgress({ ...action, status }, true)).toBe(false);
    for (const analysis_basis_status of ["changed", "unavailable", undefined] as const) expect(canRecordProgress({ ...action, analysis_basis_status }, true)).toBe(false);
    expect(canRecordProgress(action, false)).toBe(false);
  });
  it("requires running current Action and freshly read explicitly checked completion evidence", () => {
    const evidence = { caseId: "case_a", actionId: "action_a", actionVersion: 4, sourceId: "source_a", sourceSha256: "b".repeat(64) };
    expect(canCompletePreparedAction(action, true, evidence, "source_a", "b".repeat(64), true)).toBe(true);
    for (const status of ["ready", "approved", "done"] as const) expect(canCompletePreparedAction({ ...action, status }, true, evidence, "source_a", "b".repeat(64), true)).toBe(false);
    expect(canCompletePreparedAction(action, true, evidence, "source_b", "b".repeat(64), true)).toBe(false);
    expect(canCompletePreparedAction(action, true, evidence, "source_a", "c".repeat(64), true)).toBe(false);
    expect(canCompletePreparedAction({ ...action, version: 5 }, true, evidence, "source_a", "b".repeat(64), true)).toBe(false);
    expect(canCompletePreparedAction(action, false, evidence, "source_a", "b".repeat(64), true)).toBe(false);
    expect(canCompletePreparedAction(action, true, evidence, "source_a", "b".repeat(64), false)).toBe(false);
  });
  it("does not finish while a typed manual record would become unsavable in the terminal Action", () => {
    const evidence = { caseId: "case_a", actionId: "action_a", actionVersion: 4, sourceId: "source_a", sourceSha256: "b".repeat(64) };
    expect(canCompletePreparedAction(action, true, evidence, "source_a", "b".repeat(64), true, "已收到回复，尚未显式保存这条记录")).toBe(false);
    expect(canCompletePreparedAction(action, true, evidence, "source_a", "b".repeat(64), true, "")).toBe(true);
    expect(canCompletePreparedAction(action, true, evidence, "source_a", "b".repeat(64), true, "  \n")).toBe(true);
    // Saving or discarding the text cannot reuse the prior evidence-review checkbox.
    expect(canCompletePreparedAction(action, true, evidence, "source_a", "b".repeat(64), false, "")).toBe(false);
  });
  it("restores only raw editor fields and never revives a saved version or review checkbox", () => {
    expect(restoreProgressEdit(JSON.stringify({ kind: "result", text: "未保存结果", actor: "操作者", reason: "核对中", reviewed: true, expected_version: 4 })))
      .toEqual({ kind: "result", text: "未保存结果", actor: "操作者", reason: "核对中" });
    expect(restoreProgressEdit("invalid")).toBeNull();
    expect(actionProgressStorageKey("case_a", "action_b")).not.toBe(actionProgressStorageKey("case_b", "action_a"));
  });
  it("rejects late or different Case/Action/version reads before granting write authority", () => {
    expect(matchesProgressRead(action, { action, entries: [] })).toBe(true);
    expect(matchesProgressRead(action, { action: { ...action, case_id: "case_b" }, entries: [] })).toBe(false);
    expect(matchesProgressRead(action, { action: { ...action, id: "action_b" }, entries: [] })).toBe(false);
    expect(matchesProgressRead(action, { action: { ...action, version: 5 }, entries: [] })).toBe(false);
  });
});
