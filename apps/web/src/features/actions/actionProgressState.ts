import type { ActionProgressRead, ProgressEdit } from "./actionProgressApi";
import type { ActionRecord } from "./types";

export interface CompletionBinding { caseId: string; actionId: string; actionVersion: number; sourceId: string; sourceSha256: string }
export function canRecordProgress(action: ActionRecord, available: boolean) {
  return available && action.kind === "prepare_communication" && Boolean(action.payload.analysis_run_id) &&
    action.analysis_basis_status === "current" && ["blocked", "ready", "approved", "running", "failed"].includes(action.status);
}
export function canCompletePreparedAction(action: ActionRecord, available: boolean, evidence: CompletionBinding | null,
  selectedId: string, selectedSha256: string, reviewed: boolean, unsavedText = "") {
  return canRecordProgress(action, available) && action.status === "running" && !unsavedText.trim() && reviewed && Boolean(evidence &&
    evidence.caseId === action.case_id && evidence.actionId === action.id && evidence.actionVersion === action.version &&
    evidence.sourceId === selectedId && evidence.sourceSha256 === selectedSha256);
}
export function matchesProgressRead(action: ActionRecord, result: ActionProgressRead | null) {
  const read = result?.action;
  return Boolean(read && read.case_id === action.case_id && read.id === action.id && read.version === action.version &&
    read.payload_sha256 === action.payload_sha256 && read.status === action.status && read.analysis_basis_status === action.analysis_basis_status);
}
export function actionProgressStorageKey(caseId: string, actionId: string) { return `lifecase.action-progress.edit.v1:${encodeURIComponent(caseId)}:${encodeURIComponent(actionId)}`; }
export function restoreProgressEdit(raw: string | null): ProgressEdit | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<ProgressEdit>;
    if (!["progress", "result"].includes(value.kind ?? "") || typeof value.text !== "string" || Array.from(value.text).length > 2000 ||
      typeof value.actor !== "string" || value.actor.length > 120 || typeof value.reason !== "string" || value.reason.length > 1000) return null;
    return { kind: value.kind!, text: value.text, actor: value.actor, reason: value.reason };
  } catch { return null; }
}
