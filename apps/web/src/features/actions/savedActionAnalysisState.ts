import { getLiveAgent } from "../agentDemo/api";
import type { AnalysisBasisStatus, LiveAgentRunHistory } from "../agentDemo/types";
import { getActionProgress } from "./actionProgressApi";
import type { ActionRecord } from "./types";

export interface SavedActionAnalysisRead {
  analysis: LiveAgentRunHistory;
  actionBasisStatus: AnalysisBasisStatus;
  isLatest: boolean;
}

// Read the saved binding, never reinterpret an old Action using the latest Run.
export async function readSavedActionAnalysis(item: ActionRecord, request: typeof fetch = fetch): Promise<SavedActionAnalysisRead> {
  const fresh = (await getActionProgress(item.case_id, item.id, request)).action;
  if (fresh.version !== item.version || fresh.payload_sha256 !== item.payload_sha256 ||
    Object.keys(fresh.payload).length !== Object.keys(item.payload).length ||
    Object.entries(item.payload).some(([key, value]) => fresh.payload[key] !== value)) throw new Error("Action binding changed");
  const result = await getLiveAgent(item.case_id, request);
  if (!result?.run || result.run.case_id !== item.case_id || !Array.isArray(result.run_history)) throw new Error("Case history unavailable");
  const matches = result.run_history.filter(entry => entry?.id === item.payload.analysis_run_id);
  if (matches.length !== 1) throw new Error("Original Run unavailable");
  const analysis = matches[0];
  if ((analysis.question != null && typeof analysis.question !== "string") ||
    (analysis.basis_status != null && !["current", "changed", "unavailable", "legacy"].includes(analysis.basis_status))) throw new Error("Invalid Run history");
  const answer = analysis.answer;
  if (answer && (!["answered", "unknown"].includes(answer.status) ||
    !Array.isArray(answer.statements) || answer.statements.some(text => typeof text !== "string") ||
    !Array.isArray(answer.citations) || answer.citations.some(citation => citation?.case_id !== item.case_id) ||
    (answer.fact_references != null && (!Array.isArray(answer.fact_references) || answer.fact_references.some(fact =>
      !Array.isArray(fact?.citations) || fact.citations.some(citation => citation?.case_id !== item.case_id)))))) throw new Error("Invalid answer scope");
  return { analysis, actionBasisStatus: fresh.analysis_basis_status!, isLatest: result.run.id === analysis.id };
}
