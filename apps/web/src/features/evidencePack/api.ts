import { LifeCaseApiError, offlineGatewayError } from "../intake/api";
import type { ApiErrorEnvelope } from "../intake/types";
import type { EvidencePackArtifact } from "./types";

export async function getLatestEvidencePack(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<EvidencePackArtifact | null> {
  const response = await call(
    `/api/v1/cases/${encodeURIComponent(caseId)}/evidence-pack`,
    undefined,
    request,
  );
  if (response.status === 404) {
    const payload = await safeJson(response);
    if (isApiErrorEnvelope(payload) && payload.error.code === "artifact_not_found") {
      return null;
    }
    throw toApiError(payload, response.status);
  }
  return readArtifact(response);
}

export async function createEvidencePack(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<EvidencePackArtifact> {
  const response = await call(
    `/api/v1/cases/${encodeURIComponent(caseId)}/evidence-pack`,
    { method: "POST" },
    request,
  );
  return readArtifact(response);
}

async function call(
  input: string,
  init: RequestInit | undefined,
  request: typeof fetch,
): Promise<Response> {
  try {
    return await request(input, init);
  } catch {
    throw new LifeCaseApiError("network_error", "无法连接本地 LifeCase API", true);
  }
}

async function readArtifact(response: Response): Promise<EvidencePackArtifact> {
  const payload = await safeJson(response);
  if (!response.ok) throw toApiError(payload, response.status);
  return payload as EvidencePackArtifact;
}

async function safeJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    const offline = offlineGatewayError(response);
    if (offline) throw offline;
    throw new LifeCaseApiError(
      "unexpected_response",
      "本地 API 返回了无法识别的结果",
      response.status >= 500,
    );
  }
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
