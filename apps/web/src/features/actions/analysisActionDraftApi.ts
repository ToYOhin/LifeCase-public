import { LifeCaseApiError, offlineGatewayError } from "../intake/api";
import type { ActionStatus } from "./types";

export interface AnalysisActionDraft {
  case_id: string;
  run_id: string;
  run_version: number;
  question: string;
  basis_sha256: string;
  answer_sha256: string;
  draft_text: string;
  saved_action: { id: string; version: number; draft_text: string; status: ActionStatus } | null;
}

export function getAnalysisActionDraft(caseId: string, runId: string, request: typeof fetch = fetch): Promise<AnalysisActionDraft> {
  return draftRequest(caseId, runId, { method: "GET", cache: "no-store" }, request);
}

export async function saveAnalysisActionDraft(
  preview: AnalysisActionDraft, text: string, actor: string, reason: string, request: typeof fetch = fetch,
): Promise<AnalysisActionDraft> {
  const result = await draftRequest(preview.case_id, preview.run_id, {
    method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_run_id: preview.run_id, expected_run_version: preview.run_version,
      basis_sha256: preview.basis_sha256, answer_sha256: preview.answer_sha256,
      draft_text: text, actor, reason,
      expected_action_id: preview.saved_action?.id ?? null, expected_action_version: preview.saved_action?.version ?? null }),
  }, request);
  if (!result.saved_action || result.draft_text !== text || result.run_version !== preview.run_version ||
    result.basis_sha256 !== preview.basis_sha256 || result.answer_sha256 !== preview.answer_sha256 ||
    (preview.saved_action && result.saved_action.id !== preview.saved_action.id)) throw invalidResponse();
  return result;
}

async function draftRequest(caseId: string, runId: string, init: RequestInit, request: typeof fetch): Promise<AnalysisActionDraft> {
  let response: Response;
  try {
    const suffix = init.method === "GET" ? `?run_id=${encodeURIComponent(runId)}` : "";
    response = await request(`/api/v1/cases/${encodeURIComponent(caseId)}/analysis-action-draft${suffix}`, init);
  } catch { throw new LifeCaseApiError("network_error", "无法连接本地 LifeCase API", true); }
  let payload: unknown;
  try { payload = await response.json(); } catch { throw offlineGatewayError(response) ?? invalidResponse(); }
  if (!response.ok) {
    const error = (payload as { error?: { code?: unknown; message?: unknown; retryable?: unknown } } | null)?.error;
    throw offlineGatewayError(response) ?? new LifeCaseApiError(
      typeof error?.code === "string" ? error.code : "analysis_action_unavailable",
      typeof error?.message === "string" ? error.message : "报修草案暂时无法读取或保存", error?.retryable === true,
    );
  }
  if (!payload || typeof payload !== "object") throw invalidResponse();
  const value = payload as Partial<AnalysisActionDraft>;
  if (value.case_id !== caseId || value.run_id !== runId || !positiveVersion(value.run_version) ||
    !boundedText(value.question, 2000) || !hash(value.basis_sha256) || !hash(value.answer_sha256) ||
    !boundedText(value.draft_text, 500)) throw invalidResponse();
  const saved = value.saved_action;
  if (saved !== null && (!saved || !boundedText(saved.id, 200) || !positiveVersion(saved.version) ||
    saved.draft_text !== value.draft_text || !["proposed", "blocked", "ready", "running", "done", "failed", "cancelled", "approved"].includes(saved.status))) throw invalidResponse();
  return value as AnalysisActionDraft;
}

function positiveVersion(value: unknown): value is number { return Number.isSafeInteger(value) && Number(value) > 0; }
function boundedText(value: unknown, max: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && !value.includes("\0") && Array.from(value).length <= max;
}
function hash(value: unknown): value is string { return typeof value === "string" && /^[a-f0-9]{64}$/.test(value); }
function invalidResponse(): LifeCaseApiError {
  return new LifeCaseApiError("analysis_action_invalid_response", "草案返回结果无法确认，请重新读取后核对。", true);
}
