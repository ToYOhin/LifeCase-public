import { useEffect, useId, useState } from "react";
import { HistoryAnswerContent } from "../agentDemo/LiveAgentDossier";
import { readSavedActionAnalysis, type SavedActionAnalysisRead } from "./savedActionAnalysisState";
import type { ActionRecord } from "./types";

export function SavedActionAnalysis({ item, available }: { item: ActionRecord; available: boolean }) {
  const [open, setOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string; read: SavedActionAnalysisRead | null } | null>(null);
  const contentId = useId();
  const key = JSON.stringify([item.case_id, item.id, item.version, item.payload_sha256, item.payload,
    item.analysis_basis_status, available, open, attempt]);
  useEffect(() => {
    const controller = new AbortController();
    setState(null);
    if (open && available && item.payload.analysis_run_id) {
      const request: typeof fetch = (url, init) => fetch(url, { ...init, signal: controller.signal });
      void readSavedActionAnalysis(item, request).then(read => {
        if (!controller.signal.aborted) setState({ key, read });
      }).catch(() => {
        if (!controller.signal.aborted) setState({ key, read: null });
      });
    }
    return () => controller.abort();
  }, [key]);
  const current = state?.key === key ? state : null;
  if (item.kind !== "prepare_communication" || !item.payload.analysis_run_id) return null;
  return <div className="action-source-analysis">
    <div className="action-controls">
      <button type="button" disabled={!available} aria-expanded={open} aria-controls={contentId}
        onClick={() => setOpen(value => !value)}>{open ? "收起来源分析" : "查看来源分析与引用"}</button>
      {open && <button type="button" disabled={!available || !current} onClick={() => setAttempt(value => value + 1)}>重新读取来源分析</button>}
    </div>
    {open && <div id={contentId} className="saved-analysis-content" aria-label="此待办的来源分析" aria-busy={available && !current}
      data-run-id={available ? current?.read?.analysis.id : undefined}>
      {!available ? <p role="status">待办正在重新核对，来源分析暂不展示。</p>
        : !current ? <p role="status">正在读取此待办保存时的分析…</p>
          : !current.read ? <p role="alert">暂时无法定位此待办的原分析。请重新读取；若待办已更新，先重新读取待办。</p>
            : <>
              <p><strong>保存时的分析</strong>{!current.read.isLatest && " · 已有后续分析，以下保留原记录。"}</p>
              {current.read.actionBasisStatus === "unavailable" && <p role="status">待办依据暂时无法核对，以下仅供回看原分析。</p>}
              {current.read.actionBasisStatus === "changed" && current.read.analysis.basis_status !== "changed" && <p role="status">待办依据已变化，以下不是当前可用结论。</p>}
              <HistoryAnswerContent item={current.read.analysis} caseId={item.case_id} />
            </>}
    </div>}
  </div>;
}
