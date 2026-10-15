import { describe, expect, it } from "vitest";

import type { CaseRecord, SourceRecord } from "../intake/types";
import type { CurrentFacts } from "./types";
import { getCurrentFactsSnapshot, getPreparationSnapshot } from "./api";

const source: SourceRecord = {
  id: "source_a", case_id: "case_a", original_name: "合成收据.txt", media_type: "text/plain",
  sha256: "a".repeat(64), size_bytes: 30, trust_label: "untrusted", imported_at: "2026-09-18",
  extraction: { status: "completed", extractor_version: null, segment_count: 1, notices: [], error_code: null },
};
const record: CaseRecord = {
  id: "case_a", type: "warranty", template_id: "warranty_claim", template_version: 1,
  title: "合成家电", goal: "不要自动放入诉求", status: "intake", priority: 0, version: 1,
  created_at: "2026-09-18", updated_at: "2026-09-18", closed_at: null, sources: [source],
};
const facts: CurrentFacts = { case_id: "case_a", case_type: "warranty", items: [] };

function requestFor(projection: CurrentFacts, currentCase: CaseRecord): typeof fetch {
  return async (input) => new Response(JSON.stringify(String(input).endsWith("/current-facts") ? projection : currentCase));
}

describe("报修准备单只读 API", () => {
  it("uses only two fresh Case-scoped GETs and forwards cancellation", async () => {
    const signal = new AbortController().signal;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const result = await getPreparationSnapshot("case_a", signal, async (input, init) => {
      calls.push({ url: String(input), init });
      return requestFor(facts, record)(input, init);
    });
    expect(result.facts).toEqual(facts);
    expect(result.sources).toEqual([source]);
    expect(calls.map(({ url }) => url)).toEqual(["/api/v1/cases/case_a/current-facts", "/api/v1/cases/case_a"]);
    expect(calls.every(({ init }) => init?.signal === signal && init.cache === "no-store" && !init.method)).toBe(true);
  });

  it.each([
    { ...record, id: "case_b" },
    { ...record, type: "refund" },
    { ...record, sources: [{ ...source, case_id: "case_b" }] },
  ])("rejects wrong Case or source inventories before rendering", async (currentCase) => {
    await expect(getPreparationSnapshot("case_a", new AbortController().signal, requestFor(facts, currentCase)))
      .rejects.toMatchObject({ code: "current_facts_integrity_failed" });
  });

  it("rejects another Case's projection", async () => {
    await expect(getPreparationSnapshot("case_a", new AbortController().signal, requestFor({ ...facts, case_id: "case_b" }, record)))
      .rejects.toMatchObject({ code: "current_facts_integrity_failed" });
  });

  it("fails closed when the source text or quote hash differs", async () => {
    const projection: CurrentFacts = { ...facts, items: [{
      claim: { id: "claim_a", case_id: "case_a", field: "product_name", value: "海星 H8", status: "confirmed", confidence: null, created_by: "user", contradiction: false, version: 2, created_at: "2026-09-18", updated_at: "2026-09-18" },
      confirmation_basis: "evidence", confirmation: { actor: "tester", reason: "核对收据", occurred_at: "2026-09-18" },
      evidence_status: "valid", conflict: false, usable_as_fact: true,
      citations: [{ claim_id: "claim_a", case_id: "case_a", source_id: "source_a", source_name: source.original_name, segment_id: "segment_a", sequence: 0, page: null, text: "海星 H8", quote_text: "海星 H8", text_sha256: "0".repeat(64), quote_start: 0, quote_end: 5, quote_hash: "0".repeat(64), relation: "supports", attached_by: "tester", attached_at: "2026-09-18" }],
    }] };
    await expect(getPreparationSnapshot("case_a", new AbortController().signal, requestFor(projection, record)))
      .rejects.toMatchObject({ code: "current_facts_integrity_failed" });
  });
});

const caseBindings = {
  warranty: { templateId: "warranty_claim", field: "merchant_or_manufacturer" },
  return_refund: { templateId: "return_refund_claim", field: "merchant" },
  reimbursement: { templateId: "expense_reimbursement", field: "merchant" },
  subscription_cancellation: { templateId: "subscription_cancellation_claim", field: "merchant" },
} as const;
type ReviewedCaseType = keyof typeof caseBindings;
const caseTypes = Object.keys(caseBindings) as ReviewedCaseType[];

async function hashText(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

async function citedSnapshot(caseType: ReviewedCaseType = "warranty") {
  const prefix = "合成材料 🧾：";
  const value = "合成商家";
  const text = `${prefix}${value}；仅供离线回归。`;
  const textHash = await hashText(text);
  const currentSource: SourceRecord = { ...source, sha256: textHash, size_bytes: new TextEncoder().encode(text).byteLength };
  const currentCase: CaseRecord = {
    ...record, type: caseType, template_id: caseBindings[caseType].templateId, sources: [currentSource],
  };
  const start = Array.from(prefix).length;
  const projection: CurrentFacts = { case_id: record.id, case_type: caseType, items: [{
    claim: { id: "claim_a", case_id: record.id, field: caseBindings[caseType].field, value, status: "confirmed", confidence: null, created_by: "user", contradiction: false, version: 2, created_at: "2026-09-18", updated_at: "2026-09-18" },
    confirmation_basis: "evidence", confirmation: { actor: "tester", reason: "核对合成收据", occurred_at: "2026-09-18" },
    evidence_status: "valid", conflict: false, usable_as_fact: true,
    citations: [{ claim_id: "claim_a", case_id: record.id, source_id: currentSource.id, source_name: currentSource.original_name, segment_id: "segment_a", sequence: 0, page: null, text, quote_text: value, text_sha256: textHash, quote_start: start, quote_end: start + Array.from(value).length, quote_hash: await hashText(value), relation: "supports", attached_by: "tester", attached_at: "2026-09-18" }],
  }] };
  return { projection, currentCase };
}

describe("四类案件已核实事实只读 API", () => {
  it.each(caseTypes)("reads a cited %s fact with only fresh scoped GETs", async caseType => {
    const { projection, currentCase } = await citedSnapshot(caseType);
    const signal = new AbortController().signal;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const result = await getCurrentFactsSnapshot(record.id, caseType, signal, async (input, init) => {
      calls.push({ url: String(input), init });
      return requestFor(projection, currentCase)(input, init);
    });

    expect(result.facts).toEqual(projection);
    expect(result.facts.items[0].claim.value).toBe("合成商家");
    expect(result.facts.items[0].citations[0].quote_text).toBe("合成商家");
    expect(result.sources).toEqual(currentCase.sources);
    expect(calls.map(({ url }) => url)).toEqual(["/api/v1/cases/case_a/current-facts", "/api/v1/cases/case_a"]);
    expect(calls.every(({ init }) => init?.signal === signal && init.cache === "no-store" && (init.method ?? "GET") === "GET")).toBe(true);
  });

  it("rejects a matching response pair for a different expected Case type", async () => {
    const { projection, currentCase } = await citedSnapshot();
    await expect(getCurrentFactsSnapshot(record.id, "return_refund", new AbortController().signal, requestFor(projection, currentCase)))
      .rejects.toMatchObject({ code: "current_facts_integrity_failed" });
  });

  const corruptions: Array<[string, (projection: CurrentFacts, currentCase: CaseRecord) => void]> = [
    ["Case type", (_, currentCase) => { currentCase.type = "reimbursement"; }],
    ["projection type", projection => { projection.case_type = "reimbursement"; }],
    ["Case ID", (_, currentCase) => { currentCase.id = "case_b"; }],
    ["projection Case ID", projection => { projection.case_id = "case_b"; }],
    ["Claim Case ID", projection => { projection.items[0].claim.case_id = "case_b"; }],
    ["Source Case ID", (_, currentCase) => { currentCase.sources[0].case_id = "case_b"; }],
    ["citation Case ID", projection => { projection.items[0].citations[0].case_id = "case_b"; }],
    ["citation Claim ID", projection => { projection.items[0].citations[0].claim_id = "claim_b"; }],
    ["citation Source ID", projection => { projection.items[0].citations[0].source_id = "source_b"; }],
    ["citation Source name", projection => { projection.items[0].citations[0].source_name = "其他案件收据.txt"; }],
    ["source text hash", projection => { projection.items[0].citations[0].text_sha256 = "0".repeat(64); }],
    ["quote hash", projection => { projection.items[0].citations[0].quote_hash = "0".repeat(64); }],
  ];
  it.each(corruptions)("fails closed for mismatched %s", async (_, corrupt) => {
    const { projection, currentCase } = await citedSnapshot();
    corrupt(projection, currentCase);
    await expect(getCurrentFactsSnapshot(record.id, "warranty", new AbortController().signal, requestFor(projection, currentCase)))
      .rejects.toMatchObject({ code: "current_facts_integrity_failed" });
  });

  it("forwards cancellation to both GETs without retrying or writing", async () => {
    const controller = new AbortController();
    controller.abort();
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    await expect(getCurrentFactsSnapshot(record.id, "warranty", controller.signal, async (input, init) => {
      calls.push({ url: String(input), init });
      if (init?.signal?.aborted) throw new DOMException("Cancelled synthetic read", "AbortError");
      return requestFor(facts, record)(input, init);
    })).rejects.toMatchObject({ code: "network_error" });
    expect(calls.map(({ url }) => url)).toEqual(["/api/v1/cases/case_a/current-facts", "/api/v1/cases/case_a"]);
    expect(calls.every(({ init }) => init?.signal === controller.signal && init.cache === "no-store" && (init.method ?? "GET") === "GET")).toBe(true);
  });

  it.each(caseTypes.filter(caseType => caseType !== "warranty"))("keeps %s outside the warranty preparation reader", async caseType => {
    const { projection, currentCase } = await citedSnapshot(caseType);
    await expect(getPreparationSnapshot(record.id, new AbortController().signal, requestFor(projection, currentCase)))
      .rejects.toMatchObject({ code: "current_facts_integrity_failed" });
  });
});
