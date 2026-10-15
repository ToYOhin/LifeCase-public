import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { DesiredOutcomeIntake } from "./DesiredOutcomeIntake";
import { desiredOutcomeFitsClaim, registerDesiredOutcome } from "./desiredOutcomeApi";
import type { CurrentFacts } from "./types";

const claim = { id: "claim_synthetic", case_id: "case_a", field: "desired_outcome", value: "请先说明检测流程",
  version: 1, status: "candidate" as const, created_by: "user" as const, confidence: null, contradiction: false,
  created_at: "2026-09-30T00:00:00Z", updated_at: "2026-09-30T00:00:00Z" };
const facts: CurrentFacts = { case_id: "case_a", case_type: "warranty", items: [] };
const saved = { case_id: "case_a", intent: claim.value, version: 1, updated_at: claim.created_at };

describe("personal desired outcome intake", () => {
  it("keeps preparation text intact when it exceeds the existing fact size boundary", () => {
    const longSaved = { ...saved, intent: "诉".repeat(2000) };
    expect(desiredOutcomeFitsClaim(longSaved.intent)).toBe(false);
    expect(desiredOutcomeFitsClaim("请先说明检测流程")).toBe(true);
    const markup = renderToStaticMarkup(<DesiredOutcomeIntake caseId="case_a" facts={facts} saved={longSaved} ready onRead={async () => facts} />);
    expect(markup).toContain("请缩短本人诉求后重新保存");
    expect(longSaved.intent).toHaveLength(2000);
  });
  it("sends only the saved version and explicit registration metadata, never a replacement value or confirmation", async () => {
    const request = vi.fn<typeof fetch>().mockResolvedValue(Response.json(claim, { status: 201 }));
    const signal = new AbortController().signal;
    expect(await registerDesiredOutcome("case_a", 1, "合成登记人", "从本人已保存诉求登记", signal, request)).toEqual(claim);
    expect(request).toHaveBeenCalledTimes(1);
    const [url, init] = request.mock.calls[0];
    expect(url).toBe("/api/v1/cases/case_a/desired-outcome");
    expect(init?.signal).toBe(signal);
    expect(JSON.parse(init?.body as string)).toEqual({ expected_intent_version: 1, actor: "合成登记人", reason: "从本人已保存诉求登记" });
    expect(init?.method).toBe("POST");
  });

  it.each([{ case_id: "case_b" }, { field: "purchase_date" }, { status: "confirmed" }, { created_by: "agent" }, { version: 2 }, { id: "" }])(
    "requires a read after a mismatched response %j, without resending", async changes => {
      const request = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ...claim, ...changes }));
      await expect(registerDesiredOutcome("case_a", 1, "人", "理由", new AbortController().signal, request))
        .rejects.toMatchObject({ code: "unexpected_response", retryable: true });
      expect(request).toHaveBeenCalledTimes(1);
    });

  it.each(["lost", "broken", "server"]) ("does not repeat an uncertain %s write", async kind => {
    const request = vi.fn<typeof fetch>();
    if (kind === "lost") request.mockRejectedValue(new Error("lost"));
    else request.mockResolvedValue(kind === "broken" ? new Response("broken") : Response.json({ error: { code: "storage", message: "失败" } }, { status: 503 }));
    await expect(registerDesiredOutcome("case_a", 1, "人", "理由", new AbortController().signal, request)).rejects.toMatchObject({ retryable: true });
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("preserves the conflict instead of treating an existing claim as created", async () => {
    const request = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: { code: "desired_outcome_conflict", message: "已有期望结果" } }, { status: 409 }));
    await expect(registerDesiredOutcome("case_a", 1, "人", "理由", new AbortController().signal, request)).rejects.toMatchObject({ code: "desired_outcome_conflict", retryable: false });
  });

  it("makes registration and later confirmation explicit and starts without runnable metadata", () => {
    const markup = renderToStaticMarkup(<DesiredOutcomeIntake caseId="case_a" facts={facts} saved={saved} ready onRead={async () => facts} />);
    expect(markup).toContain("本人的诉求无需材料原文");
    expect(markup).toContain("再单独确认");
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>将已保存诉求登记为待核实期望结果<\/button>/);
  });

  it.each(["candidate", "inferred", "confirmed", "corrected"] as const)("provides a route to the existing %s value, never a replacement form", status => {
    const current: CurrentFacts = { ...facts, items: [{ claim: { ...claim, status }, confirmation_basis: null, confirmation: null,
      citations: [], evidence_status: "none", conflict: false, usable_as_fact: false }] };
    const markup = renderToStaticMarkup(<DesiredOutcomeIntake caseId="case_a" facts={current} saved={saved} ready onRead={async () => current} onResolve={() => undefined} />);
    expect(markup).toContain(["confirmed", "corrected"].includes(status) ? "查看当前期望结果" : "去核实期望结果");
    expect(markup).not.toContain("登记人");
    expect(markup).not.toContain("将已保存诉求登记为待核实期望结果");
  });
});
