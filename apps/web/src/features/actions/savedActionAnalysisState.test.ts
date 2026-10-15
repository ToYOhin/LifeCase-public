import { describe, expect, it, vi } from "vitest";
import { readSavedActionAnalysis } from "./savedActionAnalysisState";
import type { LiveAgentRunHistory } from "../agentDemo/types";
import type { ActionRecord } from "./types";

const action = { id: "action_a", case_id: "case_a", kind: "prepare_communication", version: 2, status: "ready",
  payload: { analysis_run_id: "run_original", analysis_basis_sha256: "b".repeat(64), analysis_answer_sha256: "c".repeat(64) },
  payload_sha256: "a".repeat(64), analysis_basis_status: "changed", risk_level: "B", blockers: [], blockers_sha256: "d".repeat(64),
  requirements: [], dependencies: [], deadline: null, communication_draft: null, completion_evidence: [], topological_position: 0,
  created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" } satisfies ActionRecord;
const original = { id: "run_original", question: "原问题", basis_status: "changed", version: 1,
  stage_order: ["validate"], status: "completed", provider: "disabled", model_id: null, answer_status: "answered", calls: 1,
  total_tokens: 10, created_at: "2026-09-24T00:00:00Z", completed_at: "2026-09-24T00:00:01Z",
  answer: { status: "answered", statements: ["原分析"], citations: [{ case_id: "case_a", source_id: "source_a", segment_id: "segment_a",
    sequence: 0, page: null, text_sha256: "b".repeat(64), quote_start: 0, quote_end: 1, quote_hash: "c".repeat(64) }],
    fact_references: [], uncertainty_reason: null } } satisfies LiveAgentRunHistory;
const result = { run: { id: "run_latest", case_id: "case_a" }, run_history: [
  { ...original, id: "run_latest", question: "其他问题" }, original,
] };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
function requestFor(live: unknown = result, fresh: ActionRecord = action) {
  return vi.fn<typeof fetch>(async url => String(url).endsWith("/progress") ? json({ action: fresh, entries: [] }) : json(live));
}

describe("saved Action source analysis", () => {
  it("reads the displayed same-Case binding and locates only its original history Run, using read-only uncached requests", async () => {
    const request = requestFor();
    const read = await readSavedActionAnalysis(action, request);
    expect(read.analysis).toEqual(original);
    expect(read.isLatest).toBe(false);
    expect(read.actionBasisStatus).toBe("changed");
    expect(request.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/cases/case_a/actions/action_a/progress", "/api/v1/cases/case_a/live-agent",
    ]);
    for (const [, init] of request.mock.calls) {
      expect(init?.method ?? "GET").toBe("GET"); expect(init?.cache).toBe("no-store");
    }
  });

  it("allows changed, unavailable and ended records to be read as history, without inheriting authorization", async () => {
    for (const basis of ["current", "changed", "unavailable"] as const) {
      const displayed = { ...action, status: "cancelled", analysis_basis_status: basis } as ActionRecord;
      const read = await readSavedActionAnalysis(displayed, requestFor(result, displayed));
      expect(read.actionBasisStatus).toBe(basis); expect(read.analysis.id).toBe(action.payload.analysis_run_id);
    }
  });

  it("distinguishes a newer Run from a material change and permits missing old answers without substituting the latest", async () => {
    const old = { ...original, basis_status: "current", answer: null };
    const read = await readSavedActionAnalysis(action, requestFor({ ...result, run_history: [old] }));
    expect(read.isLatest).toBe(false); expect(read.analysis.answer).toBeNull(); expect(read.analysis.basis_status).toBe("current");
  });

  it("rejects missing, ambiguous, wrong-Case or malformed history instead of falling back to the latest answer", async () => {
    for (const live of [null, { ...result, run: null }, { ...result, run: { ...result.run, case_id: "case_b" } },
      { ...result, run_history: [] }, { ...result, run_history: [original, original] },
      { ...result, run_history: [{ ...original, basis_status: "invented" }] },
      { ...result, run_history: [{ ...original, answer: { ...original.answer, statements: "bad" } }] },
      { ...result, run_history: [{ ...original, answer: { ...original.answer, citations: [{ case_id: "case_b" }] } }] },
      { ...result, run_history: [{ ...original, answer: { ...original.answer, fact_references: [{ citations: [{ case_id: "case_b" }] }] } }] },
    ]) await expect(readSavedActionAnalysis(action, requestFor(live))).rejects.toThrow();
  });

  it("rejects an Action version or binding change before loading any analysis", async () => {
    for (const fresh of [{ ...action, case_id: "case_b" }, { ...action, id: "action_b" }, { ...action, version: 3 },
      { ...action, payload_sha256: "d".repeat(64) }, { ...action, payload: { ...action.payload, analysis_run_id: "run_latest" } },
    ]) {
      const request = requestFor(result, fresh);
      await expect(readSavedActionAnalysis(action, request)).rejects.toThrow(); expect(request).toHaveBeenCalledTimes(1);
    }
  });

  it("propagates read errors and fetches fresh history on retry", async () => {
    const request = requestFor();
    request.mockImplementationOnce(async () => json({ action, entries: [] })).mockImplementationOnce(async () => json({}, 503));
    await expect(readSavedActionAnalysis(action, request)).rejects.toThrow();
    expect((await readSavedActionAnalysis(action, request)).analysis.id).toBe(original.id);
    expect(request).toHaveBeenCalledTimes(4);
  });
});
