import { describe, expect, it } from "vitest";
import { readCaseAfterTransition, transitionCase } from "./api";
import type { CaseRecord, CaseTransitionInput, CaseTransitionRecord } from "./types";

const command: CaseTransitionInput = { expected_version: 3, target_status: "resolved", actor: "local_user", reason: "合成处理结果已核对" };
const transition: CaseTransitionRecord = { id: "transition_a", case_id: "case_a", prior_status: "in_progress", new_status: "resolved",
  prior_version: 3, new_version: 4, actor: command.actor, reason: command.reason, transitioned_at: "2026-09-24T00:00:00Z" };
const record: CaseRecord = { id: "case_a", type: "warranty", title: "合成案件", goal: "处理结果", template_id: "warranty_claim", template_version: 1,
  status: "resolved", version: 4, priority: 0, created_at: transition.transitioned_at, updated_at: transition.transitioned_at,
  closed_at: transition.transitioned_at, sources: [] };

describe("manual Case state recovery", () => {
  it("accepts only the transition acknowledgement for the exact Case, version, target and actor/reason", async () => {
    expect(await transitionCase("case_a", command, async () => new Response(JSON.stringify(transition)))).toEqual(transition);
    for (const response of [{ ...transition, case_id: "case_b" }, { ...transition, prior_version: 2 }, { ...transition, new_version: 6 },
      { ...transition, new_status: "archived" }, { ...transition, actor: "other" }, { ...transition, reason: "other reason" }]) {
      await expect(transitionCase("case_a", command, async () => new Response(JSON.stringify(response)))).rejects.toThrow();
    }
  });

  it("recovers authoritative state using GET only and without cache, including a later version", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const request: typeof fetch = async (url, init) => { calls.push([String(url), init]); return new Response(JSON.stringify(record)); };
    expect(await readCaseAfterTransition("case_a", transition, request)).toEqual(record);
    expect(calls).toEqual([["/api/v1/cases/case_a", { cache: "no-store" }]]);
    const later = { ...record, version: 5, status: "archived" };
    expect(await readCaseAfterTransition("case_a", transition, async () => new Response(JSON.stringify(later)))).toEqual(later);
  });

  it("refuses a foreign Case, a stale read or a conflicting acknowledgement instead of adopting it", async () => {
    for (const response of [{ ...record, id: "case_b" }, { ...record, version: 3 }, { ...record, status: "in_progress" }]) {
      await expect(readCaseAfterTransition("case_a", transition, async () => new Response(JSON.stringify(response)))).rejects.toThrow();
    }
    await expect(readCaseAfterTransition("case_a", { ...transition, case_id: "case_b" }, async () => new Response(JSON.stringify(record)))).rejects.toThrow();
    await expect(readCaseAfterTransition("case_a", null, async () => new Response("{}", { status: 503 }))).rejects.toThrow();
  });
});
