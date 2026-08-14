import type {
  ApiErrorEnvelope,
  CaseCreateInput,
  CaseRecord,
  CaseTransitionInput,
  CaseTransitionRecord,
  SourceRecord,
} from "./types";

export class LifeCaseApiError extends Error {
  readonly code: string;
  readonly retryable: boolean;

  constructor(code: string, message: string, retryable = false) {
    super(message);
    this.name = "LifeCaseApiError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function offlineGatewayError(response: Response): LifeCaseApiError | null {
  if (![502, 503, 504].includes(response.status)) return null;
  return new LifeCaseApiError("network_error", "无法连接本地 LifeCase API", true);
}

export async function createCase(
  input: CaseCreateInput,
  request: typeof fetch = fetch,
): Promise<CaseRecord> {
  return requestJson<CaseRecord>(
    await request("/api/v1/cases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function getCase(
  caseId: string,
  request: typeof fetch = fetch,
): Promise<CaseRecord> {
  return requestJson<CaseRecord>(
    await request(`/api/v1/cases/${encodeURIComponent(caseId)}`),
  );
}

export async function transitionCase(
  caseId: string,
  input: CaseTransitionInput,
  request: typeof fetch = fetch,
): Promise<CaseTransitionRecord> {
  return requestJson<CaseTransitionRecord>(
    await request(`/api/v1/cases/${encodeURIComponent(caseId)}/transitions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export function uploadSource(
  caseId: string,
  file: File,
  onProgress: (percentage: number) => void,
  createRequest: () => XMLHttpRequest = () => new XMLHttpRequest(),
): Promise<SourceRecord> {
  return new Promise((resolve, reject) => {
    const request = createRequest();
    request.open("POST", `/api/v1/cases/${encodeURIComponent(caseId)}/sources`);
    request.responseType = "json";
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    });
    request.addEventListener("load", () => {
      const payload: unknown = request.response;
      if (request.status >= 200 && request.status < 300) {
        onProgress(100);
        resolve(payload as SourceRecord);
        return;
      }
      reject(apiError(payload, request.status));
    });
    request.addEventListener("error", () => {
      reject(new LifeCaseApiError("network_error", "无法连接本地 LifeCase API", true));
    });
    request.addEventListener("abort", () => {
      reject(new LifeCaseApiError("upload_cancelled", "材料导入已取消"));
    });
    const body = new FormData();
    body.append("file", file, file.name);
    request.send(body);
  });
}

async function requestJson<T>(response: Response): Promise<T> {
  const payload: unknown = await response.json();
  if (!response.ok) {
    throw apiError(payload, response.status);
  }
  return payload as T;
}

function apiError(payload: unknown, status: number): LifeCaseApiError {
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
