import { LifeCaseApiError, offlineGatewayError } from "../intake/api";
import type { ApiErrorEnvelope } from "../intake/types";
import type {
  EvidenceReviewResult,
  JsonValue,
  ManualClaimInput,
  ReviewClaim,
  ReviewDecisionInput,
  SourceSegment,
} from "./types";

export async function getEvidenceReview(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<EvidenceReviewResult> {
  return requestJson<EvidenceReviewResult>(
    `/api/v1/cases/${encodeURIComponent(caseId)}/evidence-review`,
    undefined,
    request,
  );
}

export async function getSourceSegments(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<SourceSegment[]> {
  return requestJson<SourceSegment[]>(
    `/api/v1/cases/${encodeURIComponent(caseId)}/source-segments`,
    undefined,
    request,
  );
}

export async function createManualClaim(
  caseId: string,
  input: ManualClaimInput,
  request: typeof fetch = fetch,
): Promise<ReviewClaim> {
  return requestJson<ReviewClaim>(
    `/api/v1/cases/${encodeURIComponent(caseId)}/claims`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    request,
  );
}

export async function confirmReviewClaim(
  claimId: string,
  input: ReviewDecisionInput & { basis: "evidence" | "user" },
  request: typeof fetch = fetch,
): Promise<ReviewClaim> {
  return claimDecision(claimId, "confirm", input, request);
}

export async function correctReviewClaim(
  claimId: string,
  input: ReviewDecisionInput & { value: JsonValue },
  request: typeof fetch = fetch,
): Promise<ReviewClaim> {
  return claimDecision(claimId, "correct", input, request);
}

export async function rejectReviewClaim(
  claimId: string,
  input: ReviewDecisionInput,
  request: typeof fetch = fetch,
): Promise<ReviewClaim> {
  return claimDecision(claimId, "reject", input, request);
}

function claimDecision(
  claimId: string,
  operation: "confirm" | "correct" | "reject",
  input: object,
  request: typeof fetch,
): Promise<ReviewClaim> {
  return requestJson<ReviewClaim>(
    `/api/v1/claims/${encodeURIComponent(claimId)}/${operation}`,
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
