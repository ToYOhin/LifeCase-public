import { describe, expect, it } from "vitest";

import { getActionProgress, saveActionProgress, readCompletionSource } from "./actionProgressApi";
import type { ActionRecord } from "./types";

export const progressAction = { id: "action_a", case_id: "case_a", kind: "prepare_communication", status: "running", risk_level: "B",
  payload: { analysis_run_id: "portfolio-live-q1-a" }, payload_sha256: "a".repeat(64), analysis_basis_status: "current",
  version: 4, completion_evidence: [], requirements: [], blockers: [], dependencies: [], deadline: null,
} as unknown as ActionRecord;
const record = { action: progressAction, entries: [] };
const json = (payload: unknown, status = 200) => new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });

describe("manual Action progress API", () => {
  it("loads the exact Case and Action with no-store and appends only a manual record", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const request: typeof fetch = async (url, init) => { calls.push({ url: String(url), init }); return json(record); };
    const read = await getActionProgress("case_a", "action_a", request);
    await saveActionProgress(read, { kind: "result", text: "商家已回复，仍待核实", actor: "操作者", reason: "记录原始回复" }, async (url, init) => {
      calls.push({ url: String(url), init });
      return json({ ...record, entries: [{ id: "progress_a", action_id: "action_a", case_id: "case_a", action_version: 4,
        kind: "result", text: "商家已回复，仍待核实", actor: "操作者", reason: "记录原始回复", created_at: "2026-09-21T00:00:00Z" }] }, 201);
    });
    expect(calls.map(c => c.url)).toEqual(["/api/v1/cases/case_a/actions/action_a/progress", "/api/v1/cases/case_a/actions/action_a/progress"]);
    expect(calls[0].init?.cache).toBe("no-store");
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ expected_version: 4, expected_last_progress_id: null,
      kind: "result", text: "商家已回复，仍待核实", actor: "操作者", reason: "记录原始回复" });
  });

  it("rejects foreign Case/Action entries and does not swallow conflicts", async () => {
    await expect(getActionProgress("case_b", "action_a", async () => json(record))).rejects.toMatchObject({ code: "action_progress_invalid_response" });
    await expect(getActionProgress("case_a", "action_a", async () => json({ ...record, entries: [{ id: "p", case_id: "case_b", action_id: "action_a" }] })))
      .rejects.toMatchObject({ code: "action_progress_invalid_response" });
    await expect(getActionProgress("case_a", "action_a", async () => json({ error: { code: "action_progress_conflict", message: "Reload", retryable: false } }, 409)))
      .rejects.toMatchObject({ code: "action_progress_conflict" });
  });

  it("reads selected same-Case source metadata and exact extracted text before completion", async () => {
    const source = { id: "source_a", case_id: "case_a", sha256: "b".repeat(64), original_name: "合成回复.txt", extraction: { status: "completed" } };
    const segments = [{ id: "segment_a", source_id: "source_a", text: "准备草案已核对", text_sha256: "c".repeat(64), sequence: 0, page: null }];
    const request: typeof fetch = async url => json(String(url).endsWith("source-segments") ? segments : source);
    expect((await readCompletionSource("case_a", { id: "source_a", sha256: "b".repeat(64) }, request)).segments).toEqual(segments);
    await expect(readCompletionSource("case_b", { id: "source_a", sha256: "b".repeat(64) }, request)).rejects.toMatchObject({ code: "action_completion_unavailable" });
    await expect(readCompletionSource("case_a", { id: "source_a", sha256: "d".repeat(64) }, request)).rejects.toMatchObject({ code: "action_completion_unavailable" });
    await expect(readCompletionSource("case_a", { id: "source_a", sha256: "b".repeat(64) }, async () => { throw new Error("offline"); })).rejects.toBeDefined();
  });
});
