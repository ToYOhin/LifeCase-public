import type {
  AgentDemoLoadState,
  AgentDemoResult,
  AgentStageTrace,
  AgentDemoView,
  AgentStageName,
  LiveAgentCapability,
} from "./types";

export const agentStageOrder: AgentStageName[] = [
  "extract",
  "normalize",
  "link_claims",
  "classify_case",
  "check_gaps",
  "propose_actions",
  "validate",
];

export function agentDemoView(
  caseId: string | null,
  state: AgentDemoLoadState,
  result: AgentDemoResult | null,
): AgentDemoView {
  if (!caseId) return "unselected";
  if (state === "loading" || state === "idle") return "loading";
  if (state === "offline") return "offline";
  if (state === "error") return "failure";
  if (result?.capability.status === "disabled") return "disabled";
  if (state === "running") return "running";
  if (result?.run?.status === "completed") return "completed";
  return "ready";
}

export function stageLabel(stage: AgentStageName): string {
  return {
    extract: "提取候选事实",
    normalize: "规范化",
    link_claims: "关联事实",
    classify_case: "识别案件类型",
    check_gaps: "检查缺件",
    propose_actions: "提出只读建议",
    validate: "证据检索 / 回答",
  }[stage];
}

export function liveWorkflowLabel(stageOrder: AgentStageName[]): string {
  if (stageOrder.length === 1 && stageOrder[0] === "validate") return "问题证据分析";
  return stageOrder.length === 7 ? "旧版七阶段" : "提取与证据回答";
}

export function runStatusLabel(status: string): string {
  return {
    queued: "等待执行",
    running: "执行中",
    checkpointed: "阶段已保存",
    needs_review: "需要恢复或复核",
    cancelled: "已取消",
    completed: "已完成",
  }[status] ?? status;
}

export function liveProviderLabel(provider: LiveAgentCapability["provider"]): string {
  return {
    disabled: "默认关闭",
    openai: "OpenAI",
    groq: "Groq",
    mistral: "Mistral",
    deepseek: "DeepSeek",
    gemini: "Google Gemini",
    anthropic: "Anthropic Claude",
  }[provider];
}

export function completedStageCount(stages: AgentStageTrace[]): number {
  return stages.filter((stage) => stage.status === "checkpointed").length;
}

export function answerStatusLabel(status: "answered" | "unknown"): string {
  return status === "answered" ? "已回答" : "证据不足";
}

export function claimFieldLabel(field: string): string {
  return {
    amount: "金额",
    merchant_or_manufacturer: "商家 / 制造商",
    order_number: "订单号",
    product_name: "产品名称",
    purchase_date: "购买日期",
    serial_number: "序列号",
  }[field] ?? field.replaceAll("_", " ");
}

export function claimStatusLabel(status: string): string {
  return {
    candidate: "候选",
    inferred: "推断",
    confirmed: "已确认",
    corrected: "已修正",
    rejected: "已拒绝",
  }[status] ?? "状态待核对";
}

export function claimReviewNote(status: string): string {
  return {
    candidate: "模型提取，待人工复核",
    inferred: "模型推断，待人工复核",
    confirmed: "已按来源或人工记录确认",
    corrected: "已采用修正后的事实",
    rejected: "不作为已确认事实",
  }[status] ?? "请到事实复核查看当前记录";
}

export function claimValueLabel(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "未提供";
  if (field === "amount" && isAmountValue(value)) {
    const amount = (value.amount_minor / 100).toFixed(2);
    return value.currency === "CNY" ? `¥${amount}` : `${value.currency} ${amount}`;
  }
  if (["string", "number", "boolean"].includes(typeof value)) return String(value);
  if (Array.isArray(value)) {
    const readable = value.filter(isPrimitive).map(String);
    return readable.length > 0 ? readable.join("、") : "结构化信息";
  }
  return "结构化信息";
}

function isAmountValue(value: unknown): value is { amount_minor: number; currency: string } {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.amount_minor === "number" && typeof candidate.currency === "string";
}

function isPrimitive(value: unknown): value is string | number | boolean {
  return ["string", "number", "boolean"].includes(typeof value);
}
