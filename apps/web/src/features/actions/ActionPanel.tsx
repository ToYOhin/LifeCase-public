import { type ReactNode, useEffect, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import { requirementLabel } from "../workbench/presenters";
import {
  changeActionStatus,
  completeAction,
  createAction,
  decideActionApproval,
  getActions,
  getApprovals,
  requestActionApproval,
} from "./api";
import {
  actionKindLabel,
  actionPayloadLabel,
  actionStatusLabel,
  actionView,
  shortActionAuthority,
} from "./presenters";
import type { ActionLoadState, ActionRecord, ApprovalRecord } from "./types";
import { LazyWorkflowTool } from "../workbench/LazyWorkflowTool";

interface ActionPanelProps {
  caseId: string | null;
  caseTitle: string | null;
  refreshKey: number;
  onChanged: () => void | Promise<void>;
  sources: Array<{ id: string; original_name: string; sha256: string }>;
  evidenceFields: string[];
}

export function ActionPanel({
  caseId,
  caseTitle,
  refreshKey,
  onChanged,
  sources,
  evidenceFields,
}: ActionPanelProps) {
  const [items, setItems] = useState<ActionRecord[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [state, setState] = useState<ActionLoadState>("idle");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [evidenceField, setEvidenceField] = useState("");
  const [actor, setActor] = useState("local_user");
  const [reason, setReason] = useState("");
  const [completionSourceId, setCompletionSourceId] = useState("");

  useEffect(() => {
    setCompletionSourceId((current) =>
      sources.some((source) => source.id === current) ? current : (sources[0]?.id ?? ""),
    );
  }, [sources]);

  useEffect(() => {
    setEvidenceField((current) =>
      evidenceFields.includes(current) ? current : (evidenceFields[0] ?? ""),
    );
  }, [evidenceFields]);

  useEffect(() => {
    if (!caseId) {
      setItems([]);
      setApprovals([]);
      setState("idle");
      return;
    }
    void load(caseId);
  }, [caseId, refreshKey]);

  async function load(targetCaseId: string) {
    setState("loading");
    setFeedback(null);
    try {
      const [nextItems, nextApprovals] = await Promise.all([
        getActions(targetCaseId),
        getApprovals(targetCaseId),
      ]);
      setItems(nextItems);
      setApprovals(nextApprovals);
      setState("ready");
    } catch (reason) {
      setItems([]);
      setApprovals([]);
      setState(
        reason instanceof LifeCaseApiError && reason.code === "network_error"
          ? "offline"
          : "error",
      );
    }
  }

  async function requestApproval(item: ActionRecord) {
    if (!caseId) return;
    setPendingId(item.id);
    setFeedback(null);
    try {
      await requestActionApproval(item.id, {
        expected_action_version: item.version,
        payload_sha256: item.payload_sha256,
        summary: approvalSummary(item),
        actor: "local_user",
        reason: "Workbench exact-version approval request",
      });
      await load(caseId);
      await onChanged();
    } catch (reason) {
      setFeedback(commandFailure(reason));
    } finally {
      setPendingId(null);
    }
  }

  async function createLocalAction() {
    if (!caseId || !evidenceField || !actor.trim() || !reason.trim()) return;
    setPendingId("create");
    setFeedback(null);
    try {
      await createAction(caseId, {
        kind: "collect_evidence",
        risk_level: "B",
        payload: {
          requirement_key: evidenceField,
          requirement_type: "evidence",
        },
        requirements: [],
        dependencies: [],
        deadline: null,
        actor: actor.trim(),
        reason: reason.trim(),
      });
      setReason("");
      setFeedback("本地跟进行动已登记。Risk B 必须先申请并批准精确版本，系统不会向外部发送任何内容。");
      await load(caseId);
      await onChanged();
    } catch (caught) {
      setFeedback(commandFailure(caught));
    } finally {
      setPendingId(null);
    }
  }

  async function complete(item: ActionRecord) {
    if (!caseId) return;
    const source = sources.find((candidate) => candidate.id === completionSourceId);
    if (!source) {
      setFeedback("完成 Action 前请选择一份已导入材料作为完成证据。");
      return;
    }
    setPendingId(item.id);
    setFeedback(null);
    try {
      await completeAction(item.id, {
        expected_version: item.version,
        actor: actor.trim() || "local_user",
        reason: reason.trim() || "Completed locally with the selected imported source",
        evidence: [{
          authority_type: "source",
          authority_id: source.id,
          expected_version: null,
          authority_sha256: source.sha256,
        }],
      });
      await load(caseId);
      await onChanged();
    } catch (caught) {
      setFeedback(commandFailure(caught));
    } finally {
      setPendingId(null);
    }
  }

  async function decideApproval(
    item: ActionRecord,
    approval: ApprovalRecord,
    operation: "approve" | "reject",
  ) {
    if (!caseId) return;
    setPendingId(item.id);
    setFeedback(null);
    try {
      await decideActionApproval(item.id, operation, {
        approval_id: approval.id,
        expected_approval_version: approval.version,
        expected_action_version: item.version,
        actor: "local_user",
        reason: `Workbench ${operation} exact Action version`,
      });
      await load(caseId);
      await onChanged();
    } catch (reason) {
      setFeedback(commandFailure(reason));
    } finally {
      setPendingId(null);
    }
  }

  async function command(
    item: ActionRecord,
    operation: "revalidate" | "start" | "fail" | "cancel",
  ) {
    if (!caseId) return;
    setPendingId(item.id);
    setFeedback(null);
    try {
      await changeActionStatus(item.id, operation, {
        expected_version: item.version,
        actor: "local_user",
        reason: `Workbench ${operation}`,
      });
      await load(caseId);
      await onChanged();
    } catch (reason) {
      setFeedback(
        reason instanceof LifeCaseApiError
          ? reason.message
          : "本地 Action 命令未完成，请重新读取权威状态。",
      );
    } finally {
      setPendingId(null);
    }
  }

  const view = actionView(caseId, state, items.length);
  return (
    <section className="action-board" aria-labelledby="action-title">
      <div className="action-head">
        <div>
          <p className="section-label">ACTION RECORDS</p>
          <h2 id="action-title">本地行动记录</h2>
          {caseTitle && <p>{caseTitle}</p>}
        </div>
        <div className="action-seal">
          <strong>{String(items.length).padStart(2, "0")}</strong>
          <span>条权威行动</span>
          <small>不发送、不提交、不调用外部服务</small>
        </div>
      </div>

      {caseId && (
        <LazyWorkflowTool title="登记本地行动" hint="需要时登记补齐材料行动并进入审批边界">
        <div className="action-composer">
          <div><strong>登记补齐材料行动</strong><span>Risk B · 需要精确版本审批 · 不执行外部发送</span></div>
          <label>
            待补材料
            <select value={evidenceField} onChange={(event) => setEvidenceField(event.target.value)}>
              {evidenceFields.length === 0 && <option value="">模板没有可选材料项</option>}
              {evidenceFields.map((field) => <option key={field} value={field}>{requirementLabel(field)}</option>)}
            </select>
          </label>
          <label>操作人<input value={actor} maxLength={120} onChange={(event) => setActor(event.target.value)} /></label>
          <label>理由<input value={reason} maxLength={1000} onChange={(event) => setReason(event.target.value)} /></label>
          <label>
            完成证据
            <select value={completionSourceId} onChange={(event) => setCompletionSourceId(event.target.value)}>
              {sources.length === 0 && <option value="">先导入材料</option>}
              {sources.map((source) => <option key={source.id} value={source.id}>{source.original_name}</option>)}
            </select>
          </label>
          <button type="button" disabled={pendingId !== null || !evidenceField || !reason.trim()} onClick={() => void createLocalAction()}>登记审批型行动</button>
        </div>
        </LazyWorkflowTool>
      )}

      {feedback && <p className="action-feedback" role="alert">{feedback}</p>}
      {view === "unselected" && (
        <ActionState mark="—" title="尚未选择案件">
          从 Today 台账打开案件；Action 始终保持 Case scope。
        </ActionState>
      )}
      {view === "loading" && (
        <ActionState mark="···" title="正在校验行动图" role="status">
          正在重新校验 Claim、依赖、Deadline、completion evidence 和连续历史。
        </ActionState>
      )}
      {view === "empty" && (
        <ActionState mark="00" title="尚无已提交行动">
          当前没有 Action。系统不会把建议、草稿或浏览器临时状态当作权威行动。
        </ActionState>
      )}
      {view === "offline" && (
        <ActionState mark="OFF" title="本地 API 未连接" role="alert">
          现有行动没有改变；启动 API 后可重新读取。
          <button type="button" onClick={() => caseId && void load(caseId)}>重新连接</button>
        </ActionState>
      )}
      {view === "failure" && (
        <ActionState mark="ERR" title="行动图无法安全读取" role="alert">
          权威引用可能缺失或陈旧；系统已停止展示不完整结果。
          <button type="button" onClick={() => caseId && void load(caseId)}>重新校验</button>
        </ActionState>
      )}
      {view === "populated" && (
        <ol className="action-grid">
          {items.map((item) => (
            <ActionCard
              item={item}
              approval={latestApproval(approvals, item.id)}
              pending={pendingId === item.id}
              onCommand={command}
              onRequestApproval={requestApproval}
              onDecideApproval={decideApproval}
              onComplete={complete}
              key={item.id}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function ActionCard({
  item,
  approval,
  pending,
  onCommand,
  onRequestApproval,
  onDecideApproval,
  onComplete,
}: {
  item: ActionRecord;
  approval: ApprovalRecord | null;
  pending: boolean;
  onCommand: (
    item: ActionRecord,
    operation: "revalidate" | "start" | "fail" | "cancel",
  ) => Promise<void>;
  onRequestApproval: (item: ActionRecord) => Promise<void>;
  onDecideApproval: (
    item: ActionRecord,
    approval: ApprovalRecord,
    operation: "approve" | "reject",
  ) => Promise<void>;
  onComplete: (item: ActionRecord) => Promise<void>;
}) {
  const mutable = ["proposed", "blocked", "ready"].includes(item.status);
  const cancellable = !["done", "cancelled"].includes(item.status);
  return (
    <li className={`action-card action-${item.kind}`}>
      <div className="action-card-head">
        <span className="action-position">{String(item.topological_position + 1).padStart(2, "0")}</span>
        <div>
          <small>{actionKindLabel(item.kind)}</small>
          <strong>{actionStatusLabel(item.status)}</strong>
        </div>
        <span className={`action-risk action-risk-${item.risk_level}`}>Risk {item.risk_level}</span>
      </div>
      <dl className="action-payload">
        {Object.entries(item.payload).map(([key, value]) => (
          <div key={key}><dt>{actionPayloadLabel(key)}</dt><dd>{value}</dd></div>
        ))}
      </dl>
      {item.blockers.length > 0 && (
        <div className="action-blockers">
          <small>BLOCKERS</small>
          {item.blockers.map((blocker, index) => (
            <span key={`${blocker.code}-${index}`}>
              {blocker.field ? requirementLabel(blocker.field) : blocker.code}
              {blocker.authority_id ? ` · ${shortActionAuthority(blocker.authority_id)}` : ""}
            </span>
          ))}
        </div>
      )}
      {item.requirements.length > 0 && (
        <div className="action-authority-list">
          <small>CLAIM BASIS</small>
          {item.requirements.map((basis) => (
            <span key={basis.claim_id} title={basis.claim_id}>
              {requirementLabel(basis.field)} · v{basis.claim_version} · {shortActionAuthority(basis.value_sha256)}
            </span>
          ))}
        </div>
      )}
      {item.dependencies.length > 0 && (
        <div className="action-authority-list">
          <small>DEPENDENCIES</small>
          {item.dependencies.map((dependency) => (
            <span key={dependency.action_id}>
              {shortActionAuthority(dependency.action_id)} · v{dependency.action_version} · {actionStatusLabel(dependency.status)}
            </span>
          ))}
        </div>
      )}
      {item.deadline && (
        <div className="action-deadline">
          <small>DEADLINE LINK</small>
          <strong>{item.deadline.due_value}</strong>
          <span>{item.deadline.date_kind} · {item.deadline.timezone} · v{item.deadline.deadline_version}</span>
        </div>
      )}
      {item.communication_draft && <pre className="action-draft">{item.communication_draft}</pre>}
      {approval && (
        <div className={`approval-card approval-${approval.status}`}>
          <div><small>APPROVAL GATE</small><strong>{approval.status}</strong></div>
          <p>{approval.summary}</p>
          <span>Approval v{approval.version} · Action v{approval.action_version}</span>
          {approval.status === "pending" && (
            <div className="approval-controls">
              <button type="button" disabled={pending} onClick={() => void onDecideApproval(item, approval, "approve")}>批准当前精确版本</button>
              <button type="button" disabled={pending} onClick={() => void onDecideApproval(item, approval, "reject")}>拒绝</button>
            </div>
          )}
        </div>
      )}
      <div className="action-version">
        <code title={item.payload_sha256}>{shortActionAuthority(item.payload_sha256)}</code>
        <span>Action v{item.version}</span>
      </div>
      <div className="action-controls">
        {mutable && <button type="button" disabled={pending} onClick={() => void onCommand(item, "revalidate")}>重新校验</button>}
        {item.status === "ready" && item.risk_level === "A" && (
          <button type="button" disabled={pending} onClick={() => void onCommand(item, "start")}>本地开始</button>
        )}
        {item.status === "ready" && ["B", "C"].includes(item.risk_level) && (!approval || ["rejected", "expired", "invalidated"].includes(approval.status)) && (
          <button type="button" disabled={pending} onClick={() => void onRequestApproval(item)}>申请当前版本审批</button>
        )}
        {item.status === "approved" && item.risk_level === "B" && (
          <button type="button" disabled={pending} onClick={() => void onCommand(item, "start")}>执行已批准行动</button>
        )}
        {item.status === "running" && (
          <>
            <button type="button" disabled={pending} onClick={() => void onComplete(item)}>凭所选材料完成</button>
            <button type="button" disabled={pending} onClick={() => void onCommand(item, "fail")}>记录失败</button>
          </>
        )}
        {cancellable && <button type="button" disabled={pending} onClick={() => void onCommand(item, "cancel")}>取消</button>}
      </div>
    </li>
  );
}

export function approvalSummary(item: ActionRecord): string {
  return [
    `Action kind: ${item.kind}`,
    `Risk: ${item.risk_level}`,
    `Action version: ${item.version}`,
    `Payload SHA-256: ${item.payload_sha256}`,
  ].join("\n");
}

function latestApproval(approvals: ApprovalRecord[], actionId: string): ApprovalRecord | null {
  return approvals.filter((item) => item.action_id === actionId).at(-1) ?? null;
}

function commandFailure(reason: unknown): string {
  return reason instanceof LifeCaseApiError
    ? reason.message
    : "本地权威操作未完成，请重新读取已提交状态。";
}

function ActionState({
  mark,
  title,
  role,
  children,
}: {
  mark: string;
  title: string;
  role?: "status" | "alert";
  children: ReactNode;
}) {
  return (
    <div className="action-state" role={role}>
      <span>{mark}</span>
      <div><h3>{title}</h3><div>{children}</div></div>
    </div>
  );
}
