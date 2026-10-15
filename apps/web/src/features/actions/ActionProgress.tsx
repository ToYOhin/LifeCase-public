import { useEffect, useRef, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import { completeAction } from "./api";
import { getActionProgress, readCompletionSource, saveActionProgress, type ActionProgressRead, type CompletionSource, type ProgressEdit } from "./actionProgressApi";
import { actionProgressStorageKey, canCompletePreparedAction, canRecordProgress, matchesProgressRead, restoreProgressEdit, type CompletionBinding } from "./actionProgressState";
import type { ActionRecord } from "./types";
import "./action-progress.css";

interface Props {
  item: ActionRecord;
  available: boolean;
  sources: Array<{ id: string; original_name: string; sha256: string }>;
  onChanged: () => void | Promise<void>;
}
const emptyEdit: ProgressEdit = { kind: "progress", text: "", actor: "", reason: "" };

export function ActionProgress({ item, available, sources, onChanged }: Props) {
  const [edit, setEdit] = useState<ProgressEdit>(() => readStored(item) ?? emptyEdit);
  const [snapshot, setSnapshot] = useState<ActionProgressRead | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "saving" | "reload-required">("loading");
  const [reviewed, setReviewed] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [evidence, setEvidence] = useState<(CompletionSource & { binding: CompletionBinding }) | null>(null);
  const [sourceState, setSourceState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [completionReviewed, setCompletionReviewed] = useState(false);
  const sequence = useRef(0);
  const sourceSequence = useRef(0);
  const busy = useRef(false);
  const authorityKey = JSON.stringify([item.case_id, item.id, item.version, item.status, item.payload_sha256, item.analysis_basis_status, available]);
  const target = useRef(authorityKey); target.current = authorityKey;
  const selection = useRef(sourceId); selection.current = sourceId;
  const sourcesKey = JSON.stringify(sources.map(source => [source.id, source.sha256]));
  const sourceTarget = useRef(sourcesKey); sourceTarget.current = sourcesKey;

  useEffect(() => {
    sequence.current += 1; sourceSequence.current += 1; busy.current = false;
    setReviewed(false); setCompletionReviewed(false); setEvidence(null); setSourceState("idle");
    if (available) void read();
    else { setState("reload-required"); setError("待办权威暂时无法核对，编辑已保留。重新读取成功后才能保存或完成。"); }
    return () => { sequence.current += 1; sourceSequence.current += 1; };
  }, [authorityKey]);

  useEffect(() => {
    sourceSequence.current += 1; setEvidence(null); setSourceState("idle"); setCompletionReviewed(false);
  }, [sourceId, sourcesKey]);

  function current(generation: number, key: string) { return sequence.current === generation && target.current === key; }
  async function read() {
    if (!available || busy.current) return;
    const generation = ++sequence.current; const key = authorityKey;
    setState("loading"); setReviewed(false); setCompletionReviewed(false); setEvidence(null); setError(null); setMessage(null);
    sourceSequence.current += 1; setSourceState("idle");
    try {
      const next = await getActionProgress(item.case_id, item.id);
      if (!current(generation, key)) return;
      if (!matchesProgressRead(item, next)) {
        setState("reload-required"); setError("待办版本或依据已变化，编辑仍保留。正在重新读取案件中的当前待办，请重新核对。");
        await onChanged(); return;
      }
      setSnapshot(next); setState("ready");
      setMessage(null);
    } catch {
      if (current(generation, key)) { setState("reload-required"); setError("处理记录读取失败，编辑已保留。重新读取成功前不能保存或完成。"); }
    }
  }

  function update(next: ProgressEdit) {
    setEdit(next); setReviewed(false); setCompletionReviewed(false); setMessage(null);
    try { sessionStorage.setItem(actionProgressStorageKey(item.case_id, item.id), JSON.stringify(next)); } catch { /* Raw editing remains in this mounted panel. */ }
  }

  async function save() {
    if (!snapshot || !writable || !reviewed || busy.current || !edit.text.trim() || !edit.actor.trim() || !edit.reason.trim()) return;
    const generation = ++sequence.current; const key = authorityKey;
    busy.current = true; setState("saving"); setReviewed(false); setCompletionReviewed(false); setError(null); setMessage(null);
    sourceSequence.current += 1; setEvidence(null); setSourceState("idle");
    try {
      const next = await saveActionProgress(snapshot, { ...edit, actor: edit.actor.trim(), reason: edit.reason.trim() });
      if (!current(generation, key)) return;
      setSnapshot(next); setState("ready"); update({ ...edit, text: "" });
      setMessage("人工处理记录已保存，待办状态和审批未改变。可在案件记录中回看。");
      await onChanged();
    } catch (caught) {
      if (current(generation, key)) {
        const code = caught instanceof LifeCaseApiError ? caught.code : "";
        setState("reload-required"); setError(code === "action_progress_conflict"
          ? "其他窗口已更新待办或处理记录。编辑已保留，请重新读取最新记录并核对后再保存。"
          : code === "action_progress_stale" ? "待办已关闭或分析依据已变化、无法核对，未保存本条备注，编辑仍保留。请重新读取当前状态；依据变化时先重新分析并另存新待办。"
          : code === "action_progress_invalid" ? "本条备注未保存，编辑已保留。请检查正文长度、操作人和理由格式，重新读取并核对后再保存。"
          : "保存结果未能确认，编辑已保留。请先重新读取，核对是否已保存，不要直接重复提交。");
      }
    } finally { if (current(generation, key)) busy.current = false; }
  }

  async function readSource() {
    const selected = sources.find(source => source.id === sourceId);
    if (!selected || !writable || item.status !== "running" || busy.current) return;
    const generation = ++sourceSequence.current; const key = authorityKey; const sourceKey = sourcesKey;
    setEvidence(null); setCompletionReviewed(false); setSourceState("loading"); setError(null);
    try {
      const result = await readCompletionSource(item.case_id, selected);
      if (sourceSequence.current !== generation || target.current !== key || selection.current !== selected.id || sourceTarget.current !== sourceKey) return;
      setEvidence({ ...result, binding: { caseId: item.case_id, actionId: item.id, actionVersion: item.version,
        sourceId: selected.id, sourceSha256: selected.sha256 } });
      setSourceState("ready");
    } catch {
      if (sourceSequence.current === generation && target.current === key && selection.current === selected.id && sourceTarget.current === sourceKey) {
        setSourceState("error"); setError("所选完成材料原文无法核对，不能沿用先前材料或核对勾选。请重新读取。");
      }
    }
  }

  async function complete() {
    if (!completable || !evidence || busy.current || !edit.actor.trim() || !edit.reason.trim()) return;
    const generation = ++sequence.current; const key = authorityKey;
    busy.current = true; setState("saving"); setReviewed(false); setCompletionReviewed(false); setError(null); setMessage(null);
    try {
      const result = await completeAction(item.id, { expected_version: item.version, actor: edit.actor.trim(), reason: edit.reason.trim(),
        evidence: [{ authority_type: "source", authority_id: evidence.source.id, expected_version: null, authority_sha256: evidence.source.sha256 }] });
      if (!current(generation, key)) return;
      if (result.case_id !== item.case_id || result.id !== item.id || result.status !== "done") throw new Error("Completion response mismatch");
      setState("reload-required"); setEvidence(null); setSourceState("idle");
      setMessage("报修准备待办已按人工核对的证据标记完成；这不代表已发送或维修成功。");
      await onChanged();
    } catch {
      if (current(generation, key)) {
        setState("reload-required"); setEvidence(null); setSourceState("idle");
        setError("完成结果未能确认，编辑已保留。请先重新读取待办状态和记录，再重新核对材料；不会自动重试。");
      }
    } finally { if (current(generation, key)) busy.current = false; }
  }

  const ready = available && state === "ready" && matchesProgressRead(item, snapshot);
  const writable = canRecordProgress(item, ready);
  const selected = sources.find(source => source.id === sourceId);
  const completable = canCompletePreparedAction(item, ready, evidence?.binding ?? null, sourceId, selected?.sha256 ?? "", completionReviewed, edit.text);
  const working = state === "saving";
  const terminal = ["done", "cancelled"].includes(item.status);
  const entries = snapshot?.action.id === item.id && snapshot.action.case_id === item.case_id ? snapshot.entries : [];

  return <section className="action-progress" aria-label="人工处理记录" aria-busy={state === "loading" || working}>
    {item.analysis_basis_status !== "current" && !terminal && <p role="status">依据已变化或无法核对，暂不能保存进展或完成待办。</p>}
    {edit.text.trim() && <p role="status">有未保存的处理记录{terminal ? "，此待办已结束，仅保留查看" : ""}。</p>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <details className="action-progress-details">
      <summary>{terminal ? "查看处理记录" : "记录处理进展"}{entries.length > 0 && <span> · {entries.length} 条</span>}</summary>
      <div className="action-progress-body">
    <p>备注只记录实际处理情况，不会确认事实、审批或完成待办。</p>
    <button type="button" disabled={!available || working || state === "loading"} onClick={() => void read()}>重新读取处理记录</button>
    {state === "loading" && <p role="status">正在读取此待办的当前版本与人工记录…</p>}
    {entries.length > 0 ? <ol className="action-progress-history">{entries.map(entry => <li key={entry.id}>
      <strong>{entry.kind === "result" ? "人工结果备注" : "人工进展"}</strong>
      <p className="action-progress-text">{entry.text}</p>
      <small>{entry.actor} · {entry.created_at} · 当时待办版本 {entry.action_version}</small>
      <p>理由：{entry.reason}</p>
    </li>)}</ol> : ready && <p>尚无人工处理记录。</p>}
    {terminal && <p role="status">此待办已{item.status === "done" ? "完成" : "取消"}，处理记录只读。下方保留的未保存编辑不会自动提交。</p>}
    <label>记录类型<select value={edit.kind} disabled={working || terminal} onChange={event => update({ ...edit, kind: event.target.value as ProgressEdit["kind"] })}>
      <option value="progress">处理进展</option><option value="result">处理结果备注</option>
    </select></label>
    <label>处理记录正文<textarea aria-label="处理记录正文" rows={4} value={edit.text} maxLength={4000} readOnly={terminal} disabled={working}
      onChange={event => update({ ...edit, text: Array.from(event.target.value).slice(0, 2000).join("") })} /></label>
    <small>{Array.from(edit.text).length} / 2000 字 · 仅在本标签页暂存；浏览器禁用暂存时，离开或刷新可能丢失编辑。恢复后须重新核对。</small>
    <label>处理操作人<input value={edit.actor} maxLength={120} readOnly={terminal} disabled={working} onChange={event => update({ ...edit, actor: event.target.value })} /></label>
    <label>处理理由<input value={edit.reason} maxLength={1000} readOnly={terminal} disabled={working} onChange={event => update({ ...edit, reason: event.target.value })} /></label>
    <label className="action-progress-check"><input type="checkbox" checked={reviewed} disabled={!writable || working}
      onChange={event => setReviewed(event.target.checked)} />我已核对此待办当前版本和本条人工备注</label>
    <button type="button" disabled={!writable || !reviewed || !edit.text.trim() || !edit.actor.trim() || !edit.reason.trim() || working}
      onClick={() => void save()}>保存人工处理记录</button>
    {item.status === "running" && <section className="action-completion-review" aria-label="核对准备待办完成证据">
      <h4>核对准备待办完成证据</h4>
      <p>仅将“准备报修草案”待办标记完成，不表示已发送、商家已维修或案件已解决。新导入材料会使旧分析依据变化，需先重新分析并另存新待办。</p>
      <label>选择完成材料<select value={sourceId} disabled={!writable || working} onChange={event => { selection.current = event.target.value; setSourceId(event.target.value); }}>
        <option value="">请明确选择一份已导入材料</option>
        {sources.map(source => <option key={source.id} value={source.id}>{source.original_name}</option>)}
      </select></label>
      <button type="button" disabled={!selected || !writable || working || sourceState === "loading"} onClick={() => void readSource()}>读取所选完成材料原文</button>
      {sourceState === "loading" && <p role="status">正在核对所选材料身份与原文…</p>}
      {evidence && sourceState === "ready" && <div className="action-completion-source">
        <strong>{evidence.source.original_name}</strong>
        {evidence.segments.map(segment => <blockquote key={segment.id}><small>{segment.page !== null && `第 ${segment.page} 页 · `}第 {segment.sequence + 1} 段</small><p>{segment.text}</p></blockquote>)}
      </div>}
      {edit.text.trim() && <p role="status">还有未保存的人工记录正文。请先明确保存人工记录，或清空未保存正文，再核对完成证据；完成操作不会一并保存正文。</p>}
      <label className="action-progress-check"><input type="checkbox" checked={completionReviewed} disabled={!writable || !evidence || sourceState !== "ready" || working || Boolean(edit.text.trim())}
        onChange={event => setCompletionReviewed(event.target.checked)} />我已核对所选原文，确认准备待办已完成；这不代表维修成功</label>
      <button type="button" disabled={!completable || !edit.actor.trim() || !edit.reason.trim() || working}
        onClick={() => void complete()}>凭已核对证据完成准备待办</button>
    </section>}
    {item.completion_evidence.length > 0 && <div><strong>已保存的完成证据</strong>{item.completion_evidence.map(value => <p key={`${value.authority_type}:${value.authority_id}`}>
      {sources.find(source => source.id === value.authority_id)?.original_name ?? value.authority_id} · <code>{value.authority_sha256}</code>
    </p>)}</div>}
      </div>
    </details>
  </section>;
}

function readStored(item: ActionRecord) {
  try { return restoreProgressEdit(sessionStorage.getItem(actionProgressStorageKey(item.case_id, item.id))); } catch { return null; }
}
