import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SavedActionCopy } from "./SavedActionCopy";
import type { ActionRecord } from "./types";

const item: ActionRecord = { id: "action_a", case_id: "case_a", kind: "prepare_communication", version: 1, status: "ready", risk_level: "B",
  payload: { analysis_run_id: "run_a", draft_text: "已保存合成正文" }, communication_draft: "已保存合成正文",
  analysis_basis_status: "current", payload_sha256: "a".repeat(64), blockers: [], blockers_sha256: "b".repeat(64),
  requirements: [], dependencies: [], deadline: null, completion_evidence: [], topological_position: 0,
  created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" };

describe("saved draft copy entry", () => {
  it("keeps a compact explicit button and blocks unavailable or stale cards", () => {
    const markup = (record: ActionRecord, available: boolean) => renderToStaticMarkup(<SavedActionCopy item={record} available={available} onReload={() => undefined} />);
    expect(markup(item, true)).toContain("复制已保存正文");
    expect(markup(item, true)).not.toContain('disabled=""');
    expect(markup(item, true)).not.toContain("正文已复制");
    expect(markup(item, false)).toContain('disabled=""');
    for (const analysis_basis_status of ["changed", "unavailable", undefined] as const) {
      expect(markup({ ...item, analysis_basis_status }, true)).toContain('disabled=""');
    }
  });
  it("does not advertise saved repair copy for a derived legacy draft or missing saved body", () => {
    expect(renderToStaticMarkup(<SavedActionCopy item={{ ...item, payload: {} }} available onReload={() => undefined} />)).toBe("");
    expect(renderToStaticMarkup(<SavedActionCopy item={{ ...item, communication_draft: null }} available onReload={() => undefined} />)).toBe("");
  });
});
