import { describe, expect, it } from "vitest";

import { getPreparationIntent, savePreparationIntent } from "./intentApi";

const saved = { case_id: "case_a", intent: "请先报价。\n保留中文与 🧺", version: 2, updated_at: "2026-09-19T00:00:00Z" };

describe("按案件保存报修诉求", () => {
  it("reads saved intent afresh and forwards the request cancellation", async () => {
    const signal = new AbortController().signal;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const result = await getPreparationIntent("case_a", signal, async (input, init) => {
      calls.push({ url: String(input), init });
      return new Response(JSON.stringify(saved));
    });
    expect(result).toEqual(saved);
    expect(calls).toEqual([{ url: "/api/v1/cases/case_a/preparation-intent", init: { signal, cache: "no-store" } }]);
  });

  it.each([saved.intent, ""])("saves only the current text with its independent version: %s", async (intent) => {
    const signal = new AbortController().signal;
    let body: unknown;
    const result = await savePreparationIntent("case_a", intent, 1, signal, async (input, init) => {
      expect(String(input)).toBe("/api/v1/cases/case_a/preparation-intent");
      expect(init?.method).toBe("PUT");
      expect(init?.signal).toBe(signal);
      body = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({ ...saved, intent }));
    });
    expect(body).toEqual({ intent, expected_version: 1 });
    expect(result.intent).toBe(intent);
  });

  it("never accepts a saved result belonging to another Case", async () => {
    await expect(getPreparationIntent("case_a", new AbortController().signal, async () => new Response(JSON.stringify({ ...saved, case_id: "case_b" }))))
      .rejects.toMatchObject({ code: "preparation_intent_invalid_response" });
  });

  it.each([
    { ...saved, version: -1 },
    { ...saved, intent: null },
    { ...saved, version: 0, updated_at: null },
    { ...saved, updated_at: null },
  ])("rejects invalid saved metadata", async (payload) => {
    await expect(getPreparationIntent("case_a", new AbortController().signal, async () => new Response(JSON.stringify(payload))))
      .rejects.toMatchObject({ code: "preparation_intent_invalid_response" });
  });

  it("recognizes an empty, never-saved Case without inventing a saved timestamp", async () => {
    const blank = { case_id: "case_a", intent: "", version: 0, updated_at: null };
    expect(await getPreparationIntent("case_a", new AbortController().signal, async () => new Response(JSON.stringify(blank)))).toEqual(blank);
  });

  it("preserves a real version conflict as a conflict requiring a new read", async () => {
    await expect(savePreparationIntent("case_a", "本窗口输入", 1, new AbortController().signal, async () => new Response(JSON.stringify({ error: { code: "preparation_intent_version_conflict" } }), { status: 409 })))
      .rejects.toMatchObject({ code: "preparation_intent_version_conflict" });
  });

  it("does not call an unreadable successful PUT a confirmed save", async () => {
    await expect(savePreparationIntent("case_a", "保留输入", 1, new AbortController().signal, async () => new Response("not-json")))
      .rejects.toMatchObject({ code: "preparation_intent_invalid_response" });
  });

  it.each([1, 3])("does not acknowledge a save with an unexpected returned version %s", async (version) => {
    await expect(savePreparationIntent("case_a", saved.intent, 1, new AbortController().signal, async () => new Response(JSON.stringify({ ...saved, version }))))
      .rejects.toMatchObject({ code: "preparation_intent_invalid_response" });
  });

  it("distinguishes a transport failure without retrying a possibly completed write", async () => {
    let calls = 0;
    await expect(savePreparationIntent("case_a", "保留输入", 1, new AbortController().signal, async () => { calls++; throw new TypeError("offline"); }))
      .rejects.toMatchObject({ code: "network_error" });
    expect(calls).toBe(1);
  });
});
