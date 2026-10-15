import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ActionProgress } from "./ActionProgress";
import type { ActionRecord } from "./types";

const action: ActionRecord = {
  id: "synthetic-action", case_id: "synthetic-case", kind: "prepare_communication", status: "ready",
  risk_level: "B", payload: { analysis_run_id: "synthetic-run" }, payload_sha256: "a".repeat(64),
  blockers: [], blockers_sha256: "b".repeat(64), requirements: [], dependencies: [], deadline: null,
  completion_evidence: [], communication_draft: "合成待办正文", analysis_basis_status: "current",
  topological_position: 0, version: 1, created_at: "2026-09-21T00:00:00Z", updated_at: "2026-09-21T00:00:00Z",
};

describe("Progress disclosure", () => {
  it("starts the editor folded while keeping its fields mounted", () => {
    const markup = renderToStaticMarkup(<ActionProgress item={action} available sources={[]} onChanged={() => undefined} />);
    expect(markup).toMatch(/<details class="action-progress-details">/);
    expect(markup).toMatch(/<details class="action-progress-details">[\s\S]*处理记录正文[\s\S]*处理操作人[\s\S]*处理理由[\s\S]*<\/details>/);
    expect(markup).not.toMatch(/<details[^>]*open/);
  });

  it("keeps stale evidence warnings outside the folded editor", () => {
    const markup = renderToStaticMarkup(<ActionProgress item={{ ...action, analysis_basis_status: "changed" }} available sources={[]} onChanged={() => undefined} />);
    expect(markup.slice(0, markup.indexOf('<details class="action-progress-details"'))).toContain("依据已变化或无法核对");
  });
});
