import { getActionProgress } from "./actionProgressApi";
import type { ActionRecord } from "./types";

export type SavedActionCopyResult = "copied" | "changed" | "basis-unavailable" | "read-failed" | "clipboard-failed" | "superseded";

export function hasSavedActionDraft(item: ActionRecord): boolean {
  return item.kind === "prepare_communication" && typeof item.payload?.analysis_run_id === "string" &&
    Boolean(item.payload.analysis_run_id.trim()) && Number.isSafeInteger(item.version) && item.version > 0 &&
    typeof item.communication_draft === "string" && Boolean(item.communication_draft.trim()) &&
    item.communication_draft === item.payload.draft_text;
}

// Copy only the saved text the user saw, after a fresh same-Case authority read.
// The GET snapshot and the system clipboard are not a single transaction.
export async function copySavedActionDraft(
  displayed: ActionRecord,
  isCurrent: () => boolean,
  write: (text: string) => Promise<void>,
  request: typeof fetch = fetch,
): Promise<SavedActionCopyResult> {
  if (!isCurrent()) return "superseded";
  if (!hasSavedActionDraft(displayed) || displayed.analysis_basis_status !== "current") return "basis-unavailable";
  let fresh: ActionRecord;
  try {
    fresh = (await getActionProgress(displayed.case_id, displayed.id, request)).action;
  } catch {
    return isCurrent() ? "read-failed" : "superseded";
  }
  if (!isCurrent()) return "superseded";
  if (fresh.analysis_basis_status !== "current") return "basis-unavailable";
  if (!hasSavedActionDraft(fresh) || fresh.version !== displayed.version || fresh.status !== displayed.status ||
    fresh.payload_sha256 !== displayed.payload_sha256 || fresh.communication_draft !== displayed.communication_draft ||
    Object.keys(fresh.payload).length !== Object.keys(displayed.payload).length ||
    Object.entries(displayed.payload).some(([key, value]) => fresh.payload[key] !== value)) return "changed";
  try {
    await write(fresh.communication_draft!);
    return isCurrent() ? "copied" : "superseded";
  } catch {
    return isCurrent() ? "clipboard-failed" : "superseded";
  }
}
