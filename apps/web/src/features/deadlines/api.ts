import { LifeCaseApiError, offlineGatewayError } from "../intake/api";
import type { ApiErrorEnvelope } from "../intake/types";
import type {
  DeadlineRecord,
  DeadlineStatusInput,
  SuggestedDeadlineInput,
} from "./types";

export async function getDeadlines(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<DeadlineRecord[]> {
  let response: Response;
  try {
    response = await request(`/api/v1/cases/${encodeURIComponent(caseId)}/deadlines`);
  } catch {
    throw new LifeCaseApiError("network_error", "无法连接本地 LifeCase API", true);
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    const offline = offlineGatewayError(response);
    if (offline) throw offline;
    throw new LifeCaseApiError(
      "unexpected_response",
      "本地 API 返回了无法识别的结果",
      response.status >= 500,
    );
  }
  if (!response.ok) throw toApiError(payload, response.status);
  if (!Array.isArray(payload)) {
    throw new LifeCaseApiError("unexpected_response", "截止日期列表格式无效");
  }
  return payload as DeadlineRecord[];
}

export function createSuggestedDeadline(
  caseId: string,
  input: SuggestedDeadlineInput,
  request: typeof fetch = fetch,
): Promise<DeadlineRecord> {
  return deadlineRequest(
    `/api/v1/cases/${encodeURIComponent(caseId)}/deadlines/suggested`,
    input,
    request,
  );
}

export function changeDeadlineStatus(
  deadlineId: string,
  operation: "confirm" | "complete" | "dismiss",
  input: DeadlineStatusInput,
  request: typeof fetch = fetch,
): Promise<DeadlineRecord> {
  return deadlineRequest(
    `/api/v1/deadlines/${encodeURIComponent(deadlineId)}/${operation}`,
    input,
    request,
  );
}

export function calendarExportUrl(caseId: string, deadlineId: string): string {
  return `/api/v1/cases/${encodeURIComponent(caseId)}/deadlines/${encodeURIComponent(deadlineId)}/calendar-export`;
}

function toApiError(payload: unknown, status: number): LifeCaseApiError {
  if (isApiErrorEnvelope(payload)) {
    return new LifeCaseApiError(
      payload.error.code,
      payload.error.message,
      payload.error.retryable,
    );
  }
  return new LifeCaseApiError(
    "unexpected_response",
    `本地 API 返回了无法识别的错误（${status}）`,
    status >= 500,
  );
}

async function deadlineRequest(
  url: string,
  input: object,
  request: typeof fetch,
): Promise<DeadlineRecord> {
  let response: Response;
  try {
    response = await request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  } catch {
    throw new LifeCaseApiError("network_error", "无法连接本地 LifeCase API", true);
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    const offline = offlineGatewayError(response);
    if (offline) throw offline;
    throw new LifeCaseApiError("unexpected_response", "本地 API 返回了无法识别的结果");
  }
  if (!response.ok) throw toApiError(payload, response.status);
  return payload as DeadlineRecord;
}

function isApiErrorEnvelope(payload: unknown): payload is ApiErrorEnvelope {
  if (!payload || typeof payload !== "object" || !("error" in payload)) return false;
  const error = (payload as { error?: unknown }).error;
  return Boolean(
    error &&
      typeof error === "object" &&
      "code" in error &&
      typeof (error as { code: unknown }).code === "string" &&
      "message" in error &&
      typeof (error as { message: unknown }).message === "string",
  );
}
