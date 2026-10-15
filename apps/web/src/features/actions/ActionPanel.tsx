import { type ReactNode, useEffect, useRef, useState } from "react";

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
  actionReadingGroups,
  actionStatusLabel,
  actionView,
  canAdvanceAnalysisAction,
  shortActionAuthority,
} from "./presenters";
import type { ActionCreateInput, ActionLoadState, ActionRecord, ApprovalRecord } from "./types";
import { LazyWorkflowTool } from "../workbench/LazyWorkflowTool";
import { ActionProgress } from "./ActionProgress";
import { ActionFollowUp } from "./ActionFollowUp";
import { SavedActionCopy } from "./SavedActionCopy";
import { SavedActionAnalysis } from "./SavedActionAnalysis";
import { LocalActionComposer, localActionInput, localActionPayloadValue, matchesLocalAction, type LocalActionBasis, type LocalActionFields } from "./LocalActionComposer";
import { getCurrentFactsSnapshot } from "../warrantyPreparation/api";

interface ActionPanelProps {
  caseId: string | null;
  caseTitle: string | null;
  caseType: string;
  requiredClaimFields: string[] | null;
  refreshKey: number;
  onChanged: () => void | Promise<void>;
  sources: Array<{ id: string; original_name: string; sha256: string }>;
  evidenceFields: string[];
  onReviewChangedBasis?: (item: ActionRecord) => void;
  focusActionId?: string;
  onReviewFacts?: () => void;
}

export function ActionPanel({
  caseId,
  caseTitle,
  caseType,
  requiredClaimFields,
  refreshKey,
  onChanged,
  sources,
  evidenceFields,
  onReviewChangedBasis,
  focusActionId,
  onReviewFacts,
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
  const [loadedCaseId, setLoadedCaseId] = useState<string | null>(null);
  const [localAction, setLocalAction] = useState<Pick<LocalActionFields, "kind" | "intent" | "recipientRole" | "purpose" | "outcome">>({
    kind: "collect_evidence", intent: "request_information", recipientRole: "merchant", purpose: "", outcome: "",
  });
  const [unknownCreate, setUnknownCreate] = useState<{
    caseId: string; input: ActionCreateInput; knownIds: string[]; candidates: ActionRecord[] | null;
  } | null>(null);
  const [createdInput, setCreatedInput] = useState<{ caseId: string; input: ActionCreateInput; actionId: string } | null>(null);
  const createWorking = useRef(false);
  const [loadedBasis, setLoadedBasis] = useState<LocalActionBasis | null>(null);
  const factController = useRef<AbortController | null>(null);
  const requiredFieldsKey = JSON.stringify(requiredClaimFields);
  const loadSequence = useRef(0);
  const targetCase = useRef(caseId); targetCase.current = caseId;
  const mounted = useRef(true);
  const board = useRef<HTMLElement | null>(null);
  const focusedAction = useRef<string | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; loadSequence.current += 1; factController.current?.abort(); };
  }, []);

  useEffect(() => {
    if (!focusActionId) { focusedAction.current = null; return; }
    if (state !== "ready" || loadedCaseId !== caseId || !items.some(item => item.id === focusActionId && item.case_id === caseId)) return;
    const identity = `${caseId}:${focusActionId}`;
    if (focusedAction.current === identity) return;
    const card = Array.from(board.current?.querySelectorAll<HTMLElement>("[data-action-id]") ?? []).find(element => element.dataset.actionId === focusActionId);
    if (card) { focusedAction.current = identity; card.scrollIntoView({ block: "start" }); card.focus({ preventScroll: true }); }
  }, [caseId, loadedCaseId, items, state, focusActionId]);

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
    return () => { loadSequence.current += 1; };
  }, [caseId, caseType, requiredFieldsKey, refreshKey]);

  async function load(targetCaseId: string) {
    if (!mounted.current || targetCase.current !== targetCaseId) return null;
    const sequence = ++loadSequence.current;
    factController.current?.abort();
    const nextFacts = new AbortController();
    factController.current = nextFacts;
    setLoadedBasis(null);
    setState("loading");
    setFeedback(null);
    try {
      const [nextItems, nextApprovals, snapshot] = await Promise.all([
        getActions(targetCaseId),
        getApprovals(targetCaseId),
        requiredClaimFields === null ? Promise.resolve(null)
          : getCurrentFactsSnapshot(targetCaseId, caseType, nextFacts.signal).catch(() => null),
      ]);
      if (!mounted.current || loadSequence.current !== sequence || targetCase.current !== targetCaseId) return null;
      if (nextItems.some(item => item.case_id !== targetCaseId) || nextApprovals.some(item => item.case_id !== targetCaseId)) throw new Error("Case authority mismatch");
      setItems(nextItems);
      setApprovals(nextApprovals);
      setLoadedCaseId(targetCaseId);
      setLoadedBasis({ caseId: targetCaseId, caseType, requiredClaimFields, facts: snapshot?.facts ?? null });
      setState("ready");
      return nextItems;
    } catch (reason) {
      if (!mounted.current || loadSequence.current !== sequence || targetCase.current !== targetCaseId) return null;
      setState(
        reason instanceof LifeCaseApiError && reason.code === "network_error"
          ? "offline"
          : "error",
      );
      return null;
    }
  }

  async function requestApproval(item: ActionRecord) {
    if (!caseId || item.case_id !== caseId || state !== "ready" || pendingId !== null) return;
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
      if (!mounted.current || targetCase.current !== caseId) return;
      await load(caseId);
      if (!mounted.current || targetCase.current !== caseId) return;
      await onChanged();
    } catch (reason) {
      if (!mounted.current || targetCase.current !== caseId) return;
      setFeedback(commandFailure(reason));
    } finally {
      if (mounted.current && targetCase.current === caseId) setPendingId(null);
    }
  }

  async function createLocalAction() {
    if (!caseId || state !== "ready" || loadedCaseId !== caseId || pendingId !== null || createWorking.current ||
      unknownCreate?.caseId === caseId) return;
    let input: ActionCreateInput;
    try { input = localActionInput({ ...localAction, evidenceField, actor, reason }, evidenceFields, currentBasis); }
    catch (caught) { setFeedback(caught instanceof Error ? caught.message : "请核对登记内容。"); return; }
    if (createdInput?.caseId === caseId && JSON.stringify(createdInput.input) === JSON.stringify(input)) return;
    const knownIds = items.map(item => item.id);
    createWorking.current = true;
    setPendingId("create");
    setFeedback(null);
    let received = false;
    try {
      const saved = await createAction(caseId, input);
      if (!mounted.current || targetCase.current !== caseId) return;
      if (!saved.id || !matchesLocalAction(saved, caseId, input)) throw new Error("Returned Action authority mismatch");
      received = true;
      setCreatedInput({ caseId, input, actionId: saved.id });
      const refreshed = await load(caseId);
      if (!mounted.current || targetCase.current !== caseId) return;
      setFeedback(refreshed ? "本地行动已登记；输入仍保留。请在下方核对待办，精确版本审批仍需单独操作。"
        : "本地行动已登记，但列表暂时未能读取。输入仍保留，请重新读取，不要重复登记。");
      await onChanged();
    } catch (caught) {
      if (!mounted.current || targetCase.current !== caseId) return;
      if (!received && (!(caught instanceof LifeCaseApiError) || caught.retryable ||
        ["network_error", "unexpected_response"].includes(caught.code))) {
        setUnknownCreate({ caseId, input, knownIds, candidates: null });
        setFeedback("保存结果不明，输入仍保留。请先只读重新读取待办并核对，当前不会再次登记。");
      } else if (!received && caught instanceof LifeCaseApiError && ["action_claim_stale", "action_claim_invalid"].includes(caught.code)) {
        setLoadedBasis({ caseId, caseType, requiredClaimFields, facts: null });
        setFeedback("事实依据已变化或无法核对。编辑保留，请重新读取事实并核对后再登记。");
      } else setFeedback(commandFailure(caught));
    } finally {
      if (mounted.current && targetCase.current === caseId) { createWorking.current = false; setPendingId(null); }
    }
  }

  async function readUnknownCreate() {
    if (!caseId || unknownCreate?.caseId !== caseId || pendingId !== null || createWorking.current) return;
    const pending = unknownCreate;
    createWorking.current = true;
    setPendingId("read-create");
    try {
      const refreshed = await load(caseId);
      if (!mounted.current || targetCase.current !== caseId) return;
      if (!refreshed) { setFeedback("待办未能安全读取，登记仍被锁定，输入仍保留。"); return; }
      const candidates = refreshed.filter(item => !pending.knownIds.includes(item.id) && matchesLocalAction(item, caseId, pending.input));
      setUnknownCreate({ ...pending, candidates });
      setFeedback(candidates.length ? "已读取到相同内容的待办，请核对后明确选择本次记录；不会再次登记。"
        : "本次读取未找到新建的相同内容待办；这不保证迟到请求不会保存，请稍后读取或明确核对后再决定。");
    } finally {
      if (mounted.current && targetCase.current === caseId) { createWorking.current = false; setPendingId(null); }
    }
  }

  function confirmUnknownCreate(candidate: ActionRecord) {
    if (!caseId || unknownCreate?.caseId !== caseId || createWorking.current || pendingId !== null ||
      state !== "ready" || loadedCaseId !== caseId ||
      !items.some(item => item.id === candidate.id && matchesLocalAction(item, caseId, unknownCreate.input))) return;
    setCreatedInput({ caseId, input: unknownCreate.input, actionId: candidate.id });
    setUnknownCreate(null);
    setFeedback("你已核对本次待办，未重复登记。输入仍保留，后续审批与完成仍需单独操作。");
  }

  function updateLocalAction(updates: Partial<LocalActionFields>) {
    const { actor: nextActor, reason: nextReason, evidenceField: nextField, ...changes } = updates;
    if (nextActor !== undefined) setActor(nextActor);
    if (nextReason !== undefined) setReason(nextReason);
    if (nextField !== undefined) setEvidenceField(nextField);
    setLocalAction(current => ({ ...current, ...changes }));
  }

  async function complete(item: ActionRecord) {
    if (!caseId || item.case_id !== caseId || state !== "ready" || pendingId !== null || item.payload.analysis_run_id) return;
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
      if (!mounted.current || targetCase.current !== caseId) return;
      await load(caseId);
      if (!mounted.current || targetCase.current !== caseId) return;
      await onChanged();
    } catch (caught) {
      if (!mounted.current || targetCase.current !== caseId) return;
      setFeedback(commandFailure(caught));
    } finally {
      if (mounted.current && targetCase.current === caseId) setPendingId(null);
    }
  }

  async function decideApproval(
    item: ActionRecord,
    approval: ApprovalRecord,
    operation: "approve" | "reject",
  ) {
    if (!caseId || item.case_id !== caseId || state !== "ready" || pendingId !== null) return;
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
      if (!mounted.current || targetCase.current !== caseId) return;
      await load(caseId);
      if (!mounted.current || targetCase.current !== caseId) return;
      await onChanged();
    } catch (reason) {
      if (!mounted.current || targetCase.current !== caseId) return;
      setFeedback(commandFailure(reason));
    } finally {
      if (mounted.current && targetCase.current === caseId) setPendingId(null);
    }
  }

  async function command(
    item: ActionRecord,
    operation: "revalidate" | "start" | "fail" | "cancel",
  ) {
    if (!caseId || item.case_id !== caseId || state !== "ready" || pendingId !== null) return;
    if (operation === "cancel" && item.payload.analysis_run_id) return;
    setPendingId(item.id);
    setFeedback(null);
    try {
      await changeActionStatus(item.id, operation, {
        expected_version: item.version,
        actor: "local_user",
        reason: `Workbench ${operation}`,
      });
      if (!mounted.current || targetCase.current !== caseId) return;
      await load(caseId);
      if (!mounted.current || targetCase.current !== caseId) return;
      await onChanged();
    } catch (reason) {
      if (!mounted.current || targetCase.current !== caseId) return;
      setFeedback(
        reason instanceof LifeCaseApiError
          ? reason.message
          : "本地 Action 命令未完成，请重新读取权威状态。",
      );
    } finally {
      if (mounted.current && targetCase.current === caseId) setPendingId(null);
    }
  }

  const currentItems = loadedCaseId === caseId ? items : [];
  const view = actionView(caseId, state, currentItems.length);
  const readingGroups = actionReadingGroups(currentItems);
  const localFields = { ...localAction, evidenceField, actor, reason };
  const currentBasis = loadedBasis?.caseId === caseId && loadedBasis.caseType === caseType &&
    JSON.stringify(loadedBasis.requiredClaimFields) === requiredFieldsKey && state === "ready" && loadedCaseId === caseId ? loadedBasis : null;
  let sameCreatedInput = false;
  try { sameCreatedInput = createdInput?.caseId === caseId && JSON.stringify(createdInput.input) === JSON.stringify(localActionInput(localFields, evidenceFields, currentBasis)); }
  catch { /* Keep incomplete edits. */ }
  return (
    <section className="action-board" aria-labelledby="action-title" ref={board}>
      <div className="action-head">
        <div>
          <h2 id="action-title">案件待办</h2>
          {caseTitle && <p>{caseTitle}</p>}
        </div>
        <div className="action-seal">
          <strong>{String(currentItems.length).padStart(2, "0")}</strong>
          <span>条待办 · 由你决定如何处理</span>
        </div>
      </div>

      {caseId && (
        <LazyWorkflowTool title="登记本地行动" hint="补材料、沟通、跟进或记录结果">
          <LocalActionComposer fields={localFields} evidenceFields={evidenceFields} basis={currentBasis} onReviewFacts={onReviewFacts}
            onReadFacts={() => { if (caseId && pendingId === null) void load(caseId); }}
            available={state === "ready" && loadedCaseId === caseId && unknownCreate?.caseId !== caseId && !sameCreatedInput}
            busy={pendingId !== null} onChange={updateLocalAction} onCreate={() => void createLocalAction()} />
          {sameCreatedInput && <p role="status">相同登记内容已保存为待办；请核对已有记录，修改内容后才可登记另一条。</p>}
          {unknownCreate?.caseId === caseId && <div role="status">
            <p>保存结果不明；本次输入仍保留，重新读取只获取待办，不会重复保存。</p>
            <p>本次登记：{actionKindLabel(unknownCreate.input.kind)} · {Object.values(unknownCreate.input.payload).join(" / ")}</p>
            <button type="button" disabled={pendingId !== null} onClick={() => void readUnknownCreate()}>只读重新读取待办</button>
            {unknownCreate.candidates?.map(candidate => <div key={candidate.id}>
              <p>{actionKindLabel(candidate.kind)} · {actionStatusLabel(candidate.status)} · {candidate.id}</p>
              <button type="button" disabled={pendingId !== null || state !== "ready"}
                onClick={() => confirmUnknownCreate(candidate)}>确认这条是本次登记</button>
            </div>)}
            {unknownCreate.candidates?.length === 0 && <button type="button" disabled={pendingId !== null || state !== "ready"}
              onClick={() => { setUnknownCreate(null); setFeedback("你已明确核对未保存。输入保留，只有再次点击登记才会创建待办。"); }}>
              我已核对未保存，允许重新登记
            </button>}
          </div>}
          <label>
            完成证据
            <select value={completionSourceId} onChange={(event) => setCompletionSourceId(event.target.value)}>
              {sources.length === 0 && <option value="">先导入材料</option>}
              {sources.map((source) => <option key={source.id} value={source.id}>{source.original_name}</option>)}
            </select>
          </label>
        </LazyWorkflowTool>
      )}

      {feedback && <p className="action-feedback" role="alert">{feedback}</p>}
      {view === "unselected" && (
        <ActionState mark="—" title="尚未选择案件">
          从 Today 台账打开案件；Action 始终保持 Case scope。
        </ActionState>
      )}
      {view === "loading" && (
        <ActionState mark="···" title="正在读取待办" role="status">
          正在核对最新状态与依据。
        </ActionState>
      )}
      {view === "empty" && (
        <ActionState mark="00" title="还没有待办">
          可登记补齐材料、准备沟通、跟进或结果待办。
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
          权威引用可能缺失或陈旧；已读记录和编辑保留，重新校验成功前不能操作。
          <button type="button" onClick={() => caseId && void load(caseId)}>重新校验</button>
        </ActionState>
      )}
      {currentItems.length > 0 && (
        <>
        <p className="action-reading-summary">
          {state !== "ready" && <strong>上次读取的分区，恢复核对前不能操作。 </strong>}
          {readingGroups.map(group => `${group.title} ${group.items.length} 条`).join(" · ")}
        </p>
        <ol className="action-grid" aria-label="按处理顺序排列的待办与历史记录">
          {/* Keep keyed cards under one parent so moving sections preserves in-memory edits. */}
          {readingGroups.flatMap(group => [
            <li className="action-reading-heading" role="presentation" key={`heading:${group.id}`} data-action-group={group.id}>
              <h3>{group.title}{state !== "ready" ? "（上次读取）" : ""}<span>{group.items.length} 条</span></h3>
              <p>{group.description}</p>
            </li>,
            ...group.items.map((item) => (
            <ActionCard
              item={item}
              approval={latestApproval(approvals, item.id)}
              pending={pendingId !== null || state !== "ready"}
              onCommand={command}
              onRequestApproval={requestApproval}
              onDecideApproval={decideApproval}
              onComplete={complete}
              sources={sources}
              available={state === "ready" && pendingId === null}
              onChanged={onChanged}
              onReviewChangedBasis={onReviewChangedBasis}
              onReload={() => { if (caseId) void load(caseId); }}
              key={item.id}
            />
            )),
          ])}
        </ol>
        </>
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
  sources,
  available,
  onChanged,
  onReviewChangedBasis,
  onReload,
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
  sources: Array<{ id: string; original_name: string; sha256: string }>;
  available: boolean;
  onChanged: () => void | Promise<void>;
  onReviewChangedBasis?: (item: ActionRecord) => void;
  onReload: () => void;
}) {
  const currentBasis = canAdvanceAnalysisAction(item);
  const mutable = currentBasis && ["proposed", "blocked", "ready"].includes(item.status);
  const cancellable = !["done", "cancelled"].includes(item.status);
  const analysisBound = Boolean(item.payload.analysis_run_id);
  return (
    <li className={`action-card action-${item.kind}`} data-action-id={item.id} tabIndex={-1}>
      <div className="action-card-head">
        <span className="action-position">{String(item.topological_position + 1).padStart(2, "0")}</span>
        <div>
          <small>{actionKindLabel(item.kind)}</small>
          <strong>{actionStatusLabel(item.status)}</strong>
        </div>
        <span className={`action-risk action-risk-${item.risk_level}`} title={`风险等级 ${item.risk_level}`}>{item.risk_level === "A" ? "本地操作" : "审批类操作"}</span>
      </div>
      <dl className="action-payload">
        {Object.entries(item.payload).filter(([key]) => !analysisBound || (key !== "draft_text" && !key.startsWith("analysis_"))).map(([key, value]) => (
          <div key={key}><dt>{actionPayloadLabel(key)}</dt><dd>{localActionPayloadValue(key, value, analysisBound)}</dd></div>
        ))}
      </dl>
      {analysisBound && <p role={currentBasis ? undefined : "status"}>
        {item.analysis_basis_status === "changed" ? (cancellable ? "依据已变化，请先重新分析；此待办暂不能推进或审批。" : "历史依据已变化，原正文与处理记录仍保留。")
          : !currentBasis ? "依据暂时无法核对，原正文保留；暂不能推进或审批。"
            : "按已核对的分析保存，审批只对当前版本有效。"}
      </p>}
      {item.blockers.length > 0 && (
        <div className="action-blockers">
          <small>还需处理</small>
          {item.blockers.map((blocker, index) => (
            <span key={`${blocker.code}-${index}`}>
              {blocker.field ? requirementLabel(blocker.field) : blocker.code}
              {blocker.authority_id ? ` · ${shortActionAuthority(blocker.authority_id)}` : ""}
            </span>
          ))}
        </div>
      )}
      {(item.requirements.length > 0 || item.dependencies.length > 0) && <details className="action-authority-details">
      <summary>查看事实与关联待办</summary>
      {item.requirements.length > 0 && (
        <div className="action-authority-list">
          <small>事实依据</small>
          {item.requirements.map((basis) => (
            <span key={basis.claim_id} title={basis.claim_id}>
              {requirementLabel(basis.field)} · v{basis.claim_version} · {shortActionAuthority(basis.value_sha256)}
            </span>
          ))}
        </div>
      )}
      {item.dependencies.length > 0 && (
        <div className="action-authority-list">
          <small>关联待办</small>
          {item.dependencies.map((dependency) => (
            <span key={dependency.action_id}>
              {shortActionAuthority(dependency.action_id)} · v{dependency.action_version} · {actionStatusLabel(dependency.status)}
            </span>
          ))}
        </div>
      )}
      </details>}
      {item.deadline && (
        <div className="action-deadline">
          <small>关联日期</small>
          <strong>{item.deadline.due_value}</strong>
          <span>{item.deadline.date_kind} · {item.deadline.timezone} · v{item.deadline.deadline_version}</span>
        </div>
      )}
      {item.communication_draft && <pre className="action-draft">{item.communication_draft}</pre>}
      {analysisBound && <SavedActionCopy item={item} available={available} onReload={onReload} />}
      {analysisBound && <SavedActionAnalysis item={item} available={available} />}
      {analysisBound && <details className="action-analysis-details">
        <summary>查看依据标识</summary>
        <dl>{Object.entries(item.payload).filter(([key]) => key.startsWith("analysis_")).map(([key, value]) =>
          <div key={key}><dt>{actionPayloadLabel(key)}</dt><dd><code>{value}</code></dd></div>)}</dl>
      </details>}
      {approval && (
        <div className={`approval-card approval-${approval.status}`}>
          <div><small>审批</small><strong>{{ pending: "待审批", approved: "已批准", rejected: "已拒绝", expired: "已过期", invalidated: "已失效" }[approval.status]}</strong></div>
          <p>{approval.summary}</p>
          <span>审批第 {approval.version} 版 · 对应待办第 {approval.action_version} 版</span>
          {currentBasis && approval.status === "pending" && (
            <div className="approval-controls">
              <button type="button" disabled={pending} onClick={() => void onDecideApproval(item, approval, "approve")}>批准当前精确版本</button>
              <button type="button" disabled={pending} onClick={() => void onDecideApproval(item, approval, "reject")}>拒绝</button>
            </div>
          )}
        </div>
      )}
      <details className="action-version-details"><summary>第 {item.version} 版 · 记录详情</summary><div className="action-version">
        <code title={item.payload_sha256}>{shortActionAuthority(item.payload_sha256)}</code>
        <span>内容校验标识</span>
      </div></details>
      <div className="action-controls">
        {mutable && <button type="button" disabled={pending} onClick={() => void onCommand(item, "revalidate")}>重新校验</button>}
        {currentBasis && item.status === "ready" && item.risk_level === "A" && (
          <button type="button" disabled={pending} onClick={() => void onCommand(item, "start")}>本地开始</button>
        )}
        {currentBasis && item.status === "ready" && ["B", "C"].includes(item.risk_level) && (!approval || ["rejected", "expired", "invalidated"].includes(approval.status)) && (
          <button type="button" disabled={pending} onClick={() => void onRequestApproval(item)}>申请当前版本审批</button>
        )}
        {currentBasis && item.status === "approved" && item.risk_level === "B" && (
          <button type="button" disabled={pending} onClick={() => void onCommand(item, "start")}>执行已批准行动</button>
        )}
        {currentBasis && item.status === "running" && (
          <>
            {!analysisBound && <button type="button" disabled={pending} onClick={() => void onComplete(item)}>凭所选材料完成</button>}
            <button type="button" disabled={pending} onClick={() => void onCommand(item, "fail")}>记录失败</button>
          </>
        )}
        {cancellable && !analysisBound && <button type="button" disabled={pending} onClick={() => void onCommand(item, "cancel")}>取消</button>}
      </div>
      {analysisBound && <ActionFollowUp key={`follow-up:${item.case_id}:${item.id}`} item={item} available={available} onChanged={onChanged} onReviewChangedBasis={onReviewChangedBasis} />}
      {analysisBound && <ActionProgress key={`${item.case_id}:${item.id}`} item={item} available={available} sources={sources} onChanged={onChanged} />}
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
