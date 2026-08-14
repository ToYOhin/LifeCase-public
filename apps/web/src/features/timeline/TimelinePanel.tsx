import { type ReactNode, useEffect, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import { requirementLabel, statusLabel } from "../workbench/presenters";
import { getTimeline } from "./api";
import {
  eventStatus,
  shortAuthority,
  timelineKindLabel,
  timelineOriginLabel,
  timelineTime,
  timelineView,
} from "./presenters";
import type { TimelineEvent, TimelineLoadState, TimelineResult } from "./types";

interface TimelinePanelProps {
  caseId: string | null;
  caseTitle: string | null;
  refreshKey: number;
}

export function TimelinePanel({ caseId, caseTitle, refreshKey }: TimelinePanelProps) {
  const [result, setResult] = useState<TimelineResult | null>(null);
  const [state, setState] = useState<TimelineLoadState>("idle");

  useEffect(() => {
    if (!caseId) {
      setResult(null);
      setState("idle");
      return;
    }
    void loadTimeline(caseId);
  }, [caseId, refreshKey]);

  async function loadTimeline(targetCaseId: string) {
    setState("loading");
    try {
      setResult(await getTimeline(targetCaseId));
      setState("ready");
    } catch (reason) {
      setResult(null);
      setState(
        reason instanceof LifeCaseApiError && reason.code === "network_error"
          ? "offline"
          : "error",
      );
    }
  }

  const events = result?.events ?? [];
  const view = timelineView(caseId, state, events.length);

  return (
    <section className="case-timeline" aria-labelledby="timeline-title">
      <div className="timeline-head">
        <div>
          <p className="section-label">CASE CHRONICLE</p>
          <h2 id="timeline-title">案件进展与恢复记录</h2>
          {caseTitle && <p>{caseTitle}</p>}
        </div>
        <div className="timeline-recovery-seal">
          <strong>{String(events.length).padStart(2, "0")}</strong>
          <span>条权威记录</span>
          <small>刷新 / 重启可恢复</small>
        </div>
      </div>

      {view === "unselected" && (
        <TimelineState mark="—" title="尚未选择案件">
          从 Today 台账打开案件；Timeline 不会跨案件聚合记录。
        </TimelineState>
      )}
      {view === "loading" && (
        <TimelineState mark="···" title="正在恢复持久化进展" role="status">
          正在按 Case 范围重新读取 SQLite 权威记录。
        </TimelineState>
      )}
      {view === "empty" && (
        <TimelineState mark="00" title="尚无可呈现事件">
          当前案件还没有材料、状态转换、事实决定、截止日期、Agent 状态或产物记录。
        </TimelineState>
      )}
      {view === "offline" && (
        <TimelineState mark="OFF" title="本地 API 未连接" role="alert">
          持久化进展没有丢失；启动 API 后可重新恢复。
          <button type="button" onClick={() => caseId && void loadTimeline(caseId)}>
            重新连接
          </button>
        </TimelineState>
      )}
      {view === "failure" && (
        <TimelineState mark="ERR" title="Timeline 无法安全读取" role="alert">
          权威关联可能不一致，系统已停止展示而不是使用不完整历史。
          <button type="button" onClick={() => caseId && void loadTimeline(caseId)}>
            重新校验
          </button>
        </TimelineState>
      )}
      {view === "populated" && result && (
        <>
          <div className="timeline-availability" role="note">
            <span>已覆盖：材料、转换、事实决定、截止日期、Action、Agent、产物</span>
            <span>审批已接入；沟通草稿仍只作为本地 Action 投影，不代表已发送</span>
          </div>
          <ol className="timeline-spine">
            {events.map((event, index) => (
              <TimelineItem event={event} index={index} key={event.event_id} />
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function TimelineItem({ event, index }: { event: TimelineEvent; index: number }) {
  const status = eventStatus(event);
  const label =
    event.authority_type === "claim_event" && event.label
      ? requirementLabel(event.label)
      : event.label;
  return (
    <li className={`timeline-event event-${event.kind}`}>
      <div className="timeline-clock">
        <span>{String(index + 1).padStart(2, "0")}</span>
        <time dateTime={event.occurred_at}>{timelineTime(event.occurred_at)}</time>
      </div>
      <div className="timeline-node" aria-hidden="true"><span /></div>
      <article>
        <div className="timeline-event-head">
          <div>
            <small>{timelineOriginLabel(event.origin)}</small>
            <h3>{timelineKindLabel(event.kind)}</h3>
          </div>
          <code title={event.authority_id}>{shortAuthority(event.authority_id)}</code>
        </div>
        <div className="timeline-event-facts">
          {label && <strong>{label}</strong>}
          {status && (
            <span className="timeline-status">
              {event.kind === "case_transitioned" && event.status
                ? `${event.prior_status ? `${statusLabel(event.prior_status)} → ` : ""}${statusLabel(event.status)}`
                : status}
            </span>
          )}
          {event.version !== null && <span>版本 {event.version}</span>}
          {event.actor && <span>操作者 {event.actor}</span>}
        </div>
        {event.reason && <p className="timeline-reason">{event.reason}</p>}
        <p className="timeline-reference">
          {event.authority_type} · {event.subject_id}
          {event.parent_id ? ` · 上级 ${event.parent_id}` : ""}
        </p>
      </article>
    </li>
  );
}

interface TimelineStateProps {
  mark: string;
  title: string;
  role?: "status" | "alert";
  children: ReactNode;
}

function TimelineState({ mark, title, role, children }: TimelineStateProps) {
  return (
    <div className="timeline-state" role={role}>
      <span>{mark}</span>
      <div><h3>{title}</h3><div>{children}</div></div>
    </div>
  );
}
