import type { ActionKind, ActionLoadState, ActionRecord, ActionStatus, ActionView } from "./types";

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
  const labels: Record<string, string> = {
    intent: "沟通目的", recipient_role: "接收方角色", analysis_run_id: "来源分析运行",
    analysis_basis_sha256: "事实与材料依据 SHA-256", analysis_answer_sha256: "分析答案 SHA-256",
  };
  if (labels[key]) return labels[key];
  return key.replaceAll("_", " ");
}

export function canAdvanceAnalysisAction(item: ActionRecord): boolean {
  if (item.payload.analysis_run_id) return item.analysis_basis_status === "current";
  return item.analysis_basis_status !== "changed" && item.analysis_basis_status !== "unavailable";
}

export function actionReadingGroups(items: readonly ActionRecord[]) {
  const groups = [
    { id: "current", title: "当前可处理", description: "先查看这些待办；是否能推进仍以各卡片的阻塞、状态和审批要求为准。", items: [] as ActionRecord[] },
    { id: "changed", title: "依据变化待处置", description: "原待办仍保留。可重新分析，再由你决定保留或取消旧待办。", items: [] as ActionRecord[] },
    { id: "unavailable", title: "依据暂无法核对", description: "这些待办尚未结束。读取恢复前不能继续推进或审批，也不能据此判断依据已经变化。", items: [] as ActionRecord[] },
    { id: "ended", title: "已结束记录", description: "已完成或已取消的历史记录；原正文、来源、审批和处理记录仍可查看。", items: [] as ActionRecord[] },
  ];
  for (const item of items) {
    const index = item.status === "done" || item.status === "cancelled" ? 3
      : item.analysis_basis_status === "changed" ? 1
        : canAdvanceAnalysisAction(item) ? 0 : 2;
    groups[index].items.push(item);
  }
  return groups.filter(group => group.items.length > 0);
}

export function shortActionAuthority(value: string): string {
  return value.length > 20 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}
