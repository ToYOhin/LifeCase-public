import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CurrentFactsList } from "./CurrentFactsPanel";
import type { CurrentFact } from "../warrantyPreparation/types";

function fact(): CurrentFact {
  return {
    claim: { id: "claim_a", case_id: "case_a", field: "product_name", value: "海星 H8 🧾", status: "confirmed", confidence: null, created_by: "user", contradiction: false, version: 2, created_at: "2026-09-18", updated_at: "2026-09-18" },
    confirmation_basis: "user", confirmation: { actor: "核实人", reason: "核对合成材料", occurred_at: "2026-09-18" },
    evidence_status: "none", citations: [], conflict: false, usable_as_fact: true,
  };
}
const render = (items: CurrentFact[], busy = false) => renderToStaticMarkup(
  <CurrentFactsList items={items} busy={busy} onSelect={() => undefined} />,
);
const outsideDetails = (markup: string) => markup.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g, "");

describe("已核实事实回看入口", () => {
  it("keeps settled facts and correction reachable without any review candidates", () => {
    const markup = render([fact()]);
    expect(markup).toContain("海星 H8 🧾");
    expect(markup).toContain("修正产品名称");
    expect(markup).toContain("版本 2");
    expect(markup).toContain("核实人");
    expect(markup).toContain("核对合成材料");
  });
  it("keeps the value, manual basis and correction visible while folding the audit record", () => {
    const markup = render([fact()]);
    const record = markup.match(/<details\b([^>]*)>([\s\S]*?)<\/details>/);
    expect(record).not.toBeNull();
    expect(record?.[1]).not.toMatch(/\bopen(?:\s|=|$)/);
    expect(record?.[2]).toContain("核实人");
    expect(record?.[2]).toContain("核对合成材料");
    const visible = outsideDetails(markup);
    expect(visible).toContain("产品名称");
    expect(visible).toContain("海星 H8 🧾");
    expect(visible).toContain("人工确认");
    expect(visible).toContain("修正产品名称");
    expect(visible).not.toContain("核实人");
    expect(visible).not.toContain("核对合成材料");
  });
  it("keeps source navigation alongside the compact fact record", () => {
    const item = fact();
    item.evidence_status = "valid";
    item.citations = [{
      claim_id: item.claim.id, case_id: item.claim.case_id, source_id: "source_synthetic", source_name: "合成保修凭证.txt",
      segment_id: "segment_synthetic", sequence: 0, page: null, text_sha256: "synthetic_text_hash",
      text: "海星 H8", quote_start: 0, quote_end: 5, quote_hash: "synthetic_quote_hash", quote_text: "海星 H8",
      relation: "supports", attached_by: "核实人", attached_at: "2026-09-18",
    }];
    const markup = render([item]);
    expect(markup).toMatch(/<details class="preparation-source"><summary>合成保修凭证\.txt · 段 1 · 查看原文<\/summary>/);
    expect(markup).toContain("<mark>海星 H8</mark>");
    expect(outsideDetails(markup)).toContain("修正产品名称");
  });
  it("keeps unavailable/conflicting settled facts visible with explicit warnings", () => {
    const item = fact(); item.evidence_status = "unavailable"; item.conflict = true; item.usable_as_fact = false;
    const markup = outsideDetails(render([item]));
    expect(markup).toContain("海星 H8 🧾");
    expect(markup).toContain("来源失效");
    expect(markup).toContain("存在冲突");
    expect(markup).toContain("暂不能作为可靠事实使用");
  });
  it("labels corrected authority as manual and does not imply verbatim evidence", () => {
    const item = fact(); item.claim.status = "corrected"; item.confirmation_basis = "correction";
    expect(outsideDetails(render([item]))).toContain("人工修正");
    expect(outsideDetails(render([item]))).toContain("不能视为原文逐字内容");
  });
  it("does not describe a missing confirmation record as evidence-backed", () => {
    const item = fact(); item.confirmation = null; item.confirmation_basis = null; item.usable_as_fact = false;
    expect(outsideDetails(render([item]))).toContain("核实记录缺失");
    expect(render([item])).not.toContain("依据原文确认");
  });
  it.each(["candidate", "inferred", "rejected"] as const)("does not put %s values in settled list", status => {
    const item = fact(); item.claim.status = status;
    expect(render([item])).not.toContain("海星 H8");
    expect(render([item])).toContain("尚无已核实事实");
  });
  it("does not coerce structured values into strings or allow selection while saving", () => {
    const item = fact(); item.claim.value = { label: "structured" };
    expect(render([item])).not.toContain("修正产品名称");
    expect(render([item])).toContain("暂不支持普通输入修正");
    expect(render([fact()], true)).toContain("disabled");
  });

  it.each([
    ["merchant", "商户"], ["return_tracking_number", "退货物流单号"],
    ["expense_date", "费用日期"], ["approval_reference", "预审批编号"],
    ["order_or_reference", "订单或参考编号"],
    ["cancellation_confirmation_reference", "取消确认编号"],
    ["amount", "金额"],
  ])("keeps settled %s correction reachable after the review queue is empty", (field, label) => {
    const item = fact(); item.claim.field = field;
    if (field === "amount") item.claim.value = 200;
    expect(render([item])).toContain(`修正${label}`);
  });
});
