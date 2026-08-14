import { LifeCaseApiError, offlineGatewayError } from "../intake/api";
import type { ApiErrorEnvelope } from "../intake/types";
import type {
  ActionRecord,
  ActionCompleteInput,
  ActionCreateInput,
  ActionStatusInput,
  ApprovalDecisionInput,
  ApprovalDecisionResult,
  ApprovalRecord,
  ApprovalRequestInput,
} from "./types";

export async function getActions(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<ActionRecord[]> {
  return requestJson<ActionRecord[]>(
    `/api/v1/cases/${encodeURIComponent(caseId)}/actions`,
    undefined,
    request,
  );
}

export async function createAction(
  caseId: string,
  input: ActionCreateInput,
  request: typeof fetch = fetch,
): Promise<ActionRecord> {
  return requestJson<ActionRecord>(
    `/api/v1/cases/${encodeURIComponent(caseId)}/actions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    request,
  );
}

export async function completeAction(
  actionId: string,
  input: ActionCompleteInput,
  request: typeof fetch = fetch,
): Promise<ActionRecord> {
  return requestJson<ActionRecord>(
    `/api/v1/actions/${encodeURIComponent(actionId)}/complete`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    request,
  );
}

export async function changeActionStatus(
  actionId: string,
  operation: "revalidate" | "start" | "fail" | "cancel",
  input: ActionStatusInput,
  request: typeof fetch = fetch,
): Promise<ActionRecord> {
  return requestJson<ActionRecord>(
    `/api/v1/actions/${encodeURIComponent(actionId)}/${operation}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    request,
  );
}

export async function getApprovals(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<ApprovalRecord[]> {
  return requestJson<ApprovalRecord[]>(
    `/api/v1/cases/${encodeURIComponent(caseId)}/approvals`,
    undefined,
    request,
  );
}

export async function requestActionApproval(
  actionId: string,
  input: ApprovalRequestInput,
  request: typeof fetch = fetch,
): Promise<ApprovalRecord> {
  return requestJson<ApprovalRecord>(
    `/api/v1/actions/${encodeURIComponent(actionId)}/approval-requests`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    request,
  );
}

export async function decideActionApproval(
  actionId: string,
  operation: "approve" | "reject" | "expire-approval",
  input: ApprovalDecisionInput,
  request: typeof fetch = fetch,
): Promise<ApprovalRecord | ApprovalDecisionResult> {
  return requestJson<ApprovalRecord | ApprovalDecisionResult>(
    `/api/v1/actions/${encodeURIComponent(actionId)}/${operation}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    request,
  );
}

async function requestJson<T>(
  url: string,
  init: RequestInit | undefined,
  request: typeof fetch,
): Promise<T> {
  let response: Response;
  try {
    response = await request(url, init);
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
  return payload as T;
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
