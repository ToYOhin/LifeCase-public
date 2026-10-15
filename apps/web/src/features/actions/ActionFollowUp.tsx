import { useEffect, useRef, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import { changeActionStatus, getActions } from "./api";
import { canCancelReviewedAction, cancellationStorageKey, restoreCancellationEdit, type CancellationEdit } from "./actionCancellationState";
import { actionStatusLabel } from "./presenters";
import type { ActionRecord } from "./types";

interface Props {
  item: ActionRecord;
  available: boolean;
  onChanged: () => void | Promise<void>;
  onReviewChangedBasis?: (item: ActionRecord) => void;
}

export function ActionFollowUp({ item, available, onChanged, onReviewChangedBasis }: Props) {
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState<CancellationEdit>(() => readStored(item));
  const [fresh, setFresh] = useState<ActionRecord | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "saving" | "reload-required" | "terminal">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const working = useRef(false);
  const authorityKey = JSON.stringify([item.case_id, item.id, item.version, item.status, item.payload_sha256, item.analysis_basis_status, available]);
  const target = useRef(authorityKey); target.current = authorityKey;
  const terminal = ["done", "cancelled"].includes(item.status) || state === "terminal";

  useEffect(() => {
    generation.current += 1; working.current = false; setReviewed(false); setFresh(null);
    if (!open) { setState("idle"); return; }
    if (["done", "cancelled"].includes(item.status)) { setState("terminal"); setError(null); return; }
    if (!available) {
      setState("reload-required"); setError("旧待办当前无法核对，取消说明已保留。重新读取成功前不能取消。");
    } else void read();
    return () => { generation.current += 1; };
  }, [authorityKey, open]);

  function isCurrent(sequence: number, key: string) { return generation.current === sequence && target.current === key; }

  async function read() {
    if (!available || working.current) return;
    const sequence = ++generation.current; const key = authorityKey;
    working.current = true; setState("loading"); setFresh(null); setReviewed(false); setError(null); setMessage(null);
    try {
      const records = await getActions(item.case_id, (input, init) => fetch(input, { ...init, cache: "no-store" }));
      if (!isCurrent(sequence, key)) return;
      const current = records.find(record => record.id === item.id && record.case_id === item.case_id) ?? null;
      if (!current || records.some(record => record.case_id !== item.case_id)) throw new Error("Case or Action missing");
      if (!canCancelReviewedAction(item, current, true, true)) {
        setState("reload-required"); setError("待办版本、状态或依据已变化，取消说明仍保留。请核对更新后的原待办，不沿用旧勾选。");
        await onChanged(); return;
      }
      setFresh(current); setState("ready");
      setMessage("已重新读取此待办的当前版本。取消只处理这条待办，不修改其正文、历史或其他待办。");
    } catch {
      if (isCurrent(sequence, key)) {
        setFresh(null); setState("reload-required"); setError("旧待办读取失败，取消说明已保留。重新读取成功后才能重新核对取消。");
      }
    } finally { if (isCurrent(sequence, key)) working.current = false; }
  }

  function update(next: CancellationEdit) {
    setEdit(next); setReviewed(false); setMessage(null);
    try { sessionStorage.setItem(cancellationStorageKey(item.case_id, item.id), JSON.stringify(next)); } catch { /* Retain raw edits in this mounted form. */ }
  }

  async function cancel() {
    if (!fresh || !allowed || working.current || !edit.actor.trim() || !edit.reason.trim()) return;
    const sequence = ++generation.current; const key = authorityKey;
    working.current = true; setState("saving"); setReviewed(false); setError(null); setMessage(null);
    try {
      const cancelled = await changeActionStatus(item.id, "cancel", {
        expected_version: fresh.version, actor: edit.actor.trim(), reason: edit.reason.trim(),
      });
      if (!isCurrent(sequence, key)) return;
      if (cancelled.id !== item.id || cancelled.case_id !== item.case_id || cancelled.status !== "cancelled" ||
        cancelled.version !== fresh.version + 1 || cancelled.payload_sha256 !== fresh.payload_sha256) throw new Error("Cancellation response mismatch");
      setFresh(null); setState("terminal");
      setMessage("此待办已明确取消，原正文、处理记录和历史仍保留；其他待办没有改变。");
      try { await onChanged(); } catch {
        if (isCurrent(sequence, key)) setError("取消已确认，但案件列表暂时无法刷新。说明仍保留，请重新读取案件后回看状态。");
      }
    } catch (caught) {
      if (isCurrent(sequence, key)) {
        setFresh(null); setState("reload-required");
        setError(caught instanceof LifeCaseApiError && caught.code.includes("conflict")
          ? "待办已被其他窗口更新，取消说明已保留。请重新读取当前状态并重新核对，不会自动重试。"
          : "取消结果未能确认，说明已保留。请先重新读取确认是否已经取消，不要直接重复提交。");
      }
    } finally { if (isCurrent(sequence, key)) working.current = false; }
  }

  const ready = available && state === "ready";
  const allowed = canCancelReviewedAction(item, fresh, ready, reviewed);
  const busy = state === "loading" || state === "saving";
  return <section className="action-follow-up" aria-label="待办处置">
    {item.analysis_basis_status === "changed" && onReviewChangedBasis && <button type="button" disabled={!available || busy}
      onClick={() => { if (available && !working.current) onReviewChangedBasis(item); }}>查看原问题并重新分析</button>}
    {item.analysis_basis_status === "changed" && !terminal && <p>新待办不替换旧记录，也不继承旧审批。</p>}
    {!terminal && <button type="button" disabled={state === "saving"} onClick={() => {
      generation.current += 1; working.current = false; setFresh(null); setReviewed(false); setOpen(false); setState("idle"); setError(null);
      setMessage("已保留此待办，没有提交状态变更；可继续回看原正文和处理记录。");
    }}>保留此待办</button>}
    <details open={open} onToggle={event => setOpen(event.currentTarget.open)}>
      <summary onClick={event => { if (state === "saving") event.preventDefault(); }}>{terminal ? "查看本标签页暂存说明" : "明确取消此待办"}</summary>
      <div className="action-cancellation-form" aria-busy={busy}>
        <p>当前待办第 {item.version} 版 · {actionStatusLabel(item.status)}</p>
        <details><summary>查看待办编号</summary><code>{item.id}</code></details>
        <p>取消后停止追加记录，已有正文、历史和未保存编辑保留。</p>
        {terminal && <p role="status">此待办已结束。这里仅回看本标签页暂存的说明，不会再次提交或撤销取消；已提交的操作者和理由请到“记录与导出”查看案件记录。</p>}
        <label>取消操作人<input value={edit.actor} maxLength={120} readOnly={terminal} disabled={busy} onChange={event => update({ ...edit, actor: event.target.value })} /></label>
        <label>取消理由<textarea aria-label="取消理由" rows={3} value={edit.reason} maxLength={2000} readOnly={terminal} disabled={busy}
          onChange={event => update({ ...edit, reason: Array.from(event.target.value).slice(0, 1000).join("") })} /></label>
        <small>只暂存当前标签页的取消说明，不保存版本或核对授权。浏览器拒绝暂存时，关闭面板或刷新可能丢失未提交说明。</small>
        {!terminal && <>
          <button type="button" disabled={!available || busy} onClick={() => void read()}>重新读取待办版本</button>
          <label className="action-progress-check"><input type="checkbox" checked={reviewed}
            disabled={!canCancelReviewedAction(item, fresh, ready, true) || busy}
            onChange={event => setReviewed(event.currentTarget.checked)} />我已核对这条待办的当前版本，明确取消它</label>
          <button type="button" disabled={!allowed || !edit.actor.trim() || !edit.reason.trim() || busy} onClick={() => void cancel()}>明确取消此待办</button>
        </>}
        {state === "loading" && <p role="status">正在独立读取旧待办的当前版本…</p>}
      </div>
    </details>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
  </section>;
}

function readStored(item: ActionRecord): CancellationEdit {
  try { return restoreCancellationEdit(sessionStorage.getItem(cancellationStorageKey(item.case_id, item.id))) ?? { actor: "", reason: "" }; }
  catch { return { actor: "", reason: "" }; }
}
