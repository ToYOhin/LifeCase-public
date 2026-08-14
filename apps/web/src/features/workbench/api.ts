import { LifeCaseApiError, offlineGatewayError } from "../intake/api";
import type { ApiErrorEnvelope } from "../intake/types";
import type { CaseWorkbenchItem } from "./types";

export async function listCases(
  request: typeof fetch = fetch,
): Promise<CaseWorkbenchItem[]> {
  let response: Response;
  try {
    response = await request("/api/v1/cases");
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
  if (!response.ok) {
    throw toApiError(payload, response.status);
  }
  if (!Array.isArray(payload)) {
    throw new LifeCaseApiError("unexpected_response", "案件列表格式无效");
  }
  return payload as CaseWorkbenchItem[];
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

function isApiErrorEnvelope(payload: unknown): payload is ApiErrorEnvelope {
  if (!payload || typeof payload !== "object" || !("error" in payload)) {
    return false;
  }
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
