import type {
  CaseActivityKind,
  WorkbenchLoadState,
  WorkbenchView,
} from "./types";

const statusLabels: Record<string, string> = {
  intake: "待整理",
  needs_review: "待复核",
  ready: "已就绪",
  in_progress: "进行中",
  waiting_external: "等待外部",
  blocked: "受阻",
  resolved: "已解决",
  abandoned: "已放弃",
  archived: "已归档",
};

const activityLabels: Record<CaseActivityKind, string> = {
  case_updated: "案件更新",
  source_imported: "材料导入",
  extraction_updated: "提取更新",
  claim_updated: "事实更新",
  agent_updated: "本地智能处理",
  action_updated: "行动更新",
};

const fieldLabels: Record<string, string> = {
  merchant_or_manufacturer: "商家或制造商",
  product_name: "产品名称",
  purchase_date: "购买日期",
  order_number: "订单编号",
  desired_outcome: "期望结果",
  purchase_proof: "购买凭证",
  defect_description: "问题描述",
  defect_media: "问题图片或视频",
  merchant: "商户",
  item_name: "商品名称",
  paid_amount: "付款金额",
  delivery_date: "送达日期",
  return_reason: "退货原因",
  delivery_proof: "送达凭证",
  item_condition: "商品状态材料",
  expense_date: "费用日期",
  amount: "金额",
  currency: "币种",
  expense_category: "费用类别",
  business_purpose: "业务用途",
  receipt: "收据或发票",
  payment_proof: "付款凭证",
};

export function workbenchView(
  state: WorkbenchLoadState,
  itemCount: number,
): WorkbenchView {
  if (state === "loading") return "loading";
  if (state === "offline") return "offline";
  if (state === "error") return "failure";
  return itemCount === 0 ? "empty" : "populated";
}

export function statusLabel(status: string): string {
  return statusLabels[status] ?? "未知状态";
}

export function activityLabel(kind: CaseActivityKind): string {
  return activityLabels[kind];
}

export function requirementLabel(key: string): string {
  return fieldLabels[key] ?? key.replaceAll("_", " ");
}

export function formatActivityTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}
