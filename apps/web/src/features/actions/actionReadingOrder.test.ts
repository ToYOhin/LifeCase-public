import { describe, expect, it } from "vitest";

import { actionReadingGroups } from "./presenters";
import type { ActionRecord } from "./types";

function action(id: string, patch: Partial<ActionRecord> = {}): ActionRecord {
  return {
    id, case_id: "synthetic-case", kind: "prepare_communication", status: "ready",
    risk_level: "B", payload: { analysis_run_id: "synthetic-run" },
    payload_sha256: "a".repeat(64), blockers: [], blockers_sha256: "b".repeat(64),
    requirements: [], dependencies: [], deadline: null, completion_evidence: [],
    communication_draft: "合成待办正文", analysis_basis_status: "current",
    topological_position: 0, version: 3, created_at: "2026-09-21T00:00:00Z",
    updated_at: "2026-09-21T00:00:00Z", ...patch,
  };
}

describe("Action reading order", () => {
  it("puts current work before changed/unverifiable work and ended history without losing records", () => {
    const items = [
      action("cancelled", { status: "cancelled", analysis_basis_status: "changed" }),
      action("changed", { analysis_basis_status: "changed" }),
      action("unavailable", { analysis_basis_status: "unavailable" }),
      action("ready"),
      action("done", { status: "done", analysis_basis_status: "unavailable" }),
    ];
    expect(actionReadingGroups(items).map(group => [group.id, group.items.map(item => item.id)]))
      .toEqual([
        ["current", ["ready"]], ["changed", ["changed"]],
        ["unavailable", ["unavailable"]], ["ended", ["cancelled", "done"]],
      ]);
  });

  it("keeps failed, blocked, proposed, approved and running work open with its existing authority", () => {
    const items = (["failed", "blocked", "proposed", "approved", "running"] as const)
      .map(status => action(status, { status, blockers: [{ code: "needs_review", field: null, authority_id: null }] }));
    const original = structuredClone(items);
    const groups = actionReadingGroups(items);
    expect(groups.map(group => group.id)).toEqual(["current"]);
    expect(groups[0].items).toEqual(items);
    expect(groups[0].items[0]).toBe(items[0]);
    expect(items).toEqual(original);
  });

  it("does not classify missing analysis authority as current, while legacy unbound actions remain readable", () => {
    const groups = actionReadingGroups([
      action("missing", { analysis_basis_status: undefined }),
      action("null", { analysis_basis_status: null }),
      action("legacy", { payload: { purpose: "合成本地跟进" }, analysis_basis_status: null }),
    ]);
    expect(groups.map(group => [group.id, group.items.map(item => item.id)]))
      .toEqual([["current", ["legacy"]], ["unavailable", ["missing", "null"]]]);
  });

  it("preserves the API dependency order inside each section and omits empty sections", () => {
    const items = [
      action("old", { status: "done", topological_position: 0 }),
      action("prerequisite", { topological_position: 1 }),
      action("changed", { topological_position: 2, analysis_basis_status: "changed" }),
      action("dependent", { topological_position: 3, dependencies: [{ action_id: "prerequisite", action_version: 3, status: "ready" }] }),
    ];
    const groups = actionReadingGroups(items);
    expect(groups.flatMap(group => group.items.map(item => item.id)))
      .toEqual(["prerequisite", "dependent", "changed", "old"]);
    expect(groups[0].items.map(item => item.topological_position)).toEqual([1, 3]);
    expect(items.map(item => item.id)).toEqual(["old", "prerequisite", "changed", "dependent"]);
    expect(actionReadingGroups([])).toEqual([]);
  });
});
