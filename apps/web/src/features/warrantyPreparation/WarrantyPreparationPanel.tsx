import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import type { ReviewCitation } from "../review/types";
import { getPreparationSnapshot, type PreparationSnapshot } from "./api";
import { downloadPreparation } from "./download";
import { DesiredOutcomeIntake } from "./DesiredOutcomeIntake";
import { buildPreparation, copyPreparation, factBasisLabel, splitCitation, type PreparationField } from "./preparation";
import { usePreparationIntent } from "./usePreparationIntent";
import "./warranty-preparation.css";

interface WarrantyPreparationPanelProps {
  caseId: string;
  refreshKey: number;
  onResolveField?: (field: PreparationField) => void;
  onResolveOutcome?: (mode: "review" | "current") => void;
  onChanged?: () => Promise<void>;
  isCaseOpen?: boolean;
  focusDesiredOutcome?: boolean;
}

interface LoadedSnapshot {
  caseId: string;
  refreshKey: number;
  snapshot: PreparationSnapshot;
}

export function WarrantyPreparationPanel({ caseId, refreshKey, onResolveField, onResolveOutcome, onChanged, isCaseOpen = true, focusDesiredOutcome }: WarrantyPreparationPanelProps) {
  const [loaded, setLoaded] = useState<LoadedSnapshot | null>(null);
  const draft = usePreparationIntent(caseId);
  const intent = draft.input;
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const current = loaded?.caseId === caseId && loaded.refreshKey === refreshKey ? loaded.snapshot : null;
  const preparation = useMemo(() => current ? buildPreparation(current.facts, current.sources, intent) : null, [current, intent]);

  const reload = useCallback(async () => {
    const generation = ++sequence.current;
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    busyRef.current = true;
    setBusy(true);
    setLoaded(null);
    setError(null);
    setFeedback(null);
    try {
      const snapshot = await getPreparationSnapshot(caseId, nextController.signal);
      if (generation !== sequence.current || nextController.signal.aborted) return null;
      setLoaded({ caseId, refreshKey, snapshot });
      return snapshot;
    } catch (reason) {
      if (generation !== sequence.current || nextController.signal.aborted) return null;
      setError(readFailure(reason));
      return null;
    } finally {
      if (generation === sequence.current) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }, [caseId, refreshKey]);

  useEffect(() => {
    void reload();
    const onFocus = () => { if (!busyRef.current) void reload(); };
    window.addEventListener("focus", onFocus);
    return () => {
      ++sequence.current;
      controller.current?.abort();
      window.removeEventListener("focus", onFocus);
    };
  }, [reload]);

  async function deliver(destination: "copy" | "txt") {
    if (!current || !preparation || !intent.trim() || busyRef.current || draft.status !== "ready") return;
    const generation = ++sequence.current;
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    busyRef.current = true;
    setBusy(true);
    setFeedback(`${destination === "copy" ? "复制" : "保存 TXT"}前正在重新核对事实与来源……`);
    setError(null);
    try {
      const fresh = await getPreparationSnapshot(caseId, nextController.signal);
      if (generation !== sequence.current || nextController.signal.aborted) return;
      setLoaded({ caseId, refreshKey, snapshot: fresh });
      if (fresh.signature !== current.signature) {
        setFeedback(`事实或来源依据已变化，预览已更新。请核对后再次点击“${destination === "copy" ? "复制准备单" : "保存本地 TXT"}”。`);
        return;
      }
      if (destination === "txt") {
        const started = downloadPreparation(preparation.text, caseId);
        setFeedback(started
          ? "已发起 TXT 下载，请在浏览器下载列表确认。"
          : "无法发起 TXT 下载，请重试或复制准备单。");
      } else {
        const copied = await copyPreparation(preparation.text, (text) => navigator.clipboard.writeText(text));
        if (generation !== sequence.current || nextController.signal.aborted) return;
        setFeedback(copied
          ? "准备单已复制。尚未发送，案件行动没有改变。"
          : "无法访问剪贴板。请在下方“准备单预览”中手动全选并复制；内容未发送。" );
      }
    } catch (reason) {
      if (generation !== sequence.current || nextController.signal.aborted) return;
      setLoaded(null);
      setFeedback(null);
      setError(readFailure(reason));
    } finally {
      if (generation === sequence.current) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }

  return (
    <section className="warranty-preparation" aria-label="中文报修准备单" aria-busy={busy}>
      <div className="preparation-heading">
        <div>
          <h2>中文报修准备单</h2>
          <p>核对事实和诉求后，复制或保存报修准备单。</p>
        </div>
        <span className="preparation-badge">待本人核对，尚未发送</span>
      </div>
      <label className="preparation-intent">
        本人诉求
        <textarea rows={3} maxLength={2000} value={intent} disabled={busy || draft.busy || !draft.saved} onChange={(event) => {
          draft.update(event.target.value);
          setFeedback(null);
        }} placeholder="例如：请告知检测流程、需提供的材料和可能产生的费用。" />
        <small>仅保存本案件诉求；未保存的修改会在离开后丢失。事实与来源会重新核对。</small>
      </label>
      <div className="preparation-actions preparation-save-actions">
        <button type="button" disabled={busy || draft.status !== "ready" || !draft.dirty} onClick={() => void draft.save()}>保存诉求</button>
        <button type="button" disabled={busy || draft.busy} onClick={() => void draft.readLatest()}>读取最新已保存诉求</button>
        <span aria-live="polite">{draft.busy
          ? draft.status === "saving" ? "正在保存诉求……" : "正在读取已保存诉求……"
          : draft.dirty ? "有未保存修改"
            : draft.saved?.updated_at ? `已保存 · ${new Date(draft.saved.updated_at).toLocaleString("zh-CN")}`
              : draft.saved ? "尚未保存诉求" : "已保存诉求暂不可用"}</span>
      </div>
      <div className="preparation-feedback" aria-live="polite">
        {draft.error && <p role="alert">{draft.error}</p>}
        {draft.message && <p role="status">{draft.message}</p>}
      </div>
      {draft.incoming && (
        <section className="preparation-intent-conflict" aria-label="选择要继续使用的诉求">
          <h3>已保存的诉求</h3>
          <pre>{draft.incoming.intent || "（已保存为空）"}</pre>
          <p>上方“本人诉求”仍是你的输入。选择后才允许再次保存，不会自动覆盖。</p>
          <div className="preparation-actions">
            <button type="button" disabled={busy} onClick={() => draft.chooseIncoming(true)}>使用已保存诉求</button>
            <button type="button" disabled={busy} onClick={() => draft.chooseIncoming(false)}>保留我的输入</button>
          </div>
        </section>
      )}
      <DesiredOutcomeIntake caseId={caseId} facts={current?.facts ?? null} saved={draft.saved}
        ready={isCaseOpen && !busy && draft.status === "ready" && !draft.dirty && !draft.incoming}
        focus={focusDesiredOutcome} onResolve={onResolveOutcome} onChanged={onChanged}
        onRead={async () => {
          const fresh = await reload();
          if (fresh) await draft.readLatest();
          return fresh?.facts ?? null;
        }} />
      <div className="preparation-actions">
        <button type="button" className="preparation-copy" disabled={busy || !preparation || !intent.trim() || draft.status !== "ready"} onClick={() => void deliver("copy")}>复制准备单</button>
        <button type="button" disabled={busy || !preparation || !intent.trim() || draft.status !== "ready"} onClick={() => void deliver("txt")}>保存本地 TXT</button>
        <button type="button" disabled={busy} onClick={() => void reload()}>重新核对事实</button>
        <span>{draft.dirty ? "复制和 TXT 使用当前输入，不会自动保存诉求。" : ""}复制或保存文件不会发送，也不代表行动完成。</span>
      </div>
      <div className="preparation-feedback" aria-live="polite">
        {busy && !feedback && <p role="status">正在读取当前案件事实与来源……</p>}
        {feedback && <p role="status">{feedback}</p>}
        {error && <p role="alert">{error}</p>}
      </div>
      {preparation && (
        <>
          {onResolveField && preparation.fields.some(field => field.next) && <section className="preparation-gaps" aria-label="待补项入口">
            <h3>补齐或核对事实</h3>
            <p>点击进入对应字段，由你选择原文、填写并核实。序列号按售后要求提供；未知时可继续保留待补充。</p>
            <ul>{preparation.fields.filter(field => field.next).map(field => <li key={field.key}>
              <span><strong>{field.label}</strong>：{field.text}</span>
              <button type="button" disabled={busy || draft.busy} onClick={() => onResolveField(field)}>{field.next === "register" ? "补充" : field.next === "review" ? "复核" : "查看"}{field.label}</button>
            </li>)}</ul>
          </section>}
          <label className="preparation-preview">
            准备单预览
            <textarea aria-label="准备单预览" readOnly value={preparation.text} rows={18} spellCheck={false} />
          </label>
          <p className="preparation-note">产品名称按已核实值保留，包含型号时一并显示；不会推测缺失的型号。材料清单不代表售后要求已经满足。</p>
          <details className="preparation-authority">
            <summary>核对事实依据与原文</summary>
            <div className="preparation-facts">
              {preparation.fields.map((field) => (
                <article key={field.key}>
                  <h3>{field.label}</h3>
                  <p>{field.text}</p>
                  {field.facts.map((item) => (
                    <div key={item.claim.id} className="preparation-fact-basis">
                      <p>{factBasisLabel(item)} · {item.confirmation?.actor} · {item.confirmation?.reason}</p>
                      {item.confirmation_basis === "correction" && <p>修正值依据人工记录，不能视为原文逐字内容。</p>}
                      {item.citations.length ? item.citations.map((citation, index) => <SourceExcerpt key={`${citation.segment_id}:${citation.quote_start}:${index}`} citation={citation} />)
                        : <p>没有原文支持引用；依据上述人工记录。</p>}
                    </div>
                  ))}
                </article>
              ))}
            </div>
          </details>
        </>
      )}
    </section>
  );
}

export function SourceExcerpt({ citation }: { citation: ReviewCitation }) {
  const parts = splitCitation(citation);
  return <details className="preparation-source" onToggle={(event) => {
    if (event.currentTarget.open) event.currentTarget.querySelector("mark")?.scrollIntoView({ block: "nearest" });
  }}>
    <summary>{citation.source_name} · {citation.page === null ? "" : `第 ${citation.page} 页 · `}段 {citation.sequence + 1} · 查看原文</summary>
    <p className="preparation-relation">{citation.relation === "supports" ? "支持引用" : citation.relation === "contradicts" ? "矛盾引用" : "上下文引用"}</p>
    <blockquote>{parts.before}<mark>{parts.quote}</mark>{parts.after}</blockquote>
  </details>;
}

function readFailure(reason: unknown): string {
  return reason instanceof LifeCaseApiError ? reason.message
    : "当前事实或材料未能读取，已停止使用旧准备单。请检查本地服务后重新核对。";
}
