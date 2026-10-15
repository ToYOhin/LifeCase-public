import { useCallback, useEffect, useRef, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import { getCurrentFactsSnapshot } from "../warrantyPreparation/api";
import { factBasisLabel } from "../warrantyPreparation/preparation";
import { SourceExcerpt } from "../warrantyPreparation/WarrantyPreparationPanel";
import type { CurrentFact } from "../warrantyPreparation/types";
import type { CaseTemplateSelector } from "../templates/types";
import { correctReviewClaim } from "./api";
import { correctionInputType, correctionInputValue, parseFieldCorrection } from "./correctionInput";
import { claimValueLabel, fieldLabel } from "./presenters";
import "./current-facts.css";

interface Props {
  caseId: string;
  caseType: CaseTemplateSelector;
  refreshKey: number;
  onChanged: () => Promise<void>;
  focusField?: string;
}

interface Edit {
  item: CurrentFact;
  value: string;
  actor: string;
  reason: string;
}

export function CurrentFactsPanel({ caseId, caseType, refreshKey, onChanged, focusField }: Props) {
  const [loaded, setLoaded] = useState<{ caseId: string; refreshKey: number; items: CurrentFact[] } | null>(null);
  const [edit, setEdit] = useState<Edit | null>(null);
  const [busy, setBusy] = useState(false);
  const [mustReload, setMustReload] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const sequence = useRef(0);
  const busyRef = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const editor = useRef<HTMLFormElement | null>(null);
  const panel = useRef<HTMLElement | null>(null);
  const focused = useRef<string | null>(null);
  const current = loaded?.caseId === caseId && loaded.refreshKey === refreshKey ? loaded : null;

  const reload = useCallback(async () => {
    const generation = ++sequence.current;
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    busyRef.current = true;
    setBusy(true);
    setLoaded(null);
    setEdit(null);
    setError(null);
    try {
      // Reuse the same Case, source and quote validation as the preparation reader.
      const snapshot = await getCurrentFactsSnapshot(caseId, caseType, next.signal);
      if (generation !== sequence.current || next.signal.aborted) return;
      setLoaded({ caseId, refreshKey, items: snapshot.facts.items });
      setMustReload(false);
    } catch {
      if (generation !== sequence.current || next.signal.aborted) return;
      setError("当前事实或来源未能安全读取。请检查本地服务后重新读取，旧值暂不展示。");
    } finally {
      if (generation === sequence.current) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }, [caseId, caseType, refreshKey]);

  useEffect(() => {
    void reload();
    return () => { ++sequence.current; controller.current?.abort(); };
  }, [reload]);

  useEffect(() => { editor.current?.querySelector("input")?.focus(); }, [edit?.item.claim.id]);
  useEffect(() => { focused.current = null; }, [caseId, focusField]);
  useEffect(() => {
    if (!current || !focusField || focused.current === focusField) return;
    const item = Array.from(panel.current?.querySelectorAll<HTMLElement>("[data-field]") ?? []).find(element => element.dataset.field === focusField);
    if (!item) return;
    focused.current = focusField;
    item.focus(); item.scrollIntoView({ block: "center" });
  }, [current, focusField]);

  async function save() {
    if (!edit || !current || busyRef.current || mustReload) return;
    let value;
    try {
      value = parseFieldCorrection(edit.item.claim.field, edit.item.claim.value, edit.value);
      if (!edit.actor.trim() || !edit.reason.trim()) throw new Error("请填写操作人标识和修正理由。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "请检查修正值。");
      return;
    }
    const generation = sequence.current;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await correctReviewClaim(edit.item.claim.id, {
        expected_version: edit.item.claim.version,
        value,
        actor: edit.actor.trim(),
        reason: edit.reason.trim(),
      });
    } catch (caught) {
      if (generation !== sequence.current) return;
      const code = caught instanceof LifeCaseApiError ? caught.code : "network_error";
      const invalid = code === "claim_invalid" || code === "validation_error";
      setMustReload(!invalid);
      setError(code === "claim_version_conflict"
        ? "该事实已有新版本，本次修正未保存。请先重新读取，再核对新值并填写修正。"
        : invalid ? "修正值、操作人或理由不合法，请检查后再保存。"
        : code === "claim_transition_not_allowed" ? "事实状态已变化，本次修正未保存。请重新读取后核对。"
        : "未能确认修正是否保存。输入仍保留；请先重新读取当前值，避免重复提交。");
      busyRef.current = false;
      setBusy(false);
      return;
    }
    if (generation !== sequence.current) return;
    setEdit(null);
    setMessage(caseType === "warranty"
      ? "修正已保存，旧值与修正记录仍保留。到“行动与截止”查看更新后的中文准备单；已保存诉求会保留。"
      : "修正已保存，旧值与修正记录仍保留。请核对更新后的事实及已有行动依据。");
    // Keep editing locked until the parent refresh has settled. Otherwise its late
    // refreshKey update could discard a second edit opened after our own reread.
    try { await onChanged(); } catch {
      if (sequence.current === generation) setMessage("修正已保存，但工作台刷新失败。请重新打开案件核对。");
    }
    if (sequence.current === generation) await reload();
  }

  return <section ref={panel} className="current-facts" aria-label="已核实事实" aria-busy={busy}>
    <div className="current-facts-heading">
      <div><h2>已核实事实</h2></div>
      <button type="button" disabled={busy} onClick={() => { setMessage(null); void reload(); }}>重新读取当前事实</button>
    </div>
    <div aria-live="polite">
      {busy && <p role="status">正在读取或保存，请稍候……</p>}
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
    </div>
    {current && focusField && !current.items.some(item => item.claim.field === focusField && ["confirmed", "corrected"].includes(item.claim.status)) && <p role="status">当前没有{fieldLabel(focusField)}的已核实记录，请在下方登记或复核候选。</p>}
    {current && <CurrentFactsList items={current.items} focusField={focusField} busy={busy || mustReload || edit !== null} onSelect={item => {
      if (busyRef.current || mustReload) return;
      setEdit({ item, value: correctionInputValue(item.claim.field, item.claim.value), actor: "", reason: "" });
      setError(null); setMessage(null);
    }} />}
    {edit && current && <form ref={editor} className="current-fact-editor" aria-label="修正已核实事实" onSubmit={event => { event.preventDefault(); void save(); }}>
      <h3>修正{fieldLabel(edit.item.claim.field)}</h3>
      <p>当前值：{claimValueLabel(edit.item.claim.value)} · 版本 {edit.item.claim.version}</p>
      <p>这是明确的人工修正，不能视为原文逐字内容。原值与操作人、理由会保留在历史中。</p>
      <label>修正值<input type={correctionInputType(edit.item.claim.field, edit.item.claim.value) === "date" ? "date" : correctionInputType(edit.item.claim.field, edit.item.claim.value) === "number" ? "number" : "text"} min={correctionInputType(edit.item.claim.field, edit.item.claim.value) === "date" ? "0001-01-01" : undefined} max={correctionInputType(edit.item.claim.field, edit.item.claim.value) === "date" ? "9999-12-31" : undefined} step={correctionInputType(edit.item.claim.field, edit.item.claim.value) === "number" ? "1" : undefined} required maxLength={1000} disabled={busy} value={edit.value} onChange={event => setEdit({ ...edit, value: event.target.value })} /></label>
      {correctionInputType(edit.item.claim.field, edit.item.claim.value) === "number" && <p>保留原值的金额单位；这里不会换算币种或元、分。</p>}
      <label>操作人标识<input required maxLength={120} disabled={busy} value={edit.actor} onChange={event => setEdit({ ...edit, actor: event.target.value })} /></label>
      <label>修正理由<textarea required maxLength={1000} rows={2} disabled={busy} value={edit.reason} onChange={event => setEdit({ ...edit, reason: event.target.value })} /></label>
      {mustReload && <p>重新读取会关闭本次编辑，请先保留仍需使用的输入。</p>}
      <div className="current-fact-actions"><button type="submit" disabled={busy || mustReload}>保存人工修正</button><button type="button" disabled={busy} onClick={() => { setEdit(null); setError(mustReload ? "编辑已取消，请先重新读取当前事实，再进行修正。" : null); }}>取消修正</button></div>
    </form>}
  </section>;
}

export function CurrentFactsList({ items, busy, onSelect, focusField }: { items: CurrentFact[]; busy: boolean; onSelect: (item: CurrentFact) => void; focusField?: string }) {
  const settled = items.filter(item => ["confirmed", "corrected"].includes(item.claim.status));
  if (!settled.length) return <p>尚无已核实事实，请先在下方登记并确认候选事实。</p>;
  return <div className="current-facts-list">{settled.map(item => <article key={item.claim.id} data-field={item.claim.field} tabIndex={-1} className={focusField === item.claim.field ? "is-targeted" : undefined}>
    <div className="current-fact-heading"><h3>{fieldLabel(item.claim.field)}</h3><span>版本 {item.claim.version}</span></div>
    <p className="current-fact-value">{claimValueLabel(item.claim.value)}</p>
    {item.confirmation && item.confirmation_basis
      ? <>
        <p>{factBasisLabel(item)}</p>
        <details className="current-fact-record">
          <summary>{item.confirmation_basis === "correction" ? "修正记录" : "核实记录"}</summary>
          <p>操作人：{item.confirmation.actor}</p>
          <p>理由：{item.confirmation.reason}</p>
        </details>
      </>
      : <p className="current-fact-warning">核实记录缺失，请重新核实。</p>}
    {item.confirmation_basis === "correction" && <p>修正值依据人工记录，不能视为原文逐字内容。</p>}
    {item.evidence_status === "unavailable" && <p className="current-fact-warning">来源失效，修正不会恢复原文依据。</p>}
    {(item.conflict || item.claim.contradiction) && <p className="current-fact-warning">存在冲突，请核对同字段的各条事实。</p>}
    {!item.usable_as_fact && <p className="current-fact-warning">暂不能作为可靠事实使用，请重新核实依据。</p>}
    {item.citations.map((citation, index) => <SourceExcerpt key={`${citation.segment_id}:${index}`} citation={citation} />)}
    {item.evidence_status === "none" && <p>没有原文支持引用{item.confirmation ? "；依据上述人工记录。" : "。"}</p>}
    {correctionInputType(item.claim.field, item.claim.value) !== "json"
      ? <button type="button" disabled={busy} onClick={() => onSelect(item)}>修正{fieldLabel(item.claim.field)}</button>
      : <p>此字段或值类型暂不支持普通输入修正。</p>}
  </article>)}</div>;
}
