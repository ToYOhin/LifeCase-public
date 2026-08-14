import { LifeCaseApiError, offlineGatewayError } from "../intake/api";
import type { ApiErrorEnvelope } from "../intake/types";
import type { BackupArtifact, RestoreResult, RestoreValidation } from "./types";

export async function getLatestBackup(
  request: typeof fetch = fetch,
): Promise<BackupArtifact | null> {
  const response = await call("/api/v1/backups", undefined, request);
  if (response.status === 404) {
    const payload = await safeJson(response);
    if (isApiErrorEnvelope(payload) && payload.error.code === "backup_not_found") return null;
    throw toApiError(payload, response.status);
  }
  return read<BackupArtifact>(response);
}

export async function createBackup(
  request: typeof fetch = fetch,
): Promise<BackupArtifact> {
  return read<BackupArtifact>(
    await call("/api/v1/backups", { method: "POST" }, request),
  );
}

export async function validateBackup(
  file: File,
  request: typeof fetch = fetch,
): Promise<RestoreValidation> {
  const body = new FormData();
  body.append("file", file);
  return read<RestoreValidation>(
    await call("/api/v1/restores/validate", { method: "POST", body }, request),
  );
}

export async function applyRestore(
  validationId: string,
  targetName: string,
  request: typeof fetch = fetch,
): Promise<RestoreResult> {
  return read<RestoreResult>(
    await call(
      "/api/v1/restores/apply",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          validation_id: validationId,
          target_name: targetName,
          confirmation: "RESTORE INTO NEW DIRECTORY",
        }),
      },
      request,
    ),
  );
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

async function read<T>(response: Response): Promise<T> {
  const payload = await safeJson(response);
  if (!response.ok) throw toApiError(payload, response.status);
  return payload as T;
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
  return new LifeCaseApiError("unexpected_response", `本地 API 错误（${status}）`, status >= 500);
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
