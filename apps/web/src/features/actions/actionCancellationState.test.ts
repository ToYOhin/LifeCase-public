import { describe, expect, it } from "vitest";

import { canCancelReviewedAction, cancellationStorageKey, restoreCancellationEdit } from "./actionCancellationState";
import type { ActionRecord } from "./types";

const oldAction = { id: "old_action", case_id: "case_a", kind: "prepare_communication", status: "running", version: 4,
  payload: { analysis_run_id: "portfolio-live-q1-old" }, payload_sha256: "a".repeat(64), analysis_basis_status: "changed" } as unknown as ActionRecord;

describe("explicit cancellation of a saved analysis Action", () => {
  it("allows an explicitly reviewed fresh old Action even when its analysis basis changed", () => {
    expect(canCancelReviewedAction(oldAction, { ...oldAction }, true, true)).toBe(true);
    const unavailable = { ...oldAction, analysis_basis_status: "unavailable" as const };
    expect(canCancelReviewedAction(unavailable, { ...unavailable }, true, true)).toBe(true);
  });

  it("requires a fresh read and explicit review, never just the navigation target or restored text", () => {
    expect(canCancelReviewedAction(oldAction, null, true, true)).toBe(false);
    expect(canCancelReviewedAction(oldAction, oldAction, false, true)).toBe(false);
    expect(canCancelReviewedAction(oldAction, oldAction, true, false)).toBe(false);
  });

  it("rejects a stale or different Case, Action, version, payload, state, or basis read", () => {
    for (const changed of [
      { case_id: "case_b" }, { id: "new_action" }, { version: 5 }, { payload_sha256: "b".repeat(64) },
      { status: "approved" as const }, { analysis_basis_status: "current" as const },
    ]) expect(canCancelReviewedAction(oldAction, { ...oldAction, ...changed }, true, true)).toBe(false);
    for (const status of ["done", "cancelled"] as const) {
      const terminal = { ...oldAction, status };
      expect(canCancelReviewedAction(terminal, terminal, true, true)).toBe(false);
    }
  });

  it("restores raw operator and reason by Case and Action without reviving authority or review", () => {
    const restored = restoreCancellationEdit(JSON.stringify({ actor: "处理人", reason: "新待办已保存，明确取消旧待办", reviewed: true, expected_version: 4, action_id: "other" }));
    expect(restored).toEqual({ actor: "处理人", reason: "新待办已保存，明确取消旧待办" });
    expect(restoreCancellationEdit("invalid")).toBeNull();
    expect(restoreCancellationEdit(JSON.stringify({ actor: "x".repeat(121), reason: "说明" }))).toBeNull();
    expect(cancellationStorageKey("case_a", "old_action")).not.toBe(cancellationStorageKey("case_b", "old_action"));
    expect(cancellationStorageKey("case_a", "old_action")).not.toBe(cancellationStorageKey("case_a", "new_action"));
  });
});
