import type {
  TimelineEvent,
  TimelineEventKind,
  TimelineLoadState,
  TimelineView,
} from "./types";

const kindLabels: Record<TimelineEventKind, string> = {
  source_imported: "材料已导入",
  source_extraction_status: "提取状态",
  artifact_created: "产物已登记",
  case_transitioned: "案件状态变化",
  claim_created: "事实候选已建立",
  claim_source_attached: "事实已关联来源",
  claim_confirmed: "事实已确认",
  claim_corrected: "事实已纠正",
  claim_rejected: "事实已拒绝",
  agent_run_status: "Agent 流程状态",
  agent_stage_status: "Agent 阶段状态",
  deadline_created: "截止日期已建立",
  deadline_confirmed: "截止日期已确认",
  deadline_passed: "截止日期已标记过期",
  deadline_completed: "截止日期已完成",
  deadline_dismissed: "截止日期已忽略",
  action_created: "Action 已建立",
  action_revised: "Action 已修订",
  action_revalidated: "Action 已重新校验",
  action_started: "Action 已开始",
  action_completed: "Action 已完成",
  action_failed: "Action 已记录失败",
  action_cancelled: "Action 已取消",
  action_approved: "Action 已批准",
  approval_requested: "审批已请求",
  approval_approved: "审批已批准",
  approval_rejected: "审批已拒绝",
  approval_expired: "审批已过期",
  approval_invalidated: "审批已失效",
};

const originLabels: Record<TimelineEvent["origin"], string> = {
  local_import: "本地导入",
  extractor: "本地提取器",
  recorded_actor: "已记录操作者",
  agent: "Agent",
  artifact_service: "产物服务",
};

export function timelineView(
  caseId: string | null,
  state: TimelineLoadState,
  count: number,
): TimelineView {
  if (!caseId) return "unselected";
  if (state === "idle" || state === "loading") return "loading";
  if (state === "offline") return "offline";
  if (state === "error") return "failure";
  return count === 0 ? "empty" : "populated";
}

export function timelineKindLabel(kind: TimelineEventKind): string {
  return kindLabels[kind];
}

export function timelineOriginLabel(origin: TimelineEvent["origin"]): string {
  return originLabels[origin];
}

export function timelineTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function shortAuthority(value: string): string {
  return value.length > 20 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

export function eventStatus(event: TimelineEvent): string | null {
  if (event.prior_status && event.status) {
    return `${event.prior_status} → ${event.status}`;
  }
  return event.status;
}
