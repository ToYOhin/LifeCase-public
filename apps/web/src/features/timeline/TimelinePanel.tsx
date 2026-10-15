import { type ReactNode, useEffect, useRef, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import { statusLabel } from "../workbench/presenters";
import { getTimeline } from "./api";
import {
  eventStatus,
  timelineKindLabel,
  timelineOriginLabel,
  timelineTime,
  timelineView,
  timelineEventLabel,
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
  const sequence = useRef(0);
  const target = useRef(caseId); target.current = caseId;

  useEffect(() => {
    if (!caseId) {
      setResult(null);
      setState("idle");
      return;
    }
    void loadTimeline(caseId);
    return () => { sequence.current += 1; };
  }, [caseId, refreshKey]);

  async function loadTimeline(targetCaseId: string) {
    if (target.current !== targetCaseId) return;
    const generation = ++sequence.current;
    setState("loading");
    try {
      const next = await getTimeline(targetCaseId);
      if (sequence.current !== generation || target.current !== targetCaseId) return;
      if (next.case_id !== targetCaseId || next.events.some(event => event.case_id !== targetCaseId)) throw new Error("Case authority mismatch");
      setResult(next);
      setState("ready");
    } catch (reason) {
      if (sequence.current !== generation || target.current !== targetCaseId) return;
      setResult(null);
      setState(
        reason instanceof LifeCaseApiError && reason.code === "network_error"
          ? "offline"
          : "error",
      );
    }
  }

  const events = result?.case_id === caseId ? result.events : [];
  const view = timelineView(caseId, state, events.length);

  return (
    <section className="case-timeline" aria-labelledby="timeline-title">
      <div className="timeline-head">
        <div>
          <h2 id="timeline-title">案件记录</h2>
          {caseTitle && <p>{caseTitle}</p>}
        </div>
        <div className="timeline-recovery-seal">
          <strong>{String(events.length).padStart(2, "0")}</strong>
          <span>条记录</span>
        </div>
      </div>

      {view === "unselected" && (
        <TimelineState mark="—" title="尚未选择案件">
          从案件列表打开一个案件后查看记录。
        </TimelineState>
      )}
      {view === "loading" && (
        <TimelineState mark="···" title="正在读取案件记录" role="status">
          {null}
        </TimelineState>
      )}
      {view === "empty" && (
        <TimelineState mark="00" title="暂无案件记录">
          导入材料或处理待办后，可在这里回看。
        </TimelineState>
      )}
      {view === "offline" && (
        <TimelineState mark="!" title="服务未连接" role="alert">
          连接恢复后可重新读取案件记录。
          <button type="button" onClick={() => caseId && void loadTimeline(caseId)}>
            重新连接
          </button>
        </TimelineState>
      )}
      {view === "failure" && (
        <TimelineState mark="!" title="案件记录暂时无法读取" role="alert">
          暂未取得完整记录，请重新读取。
          <button type="button" onClick={() => caseId && void loadTimeline(caseId)}>
            重新读取
          </button>
        </TimelineState>
      )}
      {view === "populated" && result && (
        <ol className="timeline-spine">
          {events.map((event, index) => (
            <TimelineItem event={event} index={index} key={event.event_id} />
          ))}
        </ol>
      )}
    </section>
  );
}

export function TimelineItem({ event, index }: { event: TimelineEvent; index: number }) {
  const status = eventStatus(event);
  const label = timelineEventLabel(event);
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
        <details className="timeline-reference">
          <summary>记录详情</summary>
          <p>记录类型：{event.authority_type}</p>
          <p>记录编号：{event.authority_id}</p>
          <p>关联对象：{event.subject_id}</p>
          {event.parent_id && <p>上级记录：{event.parent_id}</p>}
        </details>
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
