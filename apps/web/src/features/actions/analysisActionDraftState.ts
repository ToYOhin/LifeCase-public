import type { AgentRun } from "../agentDemo/types";
import type { AnalysisActionDraft } from "./analysisActionDraftApi";

export function canPrepareAnalysisAction(run: AgentRun | null, available: boolean): boolean {
  return Boolean(available && run?.id.startsWith("portfolio-live-q1-") && run.status === "completed" &&
    run.answer?.status === "answered" && run.analysis?.basis_status === "current");
}

export function analysisDraftStorageKey(caseId: string, runId: string): string {
  return `lifecase.analysis-action.edit.v1:${encodeURIComponent(caseId)}:${encodeURIComponent(runId)}`;
}

export interface RetainedAnalysisEdit { runId: string; text: string }

export function readRetainedAnalysisEdits(
  caseId: string, currentRunId: string | null, knownRunIds: readonly string[],
  read: (key: string) => string | null, previous: readonly RetainedAnalysisEdit[] = [],
  currentEdit?: RetainedAnalysisEdit & { caseId: string },
): RetainedAnalysisEdit[] {
  const edits: RetainedAnalysisEdit[] = [];
  for (const runId of new Set(knownRunIds)) {
    if (runId === currentRunId) continue;
    let text: string | null;
    try { text = read(analysisDraftStorageKey(caseId, runId)); } catch { text = null; }
    const memory = currentEdit?.caseId === caseId && currentEdit.runId === runId
      ? currentEdit : previous.find((item) => item.runId === runId);
    // The last visible local edit can be newer than a browser-storage write that failed.
    text = memory?.text ?? text;
    if (text !== null && !text.includes("\0") && Array.from(text).length <= 500) edits.push({ runId, text });
  }
  return edits;
}

export function reconcileAnalysisDraft(input: string | null, previous: AnalysisActionDraft | null, incoming: AnalysisActionDraft): { input: string; needsChoice: boolean } {
  if (input === null || input === incoming.draft_text || (previous && input === previous.draft_text)) {
    return { input: incoming.draft_text, needsChoice: false };
  }
  const sameAuthority = previous && previous.case_id === incoming.case_id && previous.run_id === incoming.run_id &&
    previous.run_version === incoming.run_version && previous.basis_sha256 === incoming.basis_sha256 &&
    previous.answer_sha256 === incoming.answer_sha256 && previous.saved_action?.id === incoming.saved_action?.id &&
    previous.saved_action?.version === incoming.saved_action?.version;
  return { input, needsChoice: !sameAuthority };
}
