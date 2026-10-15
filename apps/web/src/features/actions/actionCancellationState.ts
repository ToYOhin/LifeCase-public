import type { ActionRecord } from "./types";

export interface CancellationEdit { actor: string; reason: string }

export function canCancelReviewedAction(item: ActionRecord, read: ActionRecord | null, available: boolean, reviewed: boolean) {
  return available && reviewed && item.kind === "prepare_communication" && Boolean(item.payload.analysis_run_id) &&
    !["done", "cancelled"].includes(item.status) && Boolean(read && read.case_id === item.case_id && read.id === item.id &&
      read.version === item.version && read.payload_sha256 === item.payload_sha256 && read.status === item.status &&
      read.analysis_basis_status === item.analysis_basis_status);
}

export function cancellationStorageKey(caseId: string, actionId: string) {
  return `lifecase.action-cancellation.edit.v1:${encodeURIComponent(caseId)}:${encodeURIComponent(actionId)}`;
}

export function restoreCancellationEdit(raw: string | null): CancellationEdit | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<CancellationEdit>;
    if (typeof value.actor !== "string" || Array.from(value.actor).length > 120 || value.actor.includes("\0") ||
      typeof value.reason !== "string" || Array.from(value.reason).length > 1000 || value.reason.includes("\0")) return null;
    return { actor: value.actor, reason: value.reason };
  } catch { return null; }
}
