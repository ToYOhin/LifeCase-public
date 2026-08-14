import type {
  DeadlineDateKind,
  DeadlineKind,
  DeadlineLoadState,
  DeadlineRecord,
  DeadlineStatus,
  DeadlineView,
} from "./types";

const kindLabels: Record<DeadlineKind, string> = {
  explicit: "证据明确日期",
  calculated: "规则计算日期",
  suggested: "个人跟进偏好",
};

const statusLabels: Record<DeadlineStatus, string> = {
  proposed: "待确认",
  confirmed: "已确认",
  passed: "已过期",
  completed: "已完成",
  dismissed: "已忽略",
};

const dateKindLabels: Record<DeadlineDateKind, string> = {
  utc_instant: "UTC 时刻",
  local_date: "本地日期",
};

export function deadlineView(
  caseId: string | null,
  state: DeadlineLoadState,
  count: number,
): DeadlineView {
  if (!caseId) return "unselected";
  if (state === "idle" || state === "loading") return "loading";
  if (state === "offline") return "offline";
  if (state === "error") return "failure";
  return count === 0 ? "empty" : "populated";
}

export function deadlineKindLabel(kind: DeadlineKind): string {
  return kindLabels[kind];
}

export function deadlineStatusLabel(status: DeadlineStatus): string {
  return statusLabels[status];
}

export function deadlineDateKindLabel(kind: DeadlineDateKind): string {
  return dateKindLabels[kind];
}

export function deadlineUncertaintyLabel(value: DeadlineRecord["uncertainty"]): string {
  if (value === "evidence_backed_date") return "来自已确认或已纠正的权威事实";
  if (value === "deterministic_rule_result_requires_confirmation") {
    return "确定性规则结果，仍需用户确认；当前不推断风险";
  }
  return "仅为个人跟进偏好，不是外部义务或计算截止日期";
}

export function deadlineDueLabel(record: DeadlineRecord): string {
  if (record.date_kind === "local_date") return record.due_value;
  const parsed = new Date(record.due_value);
  if (Number.isNaN(parsed.getTime())) return "时间无效";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      dateStyle: "medium",
      timeStyle: "medium",
      hour12: false,
      timeZone: record.timezone,
    }).format(parsed);
  } catch {
    return "时区无效";
  }
}

export function shortDeadlineAuthority(value: string): string {
  return value.length > 22 ? `${value.slice(0, 9)}…${value.slice(-7)}` : value;
}
