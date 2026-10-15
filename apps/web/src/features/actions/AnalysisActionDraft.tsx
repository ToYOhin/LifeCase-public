import { useEffect, useRef, useState } from "react";

import type { AgentRun } from "../agentDemo/types";
import { LifeCaseApiError } from "../intake/api";
import { getAnalysisActionDraft, saveAnalysisActionDraft, type AnalysisActionDraft as Draft } from "./analysisActionDraftApi";
import { analysisDraftStorageKey, canPrepareAnalysisAction, reconcileAnalysisDraft, readRetainedAnalysisEdits, type RetainedAnalysisEdit } from "./analysisActionDraftState";
import { actionStatusLabel } from "./presenters";

interface Props {
  caseId: string;
  run: AgentRun | null;
  available: boolean;
  refreshKey: number;
  historyRunIds: string[];
  onSaved: () => Promise<void>;
  onOpenAction?: (caseId: string, actionId: string) => void;
}

interface Editor {
  caseId: string | null;
  runId: string | null;
  open: boolean;
  input: string | null;
  preview: Draft | null;
  incoming: Draft | null;
  status: "idle" | "reading" | "ready" | "saving" | "reload-required";
  reviewed: boolean;
  error: string | null;
  message: string | null;
}

const empty: Editor = { caseId: null, runId: null, open: false, input: null, preview: null, incoming: null,
  status: "idle", reviewed: false, error: null, message: null };

export function AnalysisActionDraft({ caseId, run, available, refreshKey, historyRunIds, onSaved, onOpenAction }: Props) {
  const [editor, setEditor] = useState<Editor>(empty);
  const [actor, setActor] = useState("local_user");
  const [reason, setReason] = useState("");
  const [retained, setRetained] = useState<{ caseId: string; edits: RetainedAnalysisEdit[] }>({ caseId, edits: [] });
  const knownHistory = useRef<{ caseId: string; runIds: string[] }>({ caseId, runIds: [] });
  const sequence = useRef(0);
  const working = useRef(false);
  const latest = useRef(editor);
  latest.current = editor;
  const target = useRef({ caseId, runId: run?.id ?? null, available });
  target.current = { caseId, runId: run?.id ?? null, available };
  const eligible = run?.case_id === caseId && canPrepareAnalysisAction(run, available);
  const historyKey = JSON.stringify(historyRunIds);

  useEffect(() => {
    if (knownHistory.current.caseId !== caseId) knownHistory.current = { caseId, runIds: [] };
    const known = knownHistory.current;
    const currentEdit = latest.current;
    // A temporary missing Live response cannot revoke the already read history identities.
    known.runIds = [...new Set([...known.runIds, ...historyRunIds])];
    setRetained((previous) => ({ caseId, edits: readRetainedAnalysisEdits(
      caseId, run?.id ?? latest.current.runId, known.runIds,
      (key) => sessionStorage.getItem(key), previous.caseId === caseId ? previous.edits : [],
      currentEdit.caseId === caseId && currentEdit.runId && currentEdit.input !== null && currentEdit.input !== currentEdit.preview?.draft_text
        ? { caseId, runId: currentEdit.runId, text: currentEdit.input } : undefined,
    ) }));
  }, [caseId, run?.id, historyKey, editor.input]);

  useEffect(() => {
    sequence.current += 1;
    working.current = false;
    if (!run || run.case_id !== caseId) {
      setEditor((current) => current.open ? { ...current, status: "reload-required", reviewed: false,
        error: "当前分析暂时无法核对，编辑仍保留。重新读取成功后才能保存。" } : current);
      return;
    }
    const previous = latest.current;
    const sameEditor = previous.caseId === caseId && previous.runId === run.id;
    const stored = sameEditor ? null : readStored(caseId, run.id);
    const current = sameEditor ? previous : {
      ...empty, caseId, runId: run.id, input: stored, open: stored !== null,
    };
    if (current !== previous) { latest.current = current; setEditor(current); }
    if (current.open && eligible) void read(run.id, current);
    else if (current.open) setEditor({ ...current, status: "reload-required", reviewed: false,
      error: "当前分析依据已变化或无法核对。编辑仍保留，请先完成有效的新分析；旧正文不会自动转给新运行。" });
    return () => { sequence.current += 1; };
  }, [caseId, run?.id, run?.version, eligible, refreshKey]);

  function isCurrent(generation: number, runId: string) {
    return sequence.current === generation && target.current.caseId === caseId && target.current.runId === runId && target.current.available;
  }

  async function read(runId: string, previous = latest.current) {
    if (!eligible) return;
    const generation = ++sequence.current;
    working.current = true;
    setEditor({ ...previous, caseId, runId, open: true, status: "reading", incoming: null, reviewed: false, error: null, message: null });
    try {
      const incoming = await getAnalysisActionDraft(caseId, runId);
      if (!isCurrent(generation, runId)) return;
      const reconciled = reconcileAnalysisDraft(previous.input, previous.preview, incoming);
      setEditor({ ...previous, caseId, runId, open: true, input: reconciled.input,
        preview: reconciled.needsChoice ? previous.preview : incoming,
        incoming: reconciled.needsChoice ? incoming : null,
        status: reconciled.needsChoice ? "reload-required" : "ready", reviewed: false, error: null,
        message: reconciled.needsChoice ? "请比较最新内容和保留的编辑，明确选择后再核对保存。" : "已核对当前分析与依据。请审阅短草案后明确保存。" });
    } catch (error) {
      if (!isCurrent(generation, runId)) return;
      setEditor((current) => ({ ...current, status: "reload-required", incoming: null, reviewed: false, error: failure(error, false), message: null }));
    } finally { if (isCurrent(generation, runId)) working.current = false; }
  }

  function update(input: string) {
    if (working.current || !editor.runId) return;
    const bounded = Array.from(input).slice(0, 500).join("");
    writeStored(caseId, editor.runId, bounded);
    setEditor((current) => ({ ...current, input: bounded, reviewed: false, message: null }));
  }

  function choose(useSaved: boolean) {
    if (!editor.incoming || working.current || !eligible) return;
    const input = useSaved ? editor.incoming.draft_text : editor.input;
    if (editor.runId && input !== null) writeStored(caseId, editor.runId, input);
    setEditor((current) => ({ ...current, input, preview: current.incoming, incoming: null,
      status: "ready", reviewed: false, error: null,
      message: useSaved ? "已使用最新内容，尚未执行保存。" : "已保留你的编辑，请重新核对后再保存。" }));
  }

  async function save() {
    if (!eligible || working.current || editor.status !== "ready" || !editor.reviewed || !editor.preview ||
      editor.preview.run_id !== run?.id || !editor.input?.trim() || !actor.trim() || !reason.trim()) return;
    const generation = ++sequence.current;
    const runId = editor.preview.run_id;
    working.current = true;
    setEditor((current) => ({ ...current, status: "saving", reviewed: false, error: null, message: null }));
    try {
      const saved = await saveAnalysisActionDraft(editor.preview, editor.input, actor.trim(), reason.trim());
      if (!isCurrent(generation, runId)) return;
      clearStored(caseId, runId);
      setEditor((current) => ({ ...current, input: saved.draft_text, preview: saved, incoming: null,
        status: "ready", reviewed: false, error: null,
        message: "已保存为案件待办。可在“行动与截止”查看；审批仍需单独操作。" }));
      await onSaved();
    } catch (error) {
      if (!isCurrent(generation, runId)) return;
      setEditor((current) => ({ ...current, status: "reload-required", reviewed: false, incoming: null,
        error: failure(error, true), message: null }));
    } finally { if (isCurrent(generation, runId)) working.current = false; }
  }

  const busy = editor.status === "reading" || editor.status === "saving";
  const bound = eligible && editor.runId === run?.id;
  const mutable = !editor.preview?.saved_action || ["proposed", "blocked", "ready", "approved"].includes(editor.preview.saved_action.status);
  const savable = bound && editor.status === "ready" && !!editor.preview && !editor.incoming && mutable;
  const oldEdits = retained.caseId === caseId ? retained.edits : [];
  if (!eligible && !editor.open && oldEdits.length === 0) return null;

  return <section className="analysis-action-draft" aria-label="报修行动草案" aria-busy={busy}>
    <h3>报修行动草案</h3>
    <p>根据上方当前有效分析整理可审阅短草案。核对引用和正文后，明确保存到本案件待办。</p>
    {!editor.open ? <button type="button" disabled={!eligible} onClick={() => run && void read(run.id)}>
      准备报修行动草案
    </button> : <>
      <label>报修行动短草案<textarea aria-label="报修行动短草案" rows={7} value={editor.input ?? ""} maxLength={1000}
        disabled={busy} onChange={(event) => update(event.currentTarget.value)} /></label>
      <small>{Array.from(editor.input ?? "").length} / 500 字。浏览器允许时，未保存正文暂存在本标签页；刷新后须重新核对，显式保存后才进入案件待办。</small>
      <div className="analysis-action-metadata">
        <label>操作人<input maxLength={120} value={actor} disabled={busy} onChange={(event) => setActor(event.currentTarget.value)} /></label>
        <label>保存理由<input maxLength={1000} value={reason} disabled={busy} onChange={(event) => setReason(event.currentTarget.value)} /></label>
      </div>
      {editor.preview?.saved_action && <p>已保存待办 · 版本 {editor.preview.saved_action.version} · {actionStatusLabel(editor.preview.saved_action.status)}</p>}
      {bound && editor.preview?.saved_action && onOpenAction && <button type="button" disabled={busy || editor.status !== "ready"}
        onClick={() => editor.preview?.saved_action && onOpenAction(caseId, editor.preview.saved_action.id)}>查看已保存待办</button>}
      {editor.preview?.saved_action?.status === "approved" && <p role="status">修改正文并保存后，原精确版本审批将失效，后续仍需另行申请审批。</p>}
      {!mutable && <p role="status">此待办已进入后续状态，不能在这里修改正文。请到“行动与截止”核对原记录。</p>}
      {editor.incoming && <section aria-label="比较最新草案与保留编辑">
        <strong>{editor.incoming.saved_action ? "最新已保存内容" : "当前分析生成的短草案"}</strong>
        <pre>{editor.incoming.draft_text}</pre>
        <div className="analysis-action-controls">
          <button type="button" disabled={busy || !bound} onClick={() => choose(true)}>使用已保存内容</button>
          <button type="button" disabled={busy || !bound} onClick={() => choose(false)}>保留我的编辑</button>
        </div>
      </section>}
      <label className="analysis-action-confirm"><input type="checkbox" checked={editor.reviewed} disabled={!savable || busy}
        onChange={(event) => { const reviewed = event.currentTarget.checked; setEditor((current) => ({ ...current, reviewed })); }} />
        我已核对当前分析、事实引用和草案正文
      </label>
      <div className="analysis-action-controls">
        <button type="button" disabled={!savable || !editor.reviewed || !editor.input?.trim() || !actor.trim() || !reason.trim() || busy}
          onClick={() => void save()}>{editor.status === "saving" ? "正在保存…" : editor.preview?.saved_action ? "保存待办修改" : "保存为案件待办"}</button>
        <button type="button" disabled={!bound || busy} onClick={() => run && void read(run.id)}>重新读取草案依据</button>
      </div>
      {editor.status === "reading" && <p role="status">正在重新核对分析、事实和材料依据…</p>}
      {editor.error && <p role="alert">{editor.error}</p>}
      {editor.message && <p role="status">{editor.message}</p>}
    </>}
    {oldEdits.length > 0 && <details className="retained-analysis-edits">
      <summary>本标签页保留的旧分析编辑</summary>
      <p>以下正文属于旧分析，仅供回看和手动整理，不会自动填入或授权当前草案。</p>
      {oldEdits.map((item, index) => <div key={item.runId}>
        <label>旧分析 {index + 1} 保留的编辑<textarea aria-label={`旧分析 ${index + 1} 保留的编辑`}
          rows={5} readOnly value={item.text} /></label>
        <small>原运行：<code>{item.runId}</code></small>
      </div>)}
    </details>}
  </section>;
}

function failure(error: unknown, saving: boolean): string {
  const code = error instanceof LifeCaseApiError ? error.code : "";
  if (code === "analysis_action_stale") return "分析或事实材料依据已变化，不能沿用旧草案保存。你的编辑已保留，请先重新分析。";
  if (code === "analysis_action_conflict") return "其他窗口已修改待办。你的编辑已保留，请重新读取并比较最新内容。";
  if (code === "analysis_action_invalid") return "草案未保存：正文长度、存储字节上限或内容格式不符合要求。你的编辑已保留，请缩短或检查正文，重新读取并核对后再保存。";
  return saving ? "保存结果未能确认。你的编辑已保留，请先重新读取草案依据，核对是否已经保存后再操作。"
    : "草案依据读取失败。你的编辑已保留，重新读取成功前不能保存。";
}

function readStored(caseId: string, runId: string): string | null {
  try {
    const value = sessionStorage.getItem(analysisDraftStorageKey(caseId, runId));
    return value !== null && !value.includes("\0") && Array.from(value).length <= 500 ? value : null;
  } catch { return null; }
}
function writeStored(caseId: string, runId: string, text: string) {
  try { sessionStorage.setItem(analysisDraftStorageKey(caseId, runId), text); } catch { /* The editor still retains the local input. */ }
}
function clearStored(caseId: string, runId: string) {
  try { sessionStorage.removeItem(analysisDraftStorageKey(caseId, runId)); } catch { /* A restored copy still requires an explicit review. */ }
}
