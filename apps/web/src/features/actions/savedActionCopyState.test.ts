import { describe, expect, it, vi } from "vitest";
import { copySavedActionDraft } from "./savedActionCopyState";
import type { ActionRecord } from "./types";

const text = "  您好，请协助安排报修。\n订单：SYN-COPY-001\n诉求：检查后告知维修安排。\n";
const action = {
  id: "action_a", case_id: "case_a", kind: "prepare_communication", status: "ready", risk_level: "B", version: 4,
  payload: { analysis_run_id: "run_a", analysis_basis_sha256: "b".repeat(64), analysis_answer_sha256: "c".repeat(64), draft_text: text },
  payload_sha256: "a".repeat(64), communication_draft: text, analysis_basis_status: "current",
  blockers: [], blockers_sha256: "d".repeat(64), requirements: [], dependencies: [], deadline: null,
  completion_evidence: [], topological_position: 0, created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
} satisfies ActionRecord;
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const read = (fresh: ActionRecord): typeof fetch => async () => json({ action: fresh, entries: [] });

describe("copy of a saved repair draft", () => {
  it("re-reads the exact same-Case Action without cache and copies only the exact displayed saved text", async () => {
    const write = vi.fn(async (_text: string) => undefined);
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const request: typeof fetch = async (url, init) => { calls.push({ url: String(url), init }); return json({ action, entries: [] }); };
    expect(await copySavedActionDraft(action, () => true, write, request)).toBe("copied");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/v1/cases/case_a/actions/action_a/progress");
    expect(calls[0].init?.cache).toBe("no-store");
    expect(calls[0].init?.method ?? "GET").toBe("GET");
    expect(write).toHaveBeenCalledExactlyOnceWith(text);
  });

  it("never substitutes a new version, body or analysis binding for the displayed saved draft", async () => {
    const variants = [
      { ...action, version: 5 }, { ...action, status: "approved" },
      { ...action, payload_sha256: "d".repeat(64) },
      { ...action, communication_draft: "另一份正文", payload: { ...action.payload, draft_text: "另一份正文" } },
      ...["analysis_run_id", "analysis_basis_sha256", "analysis_answer_sha256"].map(key => ({ ...action, payload: { ...action.payload, [key]: "different" } })),
      { ...action, communication_draft: null }, { ...action, communication_draft: "   " },
      { ...action, payload: { ...action.payload, draft_text: "与展示正文不一致" } },
    ] as ActionRecord[];
    for (const fresh of variants) {
      const write = vi.fn(async () => undefined);
      expect(await copySavedActionDraft(action, () => true, write, read(fresh))).toBe("changed");
      expect(write).not.toHaveBeenCalled();
    }
  });

  it("blocks changed/unavailable evidence even when the saved Action version is unchanged", async () => {
    for (const analysis_basis_status of ["changed", "unavailable"] as const) {
      const write = vi.fn(async () => undefined);
      expect(await copySavedActionDraft(action, () => true, write, read({ ...action, analysis_basis_status }))).toBe("basis-unavailable");
      expect(write).not.toHaveBeenCalled();
    }
  });

  it("refuses foreign Case/Action, invalid authority, offline and HTTP failures without fallback", async () => {
    const requests: Array<typeof fetch> = [
      read({ ...action, case_id: "case_b" }), read({ ...action, id: "action_b" }), read({ ...action, version: 0 }),
      read({ ...action, analysis_basis_status: undefined }),
      async () => { throw new Error("offline"); },
      async () => json({ error: { code: "synthetic", message: "Read failed" } }, 503),
      async () => new Response("invalid JSON"),
    ];
    for (const request of requests) {
      const write = vi.fn(async () => undefined);
      expect(await copySavedActionDraft(action, () => true, write, request)).toBe("read-failed");
      expect(write).not.toHaveBeenCalled();
    }
  });

  it("does not copy an ineligible displayed record or start a read after leaving its context", async () => {
    const request = vi.fn(read(action)); const write = vi.fn(async () => undefined);
    for (const displayed of [
      { ...action, kind: "follow_up" }, { ...action, analysis_basis_status: "changed" },
      { ...action, payload: {} }, { ...action, communication_draft: "" },
    ] as ActionRecord[]) expect(await copySavedActionDraft(displayed, () => true, write, request)).toBe("basis-unavailable");
    expect(await copySavedActionDraft(action, () => false, write, request)).toBe("superseded");
    expect(request).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled();
  });

  it("ignores a late read after a Case change, refresh or unmount before touching the clipboard", async () => {
    let valid = true; let release!: (response: Response) => void;
    const request: typeof fetch = () => new Promise(resolve => { release = resolve; });
    const write = vi.fn(async () => undefined);
    const copying = copySavedActionDraft(action, () => valid, write, request);
    valid = false; release(json({ action, entries: [] }));
    expect(await copying).toBe("superseded"); expect(write).not.toHaveBeenCalled();
  });

  it("reports clipboard refusal and requires another fresh read on retry", async () => {
    const request = vi.fn(read(action)); const write = vi.fn(async () => { throw new Error("denied"); });
    expect(await copySavedActionDraft(action, () => true, write, request)).toBe("clipboard-failed");
    const retry = vi.fn(async () => undefined);
    expect(await copySavedActionDraft(action, () => true, retry, request)).toBe("copied");
    expect(request).toHaveBeenCalledTimes(2); expect(retry).toHaveBeenCalledExactlyOnceWith(text);
  });

  it("never publishes late success to a different context after the clipboard operation", async () => {
    let valid = true;
    expect(await copySavedActionDraft(action, () => valid, async () => { valid = false; }, read(action))).toBe("superseded");
  });

  it("can copy an ended record only when its saved version and analysis are still current", async () => {
    for (const status of ["done", "cancelled"] as const) {
      const ended = { ...action, status }; const write = vi.fn(async () => undefined);
      expect(await copySavedActionDraft(ended, () => true, write, read(ended))).toBe("copied");
      expect(write).toHaveBeenCalledExactlyOnceWith(text);
    }
  });
});
