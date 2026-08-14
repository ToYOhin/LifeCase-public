import type { ReactNode } from "react";

import {
  actionKindLabel,
  actionStatusLabel,
} from "../actions/presenters";
import {
  activityLabel,
  formatActivityTime,
  requirementLabel,
  statusLabel,
  workbenchView,
} from "./presenters";
import type { CaseWorkbenchItem, WorkbenchLoadState } from "./types";

interface CaseDocketProps {
  mode: "full" | "switcher";
  state: WorkbenchLoadState;
  cases: CaseWorkbenchItem[];
  onOpen: (caseId: string) => void;
  onRefresh: () => void;
  onCreate: () => void;
}

export function CaseDocket(props: CaseDocketProps) {
  if (props.mode === "switcher") {
    return (
      <details className="case-switcher">
        <summary>
          <strong>切换案件（{props.cases.length}）</strong>
          <span>按需打开本地案件台账</span>
        </summary>
        <DocketBody {...props} compact />
      </details>
    );
  }

  return (
    <section className="workbench" aria-labelledby="workbench-title">
      <div className="workbench-head">
        <div>
          <p className="section-label">TODAY DOCKET</p>
          <h2 id="workbench-title">今日案件台账</h2>
          <p>按优先级、最近权威活动和已登记行动排序；截止风险仍未计算。</p>
        </div>
        <button
          className="refresh-action"
          type="button"
          onClick={props.onRefresh}
          disabled={props.state === "loading"}
        >
          {props.state === "loading" ? "同步中…" : "刷新本地台账"}
        </button>
      </div>
      <DocketBody {...props} />
    </section>
  );
}

function DocketBody({
  state,
  cases,
  onOpen,
  onRefresh,
  onCreate,
  compact = false,
}: CaseDocketProps & { compact?: boolean }) {
  const view = workbenchView(state, cases.length);
  const todayCases = cases.filter((item) => item.is_today);
  const referenceCases = cases.filter((item) => !item.is_today);

  if (view === "loading") {
    return (
      <DocketState
        mark="···"
        title="正在读取本地案件"
        detail="只读取 SQLite 摘要，不发送网络请求。"
        role="status"
      />
    );
  }
  if (view === "empty") {
    return (
      <DocketState
        mark="00"
        title="暂无案件记录"
        detail="在上方建立第一份本地案件，建立后将显示于今日清单。"
      >
        <button type="button" onClick={onCreate}>建立案件</button>
      </DocketState>
    );
  }
  if (view === "offline") {
    return (
      <DocketState
        mark="OFF"
        title="本地 API 未连接"
        detail="确认 API 已启动后重试；现有数据没有改变。"
        role="alert"
      >
        <button type="button" onClick={onRefresh}>重新连接</button>
      </DocketState>
    );
  }
  if (view === "failure") {
    return (
      <DocketState
        mark="ERR"
        title="案件台账暂时无法读取"
        detail="本地服务返回安全错误，可稍后重试。"
        role="alert"
      >
        <button type="button" onClick={onRefresh}>重试读取</button>
      </DocketState>
    );
  }

  return (
    <>
      {!compact && (
        <div className="docket-summary" aria-label="今日案件摘要">
          <span><strong>{String(todayCases.length).padStart(2, "0")}</strong> 今日案件</span>
          <span><strong>{todayCases.reduce((sum, item) => sum + item.missing_evidence.total, 0)}</strong> 条材料缺口</span>
          <span><strong>{todayCases.filter((item) => item.next_action).length}</strong> 个下一行动</span>
          <span><strong>—</strong> 截止风险未计算</span>
        </div>
      )}
      {todayCases.length > 0 ? (
        <DocketList items={todayCases} onOpen={onOpen} />
      ) : (
        <div className="today-clear">
          <strong>今日清单已清空</strong>
          <span>已解决、放弃或归档的案件仍保留在台账中。</span>
        </div>
      )}
      {referenceCases.length > 0 && (
        <details className="reference-cases">
          <summary>查看其他案件（{referenceCases.length}）</summary>
          <DocketList items={referenceCases} onOpen={onOpen} />
        </details>
      )}
    </>
  );
}

function DocketList({ items, onOpen }: { items: CaseWorkbenchItem[]; onOpen: (caseId: string) => void }) {
  return (
    <ol className="docket-list">
      {items.map((item, index) => (
        <li key={item.id}>
          <button className="docket-row" type="button" onClick={() => onOpen(item.id)}>
            <span className="docket-index">{String(index + 1).padStart(2, "0")}</span>
            <span className="docket-title">
              <strong>{item.title}</strong>
              <small>{item.goal}</small>
            </span>
            <span className={`case-state case-state-${item.status}`}>{statusLabel(item.status)}</span>
            <span className="docket-gap"><strong>{item.missing_evidence.total}</strong><small>项缺口</small></span>
            <span className="docket-activity"><strong>{formatActivityTime(item.last_activity_at)}</strong><small>{activityLabel(item.last_activity_kind)}</small></span>
            <span className="docket-open" aria-hidden="true">↗</span>
          </button>
          {item.missing_evidence.total > 0 && (
            <div className="requirement-rail" aria-label={`${item.title} 待补材料`}>
              {[
                ...item.missing_evidence.required_claims,
                ...item.missing_evidence.evidence_requirements,
              ].slice(0, 4).map((key) => (
                <span key={key}>{requirementLabel(key)}</span>
              ))}
              {item.missing_evidence.total > 4 && <span>另 {item.missing_evidence.total - 4} 项</span>}
            </div>
          )}
          {item.next_action && (
            <div className="next-action-rail" aria-label={`${item.title} 下一行动`}>
              <strong>{actionKindLabel(item.next_action.kind)}</strong>
              <span>{actionStatusLabel(item.next_action.status)}</span>
              <span>风险 {item.next_action.risk_level}</span>
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

function DocketState({
  mark,
  title,
  detail,
  role,
  children,
}: {
  mark: string;
  title: string;
  detail: string;
  role?: "status" | "alert";
  children?: ReactNode;
}) {
  return (
    <div className="workbench-state" role={role}>
      <span className="state-number">{mark}</span>
      <div><h3>{title}</h3><p>{detail}</p></div>
      {children}
    </div>
  );
}
