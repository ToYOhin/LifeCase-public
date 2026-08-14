import { LifeCaseApiError, offlineGatewayError } from "../intake/api";
import type { ApiErrorEnvelope } from "../intake/types";
import type { AgentDemoResult, LiveAgentResult } from "./types";

type AgentCommand = "" | "/execute" | "/retry" | "/recover" | "/cancel";

export function getAgentRun(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<AgentDemoResult> {
  return callAgentRoute(caseId, "", "GET", request);
}

export function createAgentRun(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<AgentDemoResult> {
  return callAgentRoute(caseId, "", "POST", request);
}

export function executeAgentRun(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<AgentDemoResult> {
  return callAgentRoute(caseId, "/execute", "POST", request);
}

export function retryAgentRun(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<AgentDemoResult> {
  return callAgentRoute(caseId, "/retry", "POST", request);
}

export function recoverAgentRun(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<AgentDemoResult> {
  return callAgentRoute(caseId, "/recover", "POST", request);
}

export function cancelAgentRun(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<AgentDemoResult> {
  return callAgentRoute(caseId, "/cancel", "POST", request);
}

export function getLiveAgent(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<LiveAgentResult> {
  return callLiveAgentRoute(caseId, "", "GET", undefined, request);
}

export function authorizeLiveAgent(
  caseId: string,
  previewSha256: string,
  request: typeof fetch = fetch,
): Promise<LiveAgentResult> {
  return callLiveAgentRoute(
    caseId,
    "",
    "POST",
    { preview_sha256: previewSha256, confirm_read_only: true },
    request,
  );
}

export function executeLiveAgent(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<LiveAgentResult> {
  return callLiveAgentRoute(caseId, "/execute", "POST", undefined, request);
}

export function retryLiveAgent(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<LiveAgentResult> {
  return callLiveAgentRoute(caseId, "/retry", "POST", undefined, request);
}

export function recoverLiveAgent(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<LiveAgentResult> {
  return callLiveAgentRoute(caseId, "/recover", "POST", undefined, request);
}

export function cancelLiveAgent(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<LiveAgentResult> {
  return callLiveAgentRoute(caseId, "/cancel", "POST", undefined, request);
}

async function callAgentRoute(
  caseId: string,
  command: AgentCommand,
  method: "GET" | "POST",
  request: typeof fetch,
): Promise<AgentDemoResult> {
  let response: Response;
  try {
    response = await request(
      `/api/v1/cases/${encodeURIComponent(caseId)}/agent-run${command}`,
      { method },
    );
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
  return payload as AgentDemoResult;
}

async function callLiveAgentRoute(
  caseId: string,
  command: AgentCommand,
  method: "GET" | "POST",
  authorization: { preview_sha256: string; confirm_read_only: true } | undefined,
  request: typeof fetch,
): Promise<LiveAgentResult> {
  let response: Response;
  try {
    response = await request(
      `/api/v1/cases/${encodeURIComponent(caseId)}/live-agent${command}`,
      {
        method,
        ...(authorization
          ? {
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(authorization),
            }
          : {}),
      },
    );
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
  return payload as LiveAgentResult;
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
