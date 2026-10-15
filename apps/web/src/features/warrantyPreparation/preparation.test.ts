import { describe, expect, it } from "vitest";

import { buildPreparation, copyPreparation, preparationSignature, splitCitation, validateProjection } from "./preparation";
import type { CurrentFacts, CurrentFact } from "./types";
import type { ReviewCitation } from "../review/types";

function fact(overrides: Partial<CurrentFact> = {}): CurrentFact {
  return {
    claim: { id: "claim_a", case_id: "case_a", field: "product_name", value: "海星 H8", status: "confirmed", confidence: null, created_by: "user", contradiction: false, version: 2, created_at: "2026-09-18", updated_at: "2026-09-18" },
    confirmation_basis: "user", confirmation: { actor: "tester", reason: "本人核实", occurred_at: "2026-09-18" },
    evidence_status: "none", citations: [], conflict: false, usable_as_fact: true, ...overrides,
  };
}
const projection = (items: CurrentFact[]): CurrentFacts => ({ case_id: "case_a", case_type: "warranty", items });

describe("中文报修准备单", () => {
  it("offers registration for absent fields and no work for already usable facts", () => {
    const result = buildPreparation(projection([fact()]), [], "咨询");
    expect(result.fields.find(item => item.key === "serial_number")).toMatchObject({ next: "register" });
    expect(result.fields.find(item => item.key === "product_name")).toMatchObject({ next: null });
  });
  it.each(["candidate", "inferred", "rejected"] as const)("locates the usable next surface for %s instead of treating it as confirmed", status => {
    const item = fact({ usable_as_fact: false, confirmation: null });
    item.claim.status = status;
    const result = buildPreparation(projection([item]), [], "咨询");
    expect(result.fields.find(field => field.key === "product_name")).toMatchObject({ next: status === "rejected" ? "register" : "review", facts: [] });
  });
  it.each([{ conflict: true }, { evidence_status: "unavailable" as const }, {}])("routes unsafe or empty settled facts to their existing record: %j", overrides => {
    const item = fact(overrides);
    if (Object.keys(overrides).length === 0) item.claim.value = "";
    expect(buildPreparation(projection([item]), [], "咨询").fields[0]).toMatchObject({ next: "current", facts: [] });
  });
  it("takes mixed conflicts to the pending field without hiding other records", () => {
    const pending = fact({ conflict: true, usable_as_fact: false, confirmation: null });
    pending.claim = { ...pending.claim, id: "claim_pending", status: "candidate" };
    const original = projection([fact({ conflict: true, usable_as_fact: false }), pending]);
    const before = structuredClone(original);
    expect(buildPreparation(original, [], "咨询").fields[0]).toMatchObject({ next: "review", facts: [] });
    expect(original).toEqual(before);
  });
  it("uses settled facts, explicit intent and visible missing fields without promises", () => {
    const result = buildPreparation(projection([fact()]), [], "请告知检测流程和费用。");
    expect(result.text).toContain("产品名称：海星 H8（人工确认）");
    expect(result.text).toContain("本人诉求：请告知检测流程和费用。");
    expect(result.text).toContain("购买日期：待补充");
    expect(result.text).toContain("待本人核对，尚未发送");
    expect(result.text).not.toMatch(/仍在保修期|免费维修|已经发送/);
  });
  it.each(["candidate", "inferred", "rejected"] as const)("never states %s values as facts", (status) => {
    const item = fact(); item.claim.status = status; item.claim.value = "不可肯定的内容";
    const result = buildPreparation(projection([item]), [], "");
    expect(result.text).not.toContain("不可肯定的内容");
    expect(result.text).toContain("产品名称：待核实");
    expect(result.text).toContain("本人诉求：待填写");
  });
  it.each([{ conflict: true }, { evidence_status: "unavailable" as const }, { usable_as_fact: false }])("excludes unsafe settled facts: %j", (overrides) => {
    const result = buildPreparation(projection([fact(overrides)]), [], "咨询");
    expect(result.text).not.toContain("海星 H8");
    expect(result.issues.length).toBeGreaterThan(0);
  });
  it("marks corrections as manual rather than claiming a verbatim source match", () => {
    const item = fact({ confirmation_basis: "correction" }); item.claim.status = "corrected";
    expect(buildPreparation(projection([item]), [], "咨询").text).toContain("海星 H8（人工修正）");
  });
  it("does not silently choose one of two inconsistent settled values", () => {
    const second = fact({ conflict: true, usable_as_fact: false }); second.claim = { ...second.claim, id: "claim_b", value: "另一型号" };
    const result = buildPreparation(projection([fact({ conflict: true, usable_as_fact: false }), second]), [], "咨询");
    expect(result.text).not.toContain("海星 H8");
    expect(result.text).not.toContain("另一型号");
    expect(result.text).toContain("存在冲突");
  });
  it.each([["H8  Pro", "H8 Pro"], ["Straße", "STRASSE"], ["Ｈ８", "H8"]])(
    "respects the domain's equivalent values %s and %s without redefining equality", (left, right) => {
      const first = fact(); first.claim.value = left;
      const second = fact(); second.claim = { ...second.claim, id: "claim_b", value: right };
      const result = buildPreparation(projection([first, second]), [], "咨询");
      expect(result.text).toContain(`产品名称：${left}（人工确认）`);
      expect(result.text).not.toContain("存在冲突");
    },
  );
  it("rejects wrong Case projections and citations", () => {
    expect(() => validateProjection("case_b", projection([fact()]))).toThrow();
    const item = fact(); item.claim.case_id = "case_b";
    expect(() => validateProjection("case_a", projection([item]))).toThrow();
  });
  it("reports clipboard failure without pretending to send or save", async () => {
    expect(await copyPreparation("合成准备单", async () => { throw new Error("denied"); })).toBe(false);
    const copied: string[] = [];
    expect(await copyPreparation("合成准备单", async text => { copied.push(text); })).toBe(true);
    expect(copied).toEqual(["合成准备单"]);
  });

  it("does not treat an unrecorded confirmation or a source-less evidence decision as settled", () => {
    expect(buildPreparation(projection([fact({ confirmation: null })]), [], "").text).not.toContain("海星 H8");
    expect(buildPreparation(projection([fact({ confirmation_basis: "evidence", evidence_status: "valid" })]), [], "").text).not.toContain("海星 H8");
  });

  it("does not re-use saved desired_outcome as the user's present intent", () => {
    const item = fact(); item.claim.field = "desired_outcome"; item.claim.value = "旧诉求";
    expect(buildPreparation(projection([item]), [], "").text).toContain("本人诉求：待填写");
    expect(buildPreparation(projection([item]), [], "").text).not.toContain("旧诉求");
  });

  it("includes changes to confirmation authority in the copy revalidation signature", () => {
    const initial = projection([fact()]);
    const changed = projection([fact({ confirmation: { actor: "tester", reason: "更新后的判断理由", occurred_at: "2026-09-19" } })]);
    expect(preparationSignature(initial, [])).not.toEqual(preparationSignature(changed, []));
  });

  it("splits Chinese and English quotes by code point even after emoji", () => {
    const citation = { text: "🧾 购买日期 Date 2026-09-18。", quote_start: 2, quote_end: 14, quote_text: "购买日期 Date 20" } as ReviewCitation;
    expect(splitCitation(citation)).toEqual({ before: "🧾 ", quote: "购买日期 Date 20", after: "26-09-18。" });
    expect(() => splitCitation({ ...citation, quote_end: 999 })).toThrow();
  });
});
