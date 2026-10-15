import { LifeCaseApiError, offlineGatewayError } from "../intake/api";

export interface PreparationIntent {
  case_id: string;
  intent: string;
  version: number;
  updated_at: string | null;
}

export function getPreparationIntent(caseId: string, signal: AbortSignal, request: typeof fetch = fetch): Promise<PreparationIntent> {
  return intentRequest(caseId, { signal, cache: "no-store" }, request);
}

export async function savePreparationIntent(caseId: string, intent: string, expectedVersion: number, signal: AbortSignal, request: typeof fetch = fetch): Promise<PreparationIntent> {
  const saved = await intentRequest(caseId, {
    method: "PUT", signal, cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ intent, expected_version: expectedVersion }),
  }, request);
  if (saved.intent !== intent || saved.version !== expectedVersion + 1) throw invalidResponse();
  return saved;
}

async function intentRequest(caseId: string, init: RequestInit, request: typeof fetch): Promise<PreparationIntent> {
  let response: Response;
  try {
    response = await request(`/api/v1/cases/${encodeURIComponent(caseId)}/preparation-intent`, init);
  } catch {
    throw new LifeCaseApiError("network_error", "无法连接本地 LifeCase API", true);
  }
  let payload: unknown;
  try { payload = await response.json(); } catch {
    throw offlineGatewayError(response) ?? invalidResponse();
  }
  if (!response.ok) {
    const code = (payload as { error?: { code?: unknown } } | null)?.error?.code;
    if (response.status === 409 && code === "preparation_intent_version_conflict") {
      throw new LifeCaseApiError(code, "其他窗口已保存新的诉求，请先读取最新内容再决定。", true);
    }
    throw offlineGatewayError(response) ?? new LifeCaseApiError(
      typeof code === "string" ? code : "preparation_intent_unavailable",
      "诉求暂时无法读取或保存，请重新核对。", true,
    );
  }
  if (!payload || typeof payload !== "object") throw invalidResponse();
  const value = payload as Partial<PreparationIntent>;
  if (value.case_id !== caseId || typeof value.intent !== "string" || Array.from(value.intent).length > 2000 || value.intent.includes("\0") ||
      !Number.isSafeInteger(value.version) || (value.version ?? -1) < 0 ||
      (value.version === 0 ? value.intent !== "" || value.updated_at !== null :
        typeof value.updated_at !== "string" || !Number.isFinite(Date.parse(value.updated_at)))) throw invalidResponse();
  return value as PreparationIntent;
}

function invalidResponse(): LifeCaseApiError {
  return new LifeCaseApiError("preparation_intent_invalid_response", "诉求返回结果无法确认，请重新读取后核对。", true);
}
