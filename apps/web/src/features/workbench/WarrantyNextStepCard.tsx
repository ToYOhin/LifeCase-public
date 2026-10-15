import { useEffect, useState } from "react";

import type { CaseRecord } from "../intake/types";
import type { WorkflowArea } from "./CaseWorkflow";
import { readCaseOverview, type WarrantyOverviewSnapshot, type WarrantyNextStep as NextStep } from "./warrantyNextStep";

interface Props {
  record: CaseRecord;
  refreshKey: number;
  isIdle: boolean;
  importNeedsRead: boolean;
  resolve: (data: WarrantyOverviewSnapshot) => Omit<NextStep, "area"> & { area: WorkflowArea | null };
  onReturn: () => void;
  onSelect: (area: WorkflowArea) => void;
  onOpenField?: (caseId: string, target: NonNullable<NextStep["fieldTarget"]>) => void;
  onOpenAction: (caseId: string, actionId: string) => void;
  onReadImport: () => void;
}

export function WarrantyNextStepCard({ record, refreshKey, isIdle, importNeedsRead, resolve, onReturn, onSelect, onOpenField, onOpenAction, onReadImport }: Props) {
  const [loaded, setLoaded] = useState<{ record: CaseRecord; refreshKey: number; data: WarrantyOverviewSnapshot | null } | null>(null);
  const [retry, setRetry] = useState(0);
  const current = loaded?.record === record && loaded.refreshKey === refreshKey && isIdle && !importNeedsRead ? loaded : null;

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoaded(null);
    if (isIdle && !importNeedsRead) {
      readCaseOverview(record, controller.signal).then(data => {
        if (active && !controller.signal.aborted) setLoaded({ record, refreshKey, data });
      }).catch(() => {
        if (active && !controller.signal.aborted) setLoaded({ record, refreshKey, data: null });
      });
    }
    return () => { active = false; controller.abort(); };
  }, [record, refreshKey, isIdle, importNeedsRead, retry]);

  const step = current?.data ? resolve(current.data) : null;
  const title = importNeedsRead ? "核对材料导入结果" : step?.title ?? (current ? "重新读取下一步依据" : "正在核对下一步");
  const detail = importNeedsRead ? "导入结果尚未确认；先重新读取材料列表，避免重复上传。"
    : step?.detail ?? (current ? "当前事实、缺件或待办未能完整读取；暂不使用旧提示。" : "正在读取当前事实、缺件和已有待办。" );
  const label = importNeedsRead ? "重新读取材料列表" : step?.actionLabel ?? (current ? "重新读取下一步" : "正在核对…");

  function proceed() {
    if (importNeedsRead) { onReadImport(); return; }
    if (!current) return;
    if (!step) { setLoaded(null); setRetry(value => value + 1); return; }
    if ("actionId" in step && step.actionId) { onOpenAction(record.id, step.actionId); return; }
    if (step.fieldTarget && onOpenField) { onOpenField(record.id, step.fieldTarget); return; }
    onSelect(step.area ?? "overview");
    if (step.area === "overview") window.requestAnimationFrame(() => {
      const input = document.querySelector<HTMLInputElement>('.upload-form input[type="file"]');
      input?.focus();
    });
  }

  return <>
    <div className="case-control-next warranty-next-step" aria-live="polite">
      <small>推荐步骤</small><strong>{title}</strong><span>{detail}</span>
    </div>
    <div className="case-control-actions">
      <button type="button" className="secondary-action" onClick={onReturn}>返回案件列表</button>
      <button type="button" disabled={!isIdle || (!importNeedsRead && !current)} onClick={proceed}>{label}</button>
    </div>
  </>;
}
