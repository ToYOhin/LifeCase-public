import { LifeCaseApiError, offlineGatewayError } from "../intake/api";
import type { ApiErrorEnvelope } from "../intake/types";
import type { AiCapabilities, AiSettings, AiSettingsInput } from "./types";

export async function getAiCapabilities(
  request: typeof fetch = fetch,
): Promise<AiCapabilities> {
  let response: Response;
  try {
    response = await request("/api/v1/system/ai-capabilities");
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
      "本地 API 返回了无法识别的 AI 能力目录",
      response.status >= 500,
    );
  }
  if (!response.ok) throw toApiError(payload, response.status);
  if (!isAiCapabilities(payload)) {
    throw new LifeCaseApiError("unexpected_response", "AI 能力目录格式无效");
  }
  return payload;
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

function isAiCapabilities(payload: unknown): payload is AiCapabilities {
  if (!payload || typeof payload !== "object") return false;
  const value = payload as Partial<AiCapabilities>;
  return (
    value.catalog_version === "ai_provider_catalog.v1" &&
    typeof value.current_provider === "string" &&
    value.browser_configuration === "persistent_local_settings" &&
    Array.isArray(value.providers)
  );
}

export async function getAiSettings(request: typeof fetch = fetch): Promise<AiSettings> {
  return settingsRequest(await request("/api/v1/system/ai-settings"));
}

export async function saveAiSettings(
  input: AiSettingsInput,
  request: typeof fetch = fetch,
): Promise<AiSettings> {
  return settingsRequest(
    await request("/api/v1/system/ai-settings", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "X-LifeCase-Local-Intent": "ai-settings.v1",
      },
      body: JSON.stringify(input),
    }),
  );
}

export async function clearAiSettings(request: typeof fetch = fetch): Promise<AiSettings> {
  return settingsRequest(
    await request("/api/v1/system/ai-settings", {
      method: "DELETE",
      headers: { "X-LifeCase-Local-Intent": "ai-settings.v1" },
    }),
  );
}

async function settingsRequest(response: Response): Promise<AiSettings> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    const offline = offlineGatewayError(response);
    if (offline) throw offline;
    throw new LifeCaseApiError("unexpected_response", "本地 API 返回了无效的 AI 设置响应");
  }
  if (!response.ok) throw toApiError(payload, response.status);
  if (!isAiSettings(payload)) {
    throw new LifeCaseApiError("unexpected_response", "AI 设置响应格式无效");
  }
  return payload;
}

function isAiSettings(payload: unknown): payload is AiSettings {
  if (!payload || typeof payload !== "object") return false;
  const value = payload as Partial<AiSettings>;
  return (
    value.settings_version === "ai_settings.v1" &&
    typeof value.configured === "boolean" &&
    (value.runtime_status === "disabled" || value.runtime_status === "ready")
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
