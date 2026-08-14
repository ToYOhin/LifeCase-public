import { type ReactNode, useEffect, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import { requirementLabel } from "../workbench/presenters";
import {
  calendarExportUrl,
  changeDeadlineStatus,
  createSuggestedDeadline,
  getDeadlines,
} from "./api";
import {
  deadlineDateKindLabel,
  deadlineDueLabel,
  deadlineKindLabel,
  deadlineStatusLabel,
  deadlineUncertaintyLabel,
  deadlineView,
  shortDeadlineAuthority,
} from "./presenters";
import type { DeadlineLoadState, DeadlineRecord } from "./types";
import { LazyWorkflowTool } from "../workbench/LazyWorkflowTool";

interface DeadlinePanelProps {
  caseId: string | null;
  caseTitle: string | null;
  refreshKey: number;
  onChanged: () => void | Promise<void>;
}

export function DeadlinePanel({ caseId, caseTitle, refreshKey, onChanged }: DeadlinePanelProps) {
  const [items, setItems] = useState<DeadlineRecord[]>([]);
  const [state, setState] = useState<DeadlineLoadState>("idle");
  const [dueValue, setDueValue] = useState("");
  const [timezone, setTimezone] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const [actor, setActor] = useState("local_user");
  const [reason, setReason] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    if (!caseId) {
      setItems([]);
      setState("idle");
      return;
    }
    void load(caseId);
  }, [caseId, refreshKey]);

  async function load(targetCaseId: string) {
    setState("loading");
    try {
      setItems(await getDeadlines(targetCaseId));
      setState("ready");
    } catch (reason) {
      setItems([]);
      setState(
        reason instanceof LifeCaseApiError && reason.code === "network_error"
          ? "offline"
          : "error",
      );
    }
  }

  async function createReminder() {
    if (!caseId || !dueValue || !actor.trim() || !reason.trim()) return;
    setPendingId("create");
    setFeedback(null);
    try {
      await createSuggestedDeadline(caseId, {
        date_kind: "local_date",
        due_value: dueValue,
        timezone,
        actor: actor.trim(),
        reason: reason.trim(),
      });
      setDueValue("");
      setReason("");
      setFeedback("个人跟进日期已登记；它不是外部法定期限。请确认显示值后再导出 ICS。");
      await load(caseId);
      await onChanged();
    } catch (caught) {
      setFeedback(caught instanceof LifeCaseApiError ? caught.message : "日期没有保存，请重新读取后再试。");
    } finally {
      setPendingId(null);
    }
  }

  async function changeStatus(
    item: DeadlineRecord,
    operation: "confirm" | "complete" | "dismiss",
  ) {
    if (!caseId) return;
    setPendingId(item.id);
    setFeedback(null);
    try {
      await changeDeadlineStatus(item.id, operation, {
        expected_version: item.version,
        actor: actor.trim() || "local_user",
        reason: reason.trim() || `Browser ${operation} after reviewing the displayed date`,
      });
      await load(caseId);
      await onChanged();
    } catch (caught) {
      setFeedback(caught instanceof LifeCaseApiError ? caught.message : "日期状态没有保存，请刷新后再试。");
    } finally {
      setPendingId(null);
    }
  }

  const view = deadlineView(caseId, state, items.length);
  return (
    <section className="deadline-board" aria-labelledby="deadline-title">
      <div className="deadline-head">
        <div>
          <p className="section-label">DEADLINE RECORDS</p>
          <h2 id="deadline-title">截止日期与个人提醒</h2>
          {caseTitle && <p>{caseTitle}</p>}
        </div>
        <div className="deadline-seal">
          <strong>{String(items.length).padStart(2, "0")}</strong>
          <span>条权威记录</span>
          <small>风险尚未计算</small>
        </div>
      </div>

      {caseId && (
        <LazyWorkflowTool title="登记跟进日期" hint="仅本地提醒，不代表外部义务">
        <div className="deadline-composer">
          <div><strong>登记个人跟进日期</strong><span>仅本地提醒，不代表外部义务</span></div>
          <label>日期<input type="date" value={dueValue} onChange={(event) => setDueValue(event.target.value)} /></label>
          <label>时区<input value={timezone} maxLength={100} onChange={(event) => setTimezone(event.target.value)} /></label>
          <label>操作人<input value={actor} maxLength={120} onChange={(event) => setActor(event.target.value)} /></label>
          <label>理由<input value={reason} maxLength={1000} onChange={(event) => setReason(event.target.value)} placeholder="为什么要在这一天跟进" /></label>
          <button type="button" disabled={pendingId !== null || !dueValue || !reason.trim()} onClick={() => void createReminder()}>登记日期</button>
        </div>
        </LazyWorkflowTool>
      )}
      {feedback && <p className="deadline-feedback" role="status">{feedback}</p>}

      {view === "unselected" && (
        <DeadlineState mark="—" title="尚未选择案件">
          从 Today 台账打开案件；日期不会跨案件聚合。
        </DeadlineState>
      )}
      {view === "loading" && (
        <DeadlineState mark="···" title="正在读取截止日期" role="status">
          正在从 SQLite 重新校验日期、事实依据与规则版本。
        </DeadlineState>
      )}
      {view === "empty" && (
        <DeadlineState mark="00" title="尚无已提交日期">
          当前没有明确、计算或个人建议日期；系统不会猜测 deadline risk。
        </DeadlineState>
      )}
      {view === "offline" && (
        <DeadlineState mark="OFF" title="本地 API 未连接" role="alert">
          现有记录没有改变；启动 API 后可重新读取。
          <button type="button" onClick={() => caseId && void load(caseId)}>重新连接</button>
        </DeadlineState>
      )}
      {view === "failure" && (
        <DeadlineState mark="ERR" title="截止日期无法安全读取" role="alert">
          事实依据、规则或持久化可能不一致；系统已停止展示不完整结果。
          <button type="button" onClick={() => caseId && void load(caseId)}>重新校验</button>
        </DeadlineState>
      )}
      {view === "populated" && (
        <ol className="deadline-grid">
          {items.map((item) => (
            <DeadlineCard
              caseId={caseId!}
              item={item}
              pending={pendingId === item.id}
              onStatus={changeStatus}
              key={item.id}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function DeadlineCard({
  caseId,
  item,
  pending,
  onStatus,
}: {
  caseId: string;
  item: DeadlineRecord;
  pending: boolean;
  onStatus: (
    item: DeadlineRecord,
    operation: "confirm" | "complete" | "dismiss",
  ) => Promise<void>;
}) {
  return (
    <li className={`deadline-card deadline-${item.kind}`}>
      <div className="deadline-card-head">
        <span className="deadline-kind">{deadlineKindLabel(item.kind)}</span>
        <span className={`deadline-status deadline-status-${item.status}`}>
          {deadlineStatusLabel(item.status)}
        </span>
      </div>
      <strong className="deadline-due">{deadlineDueLabel(item)}</strong>
      <div className="deadline-date-meta">
        <span>{deadlineDateKindLabel(item.date_kind)}</span>
        <span>{item.timezone}</span>
        <span>版本 {item.version}</span>
      </div>
      <p className="deadline-uncertainty">{deadlineUncertaintyLabel(item.uncertainty)}</p>
      {item.bases.length > 0 && (
        <div className="deadline-basis">
          <small>BASIS CLAIM</small>
          {item.bases.map((basis) => (
            <span key={basis.claim_id} title={basis.claim_id}>
              {requirementLabel(basis.field)} · v{basis.claim_version} · {shortDeadlineAuthority(basis.claim_id)}
            </span>
          ))}
        </div>
      )}
      {item.rule && (
        <div className="deadline-rule">
          <small>DETERMINISTIC RULE</small>
          <span>{item.rule.id} · v{item.rule.version}</span>
        </div>
      )}
      <code className="deadline-definition" title={item.definition_sha256}>
        {shortDeadlineAuthority(item.definition_sha256)}
      </code>
      {item.status === "confirmed" && (
        <a
          className="deadline-calendar-export"
          href={calendarExportUrl(caseId, item.id)}
          download
        >
          下载本地日历文件
        </a>
      )}
      <div className="deadline-controls">
        {item.status === "proposed" && <button type="button" disabled={pending} onClick={() => void onStatus(item, "confirm")}>确认显示日期</button>}
        {item.status === "confirmed" && <button type="button" disabled={pending} onClick={() => void onStatus(item, "complete")}>标记已处理</button>}
        {!['completed', 'dismissed'].includes(item.status) && <button type="button" disabled={pending} onClick={() => void onStatus(item, "dismiss")}>忽略</button>}
      </div>
    </li>
  );
}

interface DeadlineStateProps {
  mark: string;
  title: string;
  role?: "status" | "alert";
  children: ReactNode;
}

function DeadlineState({ mark, title, role, children }: DeadlineStateProps) {
  return (
    <div className="deadline-state" role={role}>
      <span>{mark}</span>
      <div><h3>{title}</h3><div>{children}</div></div>
    </div>
  );
}
