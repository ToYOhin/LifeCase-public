import type { LiveAgentResult } from "../agentDemo/types";
import type { ActionRecord } from "./types";

// Navigation identity only. Action versions and consent are always read again.
export interface ActionFollowUpTarget { caseId: string; actionId: string; runId: string }

export function changedActionTarget(item: ActionRecord, available: boolean): ActionFollowUpTarget | null {
  const runId = item.payload.analysis_run_id;
  return available && item.kind === "prepare_communication" && item.analysis_basis_status === "changed" && runId
    ? { caseId: item.case_id, actionId: item.id, runId } : null;
}

export function originalActionQuestion(
  target: ActionFollowUpTarget, caseId: string, result: LiveAgentResult | null, available: boolean,
): string | null {
  if (!available || target.caseId !== caseId || !result || (result.run && result.run.case_id !== caseId)) return null;
  const question = result.run?.id === target.runId ? result.run.analysis?.question
    : result.run_history.find(item => item.id === target.runId)?.question;
  return typeof question === "string" && question.trim() ? question : null;
}
