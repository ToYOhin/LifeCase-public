import type { SourceRecord } from "../intake/types";
import { splitSourceQuote } from "../review/quoteOffsets";
import type { JsonValue, ReviewCitation } from "../review/types";
import type { CurrentFact, CurrentFacts } from "./types";

const fields = [
  ["product_name", "产品名称"],
  ["serial_number", "序列号"],
  ["merchant_or_manufacturer", "商家或制造商"],
  ["purchase_date", "购买日期"],
  ["order_number", "订单编号"],
  ["defect_description", "故障描述"],
] as const;

export interface PreparationField {
  key: string;
  label: string;
  text: string;
  facts: CurrentFact[];
  next: "register" | "review" | "current" | null;
}

export interface Preparation {
  text: string;
  fields: PreparationField[];
  issues: string[];
}

export function validateProjection(caseId: string, projection: CurrentFacts, caseType = "warranty"): void {
  if (!["warranty", "return_refund", "reimbursement", "subscription_cancellation"].includes(caseType) ||
      projection.case_id !== caseId || projection.case_type !== caseType ||
      !Array.isArray(projection.items)) throw new Error("当前案件事实不匹配");
  const ids = new Set<string>();
  for (const item of projection.items) {
    if (item.claim.case_id !== caseId || ids.has(item.claim.id) ||
        !Array.isArray(item.citations) || typeof item.usable_as_fact !== "boolean" ||
        typeof item.conflict !== "boolean") throw new Error("当前案件事实不匹配");
    ids.add(item.claim.id);
    for (const citation of item.citations) {
      if (citation.case_id !== caseId || citation.claim_id !== item.claim.id) {
        throw new Error("引用不属于当前事实");
      }
      splitCitation(citation);
    }
  }
}

export function splitCitation(citation: ReviewCitation) {
  const { quote_start: start, quote_end: end, text } = citation;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) ||
      start < 0 || end <= start || end > Array.from(text).length) {
    throw new Error("引用范围不可用");
  }
  const parts = splitSourceQuote(text, start, end);
  if (parts.quote !== citation.quote_text) throw new Error("引用原文不一致");
  return parts;
}

export function factBasisLabel(item: CurrentFact): string {
  if (item.confirmation_basis === "correction") return "人工修正";
  if (item.confirmation_basis === "user") return "人工确认";
  return "依据原文确认";
}

function usable(item: CurrentFact): boolean {
  if (!["confirmed", "corrected"].includes(item.claim.status) ||
      !item.usable_as_fact || item.conflict || item.claim.contradiction ||
      item.evidence_status === "unavailable" || !item.confirmation ||
      !item.confirmation.actor.trim() || !item.confirmation.reason.trim()) return false;
  if (item.confirmation_basis === "evidence") {
    return item.evidence_status === "valid" &&
      item.citations.some((citation) => citation.relation === "supports");
  }
  return item.confirmation_basis === "user" || item.confirmation_basis === "correction";
}

function valueLabel(value: JsonValue): string {
  return typeof value === "string" ? value.trim() : JSON.stringify(value);
}

export function buildPreparation(projection: CurrentFacts, sources: SourceRecord[], intent: string): Preparation {
  validateProjection(projection.case_id, projection);
  if (sources.some((source) => source.case_id !== projection.case_id)) {
    throw new Error("材料不属于当前案件");
  }
  const issues: string[] = [];
  const preparedFields: PreparationField[] = fields.map(([key, label]) => {
    const items = projection.items.filter((item) => item.claim.field === key);
    const settled = items.filter(usable);
    // Navigate using existing Claim status, without promoting a candidate or
    // confusing an empty review queue with missing settled records.
    const next = items.some(item => ["candidate", "inferred"].includes(item.claim.status)) ? "review"
      : items.some(item => ["confirmed", "corrected"].includes(item.claim.status)) ? "current" : "register";
    // The domain owns normalized equality; do not redefine it with JS casing rules.
    const conflicted = items.some((item) => item.conflict || item.claim.contradiction);
    if (conflicted) {
      issues.push(`${label}：存在冲突，请回到事实复核处理。`);
      return { key, label, text: "存在冲突，待核实", facts: [], next };
    }
    if (!settled.length) {
      const unavailable = items.some((item) => item.evidence_status === "unavailable");
      const detail = unavailable ? "来源失效，待重新核实" : items.length ? "待核实" : "待补充";
      issues.push(`${label}：${detail}。`);
      return { key, label, text: detail, facts: [], next };
    }
    const current = settled[0];
    if (!valueLabel(current.claim.value) || current.claim.value === null) {
      issues.push(`${label}：已确认值为空，待补充。`);
      return { key, label, text: "待补充", facts: [], next };
    }
    return { key, label, text: `${valueLabel(current.claim.value)}（${factBasisLabel(current)}）`, facts: settled, next: null };
  });
  if (!intent.trim()) issues.push("本人诉求：待填写。本页不会自动使用案件目标或历史诉求。");
  if (!sources.length) issues.push("材料清单为空：请先导入购买凭证和故障相关材料。");
  const sourceLines = sources.length
    ? sources.map((source, index) => `${index + 1}. ${source.original_name}（${["completed", "completed_with_notices"].includes(source.extraction.status) ? "已有可读文本" : "已导入；文本提取状态待核对"}）`)
    : ["暂无已导入材料"];
  const basisLines = preparedFields.flatMap(({ label, facts }) => facts.map((item) => {
    const confirmation = item.confirmation!;
    const refs = item.citations.map((citation) => `${citation.source_name}，${citation.page === null ? "" : `第 ${citation.page} 页，`}段 ${citation.sequence + 1}`).join("；");
    return `- ${label}：${factBasisLabel(item)}；${confirmation.actor}：${confirmation.reason}${refs ? `；来源 ${refs}` : "；无原文支持引用"}`;
  }));
  return {
    fields: preparedFields,
    issues,
    text: [
      "中文报修准备单", "待本人核对，尚未发送", "",
      ...preparedFields.map(({ label, text }) => `${label}：${text}`),
      `本人诉求：${intent.trim() || "待填写"}`, "", "材料清单", ...sourceLines,
      "文件已导入不等于材料齐全；请核对购买凭证、故障说明及必要的图片或视频。",
      "", "待补项与核对事项", ...(issues.length ? issues.map((issue) => `- ${issue}`) : ["- 请本人再次核对以上内容。"]),
      "", "事实核对依据", ...(basisLines.length ? basisLines : ["暂无可作为肯定陈述的已核实事实。"]),
      "", "本准备单仅整理当前记录，不判断保修资格或承诺处理结果。复制或保存文件不会发送，也不会完成案件行动。",
    ].join("\n"),
  };
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

// Include authority metadata, not only displayed words, when comparing a fresh read.
export function preparationSignature(projection: CurrentFacts, sources: SourceRecord[]): string {
  return stableJson({
    ...projection,
    items: [...projection.items].sort((a, b) => a.claim.id.localeCompare(b.claim.id)),
    sources: [...sources].sort((a, b) => a.id.localeCompare(b.id)),
  });
}

export async function copyPreparation(text: string, write: (text: string) => Promise<void>): Promise<boolean> {
  try {
    await write(text);
    return true;
  } catch {
    return false;
  }
}
