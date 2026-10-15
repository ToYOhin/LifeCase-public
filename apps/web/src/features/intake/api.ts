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
  const result = await requestJson<CaseTransitionRecord>(
    await request(`/api/v1/cases/${encodeURIComponent(caseId)}/transitions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
  if (!result || result.case_id !== caseId || result.prior_version !== input.expected_version ||
    result.new_version !== input.expected_version + 1 || result.new_status !== input.target_status ||
    result.actor !== input.actor || result.reason !== input.reason) {
    throw new LifeCaseApiError("unexpected_response", "状态保存结果无法确认，请重新读取案件状态。", true);
  }
  return result;
}

// A failed read after saving must never repeat the transition command.
export async function readCaseAfterTransition(caseId: string, saved: CaseTransitionRecord | null, request: typeof fetch = fetch): Promise<CaseRecord> {
  const record = await readImportCase(caseId, null, request);
  if (!Number.isInteger(record.version) || record.version < 1 || (saved &&
    (saved.case_id !== caseId || record.version < saved.new_version ||
      (record.version === saved.new_version && record.status !== saved.new_status)))) {
    throw new LifeCaseApiError("unexpected_response", "案件状态尚未核对，请重新读取。", true);
  }
  return record;
}

// These errors can occur after Source persistence; retryability is not proof of rollback.
export function importRequiresRead(reason: unknown): boolean {
  return !(reason instanceof LifeCaseApiError) || reason.retryable ||
    ["extraction_failed", "source_integrity_failed"].includes(reason.code);
}

// Recover the read after an upload without repeating the upload itself.
export async function readImportCase(caseId: string, imported: SourceRecord | null, request: typeof fetch = fetch): Promise<CaseRecord> {
  const record = await getCase(caseId, (url, init) => request(url, { ...init, cache: "no-store" }));
  if (!record || record.id !== caseId || !Array.isArray(record.sources) || record.sources.some(source => source?.case_id !== caseId)) {
    throw new LifeCaseApiError("unexpected_response", "材料列表无法确认，请重新读取。", true);
  }
  if (imported && (imported.case_id !== caseId || !record.sources.some(source => source.id === imported.id && source.sha256 === imported.sha256))) {
    throw new LifeCaseApiError("unexpected_response", "已保存材料尚未在同案列表中核对，请重新读取。", true);
  }
  return record;
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
        const source = payload as SourceRecord | null;
        if (!source || source.case_id !== caseId || typeof source.id !== "string" || !source.id ||
          typeof source.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(source.sha256) ||
          !["pending", "completed", "completed_with_notices", "failed", "ocr_required"].includes(source.extraction?.status)) {
          reject(new LifeCaseApiError("unexpected_response", "导入结果无法确认，请先重新读取材料列表。", true));
          return;
        }
        onProgress(100);
        resolve(source);
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
