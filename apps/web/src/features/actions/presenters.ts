import type { ActionKind, ActionLoadState, ActionStatus, ActionView } from "./types";

const kindLabels: Record<ActionKind, string> = {
  collect_evidence: "补齐证据",
  prepare_communication: "准备沟通草稿",
  follow_up: "安排本地跟进",
  record_outcome: "登记结果",
};

const statusLabels: Record<ActionStatus, string> = {
  proposed: "待校验",
  blocked: "有阻塞",
  ready: "可推进",
  running: "进行中",
  done: "已完成",
  failed: "失败",
  cancelled: "已取消",
  approved: "已批准",
};

export function actionView(
  caseId: string | null,
  state: ActionLoadState,
  count: number,
): ActionView {
  if (!caseId) return "unselected";
  if (state === "idle" || state === "loading") return "loading";
  if (state === "offline") return "offline";
  if (state === "error") return "failure";
  return count === 0 ? "empty" : "populated";
}

export function actionKindLabel(kind: ActionKind): string {
  return kindLabels[kind];
}

export function actionStatusLabel(status: ActionStatus): string {
  return statusLabels[status];
}

export function actionPayloadLabel(key: string): string {
  return key.replaceAll("_", " ");
}

export function shortActionAuthority(value: string): string {
  return value.length > 20 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}
