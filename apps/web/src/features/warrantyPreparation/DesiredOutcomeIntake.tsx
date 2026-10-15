import { useEffect, useRef, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import { desiredOutcomeFitsClaim, registerDesiredOutcome } from "./desiredOutcomeApi";
import type { PreparationIntent } from "./intentApi";
import type { CurrentFacts } from "./types";

interface Props {
  caseId: string;
  facts: CurrentFacts | null;
  saved: PreparationIntent | null;
  ready: boolean;
  focus?: boolean;
  onRead: () => Promise<CurrentFacts | null>;
  onChanged?: () => Promise<void>;
  onResolve?: (mode: "review" | "current") => void;
}

export function DesiredOutcomeIntake({ caseId, facts, saved, ready, focus, onRead, onChanged, onResolve }: Props) {
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [needsRead, setNeedsRead] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const section = useRef<HTMLElement>(null);
  const active = useRef(false);
  const busyRef = useRef(false);
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const identity = useRef(caseId);
  identity.current = caseId;
  const existing = facts?.case_id === caseId ? facts.items.filter(item =>
    item.claim.field === "desired_outcome" && item.claim.status !== "rejected") : [];
  const mode = existing.some(item => ["confirmed", "corrected"].includes(item.claim.status)) ? "current" : "review";
  const tooLong = Boolean(saved && !desiredOutcomeFitsClaim(saved.intent));
  const canRegister = ready && facts?.case_id === caseId && saved?.case_id === caseId && saved.version > 0 &&
    Boolean(saved.intent.trim()) && !tooLong && !existing.length && !needsRead && !busy;

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; ++sequence.current; controller.current?.abort(); };
  }, [caseId]);
  useEffect(() => {
    if (focus) { section.current?.scrollIntoView({ block: "center" }); section.current?.focus({ preventScroll: true }); }
  }, [focus]);

  async function act(readOnly: boolean) {
    if (busyRef.current || (readOnly ? !needsRead : !canRegister || !actor.trim() || !reason.trim())) return;
    const generation = ++sequence.current;
    const nextController = new AbortController();
    controller.current = nextController;
    const isCurrent = () => active.current && identity.current === caseId && sequence.current === generation && !nextController.signal.aborted;
    busyRef.current = true;
    setBusy(true);
    setMessage(null);
    try {
      if (!readOnly && saved) {
        await registerDesiredOutcome(caseId, saved.version, actor.trim(), reason.trim(), nextController.signal);
        if (!isCurrent()) return;
        // A successful write still needs a fresh read; never repeat it on read failure.
        setNeedsRead(true);
      }
      const fresh = await onRead();
      if (!isCurrent()) return;
      if (!fresh || fresh.case_id !== caseId) {
        setNeedsRead(true);
        setMessage("当前期望结果未能读取，请重新读取；不会重复登记。");
        return;
      }
      setNeedsRead(false);
      const found = fresh.items.some(item => item.claim.field === "desired_outcome" && item.claim.status !== "rejected");
      setMessage(found ? "已读到期望结果，请进入复核查看。登记不会自动确认。" : "已核对当前没有期望结果，可由你再次明确登记。");
      if (onChanged && isCurrent()) await onChanged();
    } catch (error) {
      if (!isCurrent()) return;
      // Even a definite conflict needs a read to locate the existing fact/version.
      const definite = error instanceof LifeCaseApiError && !error.retryable &&
        ["claim_invalid", "validation_error", "case_not_found"].includes(error.code);
      setNeedsRead(!definite);
      setMessage(error instanceof LifeCaseApiError ? error.message : "登记结果尚未确认，请先重新读取期望结果。");
    } finally {
      if (isCurrent()) { busyRef.current = false; setBusy(false); }
    }
  }

  return <section ref={section} tabIndex={-1} className="desired-outcome-intake" aria-label="登记期望结果" aria-busy={busy}>
    <h3>期望结果</h3>
    <p>保存诉求只更新准备单。要作为案件事实，先明确登记，再单独确认；本人的诉求无需材料原文。</p>
    {existing.length ? <div className="preparation-actions">
      <span>{mode === "current" ? "已有期望结果，保留原值和历史。" : "已有待核实期望结果，请继续复核。"}</span>
      {onResolve && <button type="button" disabled={busy || !ready || needsRead} onClick={() => onResolve(mode)}>
        {mode === "current" ? "查看当前期望结果" : "去核实期望结果"}</button>}
    </div> : <>
      <p className="desired-outcome-value">已保存诉求：{saved?.version ? saved.intent || "（空白）" : "尚未保存"}</p>
      <div className="desired-outcome-metadata">
        <label>登记人<input maxLength={120} value={actor} disabled={busy} onChange={event => setActor(event.target.value)} /></label>
        <label>登记理由<input maxLength={1000} value={reason} disabled={busy} onChange={event => setReason(event.target.value)} placeholder="说明为何将这份已保存诉求作为期望结果" /></label>
      </div>
      <div className="preparation-actions"><button type="button" disabled={!canRegister || !actor.trim() || !reason.trim()}
        onClick={() => void act(false)}>将已保存诉求登记为待核实期望结果</button></div>
      {tooLong && <p role="alert">已保存诉求过长，无法登记为一项事实；请缩短本人诉求后重新保存。原诉求仍保留。</p>}
      {!ready && <p>请先完成保存或读取，核对当前诉求后再登记。</p>}
    </>}
    {needsRead && <div className="preparation-actions"><button type="button" disabled={busy} onClick={() => void act(true)}>重新读取期望结果</button></div>}
    <div className="preparation-feedback" aria-live="polite">{message && <p role="status">{message}</p>}</div>
  </section>;
}
