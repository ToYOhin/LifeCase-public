import { LifeCaseApiError, offlineGatewayError } from "../intake/api";
import type { SourceRecord } from "../intake/types";
import type { SourceSegment } from "../review/types";
import type { ActionRecord } from "./types";

export interface ActionProgressEntry {
  id: string; action_id: string; case_id: string; action_version: number;
  kind: "progress" | "result"; text: string; actor: string; reason: string; created_at: string;
}
export interface ActionProgressRead { action: ActionRecord; entries: ActionProgressEntry[] }
export interface ProgressEdit { kind: "progress" | "result"; text: string; actor: string; reason: string }
export interface CompletionSource { source: SourceRecord; segments: SourceSegment[] }

export async function getActionProgress(caseId: string, actionId: string, request: typeof fetch = fetch): Promise<ActionProgressRead> {
  return parseProgress(await requestJson(progressUrl(caseId, actionId), { cache: "no-store" }, request), caseId, actionId);
}

export async function saveActionProgress(snapshot: ActionProgressRead, edit: ProgressEdit, request: typeof fetch = fetch): Promise<ActionProgressRead> {
  const { action, entries } = snapshot;
  const result = parseProgress(await requestJson(progressUrl(action.case_id, action.id), {
    method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_version: action.version, expected_last_progress_id: entries.at(-1)?.id ?? null, ...edit }),
  }, request), action.case_id, action.id);
  const last = result.entries.at(-1);
  if (result.action.version !== action.version || result.action.status !== action.status || result.action.payload_sha256 !== action.payload_sha256 ||
    result.entries.length !== entries.length + 1 || result.entries.slice(0, -1).some((entry, index) => entry.id !== entries[index]?.id) ||
    !last || last.kind !== edit.kind || last.text !== edit.text || last.actor !== edit.actor || last.reason !== edit.reason) throw invalidResponse();
  return result;
}

export async function readCompletionSource(caseId: string, selected: { id: string; sha256: string }, request: typeof fetch = fetch): Promise<CompletionSource> {
  const source = await requestJson(`/api/v1/sources/${encodeURIComponent(selected.id)}`, { cache: "no-store" }, request) as SourceRecord;
  if (!source || source.case_id !== caseId || source.id !== selected.id || source.sha256 !== selected.sha256 ||
    !["completed", "completed_with_notices"].includes(source.extraction?.status)) throw completionUnavailable();
  const result = await requestJson(`/api/v1/cases/${encodeURIComponent(caseId)}/source-segments`, { cache: "no-store" }, request);
  if (!Array.isArray(result)) throw completionUnavailable();
  const segments = result.filter((segment: SourceSegment) => segment?.source_id === source.id) as SourceSegment[];
  if (segments.length === 0 || segments.some(segment => typeof segment.text !== "string" || !segment.text.trim() || !hash(segment.text_sha256))) throw completionUnavailable();
  return { source, segments };
}

function progressUrl(caseId: string, actionId: string) { return `/api/v1/cases/${encodeURIComponent(caseId)}/actions/${encodeURIComponent(actionId)}/progress`; }
function parseProgress(payload: unknown, caseId: string, actionId: string): ActionProgressRead {
  if (!payload || typeof payload !== "object") throw invalidResponse();
  const result = payload as ActionProgressRead;
  const action = result.action;
  if (!action || action.case_id !== caseId || action.id !== actionId || !positive(action.version) ||
    action.kind !== "prepare_communication" || !action.payload?.analysis_run_id || !hash(action.payload_sha256) ||
    !["proposed", "blocked", "ready", "approved", "running", "failed", "done", "cancelled"].includes(action.status) ||
    !["current", "changed", "unavailable"].includes(action.analysis_basis_status ?? "") || !Array.isArray(result.entries)) throw invalidResponse();
  const ids = new Set<string>();
  for (const entry of result.entries) {
    if (!entry || !text(entry.id, 200) || ids.has(entry.id) || entry.case_id !== caseId || entry.action_id !== actionId ||
      !positive(entry.action_version) || entry.action_version > action.version || !["progress", "result"].includes(entry.kind) ||
      !text(entry.text, 2000) || !text(entry.actor, 120) || !text(entry.reason, 1000) || !text(entry.created_at, 100)) throw invalidResponse();
    ids.add(entry.id);
  }
  return result;
}
async function requestJson(url: string, init: RequestInit, request: typeof fetch): Promise<unknown> {
  let response: Response;
  try { response = await request(url, init); } catch { throw new LifeCaseApiError("network_error", "无法连接本地 LifeCase API", true); }
  let payload: unknown;
  try { payload = await response.json(); } catch { throw offlineGatewayError(response) ?? invalidResponse(); }
  if (!response.ok) {
    const error = (payload as { error?: { code?: unknown; message?: unknown; retryable?: unknown } } | null)?.error;
    throw new LifeCaseApiError(typeof error?.code === "string" ? error.code : "action_progress_unavailable",
      typeof error?.message === "string" ? error.message : "处理记录暂时无法读取或保存", error?.retryable === true);
  }
  return payload;
}
function positive(value: unknown) { return Number.isSafeInteger(value) && Number(value) > 0; }
function hash(value: unknown) { return typeof value === "string" && /^[a-f0-9]{64}$/.test(value); }
function text(value: unknown, max: number) { return typeof value === "string" && value.trim().length > 0 && !value.includes("\0") && Array.from(value).length <= max; }
function invalidResponse() { return new LifeCaseApiError("action_progress_invalid_response", "返回记录无法确认，请重新读取后核对。", true); }
function completionUnavailable() { return new LifeCaseApiError("action_completion_unavailable", "所选完成材料当前无法核对，请重新读取。", true); }
