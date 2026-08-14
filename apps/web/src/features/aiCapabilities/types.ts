export type AiProviderName =
  | "openai"
  | "deepseek"
  | "groq"
  | "mistral"
  | "gemini"
  | "anthropic"
  | "ollama";

export type AiRuntimeStatus = "disabled" | "ready" | "unavailable" | "evaluation_only";

export interface AiProviderSummary {
  provider: AiProviderName;
  label: string;
  interface_type: "cloud_api" | "local_model";
  product_status: "registered" | "evaluation_only";
  evidence_status:
    | "offline_injection_verified"
    | "synthetic_acceptance_verified"
    | "quality_gate_failed";
  selected: boolean;
  runtime_status: AiRuntimeStatus;
  model_id: string | null;
  action: string;
}

export interface AiCapabilities {
  catalog_version: "ai_provider_catalog.v1";
  current_provider: string;
  current_model_id: string | null;
  runtime_status: AiRuntimeStatus;
  browser_configuration: "persistent_local_settings";
  providers: AiProviderSummary[];
}

export interface AiSettings {
  settings_version: "ai_settings.v1";
  persistence: "windows_credential_manager" | "unavailable";
  configured: boolean;
  provider: AiProviderName | null;
  model_id: string | null;
  runtime_status: "disabled" | "ready";
}

export interface AiSettingsInput {
  provider: Exclude<AiProviderName, "ollama">;
  model_id: string;
  api_key: string | null;
  data_use_confirmed: boolean;
}
