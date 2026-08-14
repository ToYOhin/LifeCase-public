import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import {
  clearAiSettings,
  getAiCapabilities,
  getAiSettings,
  saveAiSettings,
} from "./api";
import type {
  AiCapabilities,
  AiProviderName,
  AiProviderSummary,
  AiRuntimeStatus,
  AiSettings,
  AiSettingsInput,
} from "./types";

const PROVIDER_MODELS: Record<AiSettingsInput["provider"], readonly string[]> = {
  openai: ["gpt-5.6-terra"],
  deepseek: ["deepseek-v4-flash", "deepseek-v4-pro"],
  groq: ["openai/gpt-oss-20b"],
  mistral: ["mistral-small-2603"],
  gemini: ["gemini-2.5-flash"],
  anthropic: ["claude-sonnet-4-5-20250929"],
};

interface AiSettingsDrawerProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}

export function AiSettingsDrawer({ open, onClose, children }: AiSettingsDrawerProps) {
  useEffect(() => {
    if (!open) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open]);

  if (!open) return null;
  return (
    <div className="ai-settings-layer">
      <button
        className="ai-settings-scrim"
        type="button"
        aria-label="关闭 AI 设置"
        onClick={onClose}
      />
      <aside className="ai-settings-drawer" role="dialog" aria-modal="true" aria-labelledby="ai-settings-title">
        <header>
          <div>
            <p className="section-label">本机运行配置</p>
            <h2 id="ai-settings-title">AI 设置</h2>
          </div>
          <button className="secondary-action" type="button" onClick={onClose}>
            关闭
          </button>
        </header>
        {children}
      </aside>
    </div>
  );
}

export function AiSettingsPanel() {
  const [catalog, setCatalog] = useState<AiCapabilities | null>(null);
  const [settings, setSettings] = useState<AiSettings | null>(null);
  const [provider, setProvider] = useState<AiSettingsInput["provider"]>("deepseek");
  const [modelId, setModelId] = useState(PROVIDER_MODELS.deepseek[0]);
  const [apiKey, setApiKey] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void Promise.all([getAiCapabilities(), getAiSettings()])
      .then(([nextCatalog, nextSettings]) => {
        if (!active) return;
        setCatalog(nextCatalog);
        setSettings(nextSettings);
        if (nextSettings.provider && nextSettings.provider !== "ollama") {
          setProvider(nextSettings.provider);
          setModelId(nextSettings.model_id ?? PROVIDER_MODELS[nextSettings.provider][0]);
        }
      })
      .catch((reason: unknown) => {
        if (active) setError(errorMessage(reason));
      });
    return () => {
      active = false;
    };
  }, []);

  const cloudProviders = useMemo(
    () => catalog?.providers.filter((item) => item.interface_type === "cloud_api") ?? [],
    [catalog],
  );

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const saved = await saveAiSettings({
        provider,
        model_id: modelId,
        api_key: apiKey || null,
        data_use_confirmed: confirmed,
      });
      setSettings(saved);
      setApiKey("");
      setMessage("AI Provider 已保存并启用；重启 LifeCase 后仍可继续使用。");
      setCatalog(await getAiCapabilities());
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      setSettings(await clearAiSettings());
      setApiKey("");
      setConfirmed(false);
      setMessage("已清除本机保存的 AI 凭据，Provider 已恢复为禁用状态。");
      setCatalog(await getAiCapabilities());
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  if (error && (!catalog || !settings)) {
    return <p className="ai-settings-state" role="alert">{error}</p>;
  }
  if (!catalog || !settings) {
    return <p className="ai-settings-state">正在读取本机 AI 设置…</p>;
  }

  return (
    <div className="ai-settings-panel">
      <section className="ai-settings-current" aria-label="当前 AI 配置">
        <div>
          <span>当前状态</span>
          <strong>{settings.configured ? providerLabel(settings.provider) : "未启用 Provider"}</strong>
          {settings.model_id && <code>{settings.model_id}</code>}
        </div>
        <span className={`ai-runtime-badge status-${settings.runtime_status}`}>
          {settings.runtime_status === "ready" ? "运行就绪" : "未启用"}
        </span>
      </section>

      <p className="ai-settings-note">
        Key 保存于当前 Windows 用户的凭据管理器，重启 LifeCase 后仍有效。案件分析会向所选服务商发送当前授权范围内的材料。
      </p>

      <form className="ai-settings-form" onSubmit={save}>
        <label>
          AI Provider
          <select
            value={provider}
            onChange={(event) => {
              setProvider(event.target.value as AiSettingsInput["provider"]);
              setModelId(
                PROVIDER_MODELS[event.target.value as AiSettingsInput["provider"]][0],
              );
              setConfirmed(false);
            }}
            disabled={busy}
          >
            {cloudProviders.map((item) => (
              <option key={item.provider} value={item.provider}>{item.label}</option>
            ))}
          </select>
        </label>
        <AiSettingsModelField
          provider={provider}
          modelId={modelId}
          onChange={(nextModelId) => {
            setModelId(nextModelId);
            setConfirmed(false);
          }}
        />
        <label>
          API Key
          <input
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            autoComplete="new-password"
            spellCheck={false}
            placeholder={
              settings.configured && settings.provider === provider
                ? "留空将沿用已保存凭据"
                : "输入所选 Provider 的 API Key"
            }
            required={!settings.configured || settings.provider !== provider}
          />
        </label>
        <label className="ai-settings-confirm">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          <span>{confirmationLabel(provider)}</span>
        </label>
        <div className="ai-settings-actions">
          <button
            type="submit"
            disabled={
              busy ||
              !confirmed ||
              (!apiKey && (!settings.configured || settings.provider !== provider))
            }
          >保存并启用</button>
          {settings.configured && (
            <button className="secondary-action is-danger" type="button" disabled={busy} onClick={() => void clear()}>
              清除已保存凭据
            </button>
          )}
        </div>
      </form>

      {(message || error) && <p className={error ? "form-error" : "form-message"} role="status">{error ?? message}</p>}

      <details className="ai-settings-catalog">
        <summary>支持的接口与技术状态（{catalog.providers.length}）</summary>
        <AiCapabilityCatalog catalog={catalog} />
      </details>
    </div>
  );
}

export function AiSettingsModelField({
  provider,
  modelId,
  onChange,
}: {
  provider: AiSettingsInput["provider"];
  modelId: string;
  onChange: (modelId: string) => void;
}) {
  const models = PROVIDER_MODELS[provider];
  return (
    <label>
      模型
      {provider === "deepseek" ? (
        <select value={modelId} onChange={(event) => onChange(event.target.value)}>
          <option value="deepseek-v4-flash">V4 Flash · deepseek-v4-flash</option>
          <option value="deepseek-v4-pro">V4 Pro · deepseek-v4-pro</option>
        </select>
      ) : (
        <input value={models[0]} readOnly aria-readonly="true" />
      )}
    </label>
  );
}

export function AiCapabilityCatalog({ catalog }: { catalog: AiCapabilities }) {
  return (
    <div className="ai-provider-list" aria-label="AI Provider 能力目录">
      {catalog.providers.map((provider) => (
        <ProviderRow key={provider.provider} provider={provider} />
      ))}
    </div>
  );
}

function ProviderRow({ provider }: { provider: AiProviderSummary }) {
  return (
    <article className={`ai-provider-row${provider.selected ? " is-selected" : ""}`}>
      <div className="ai-provider-heading">
        <div><strong>{provider.label}</strong><span>{provider.interface_type === "cloud_api" ? "云端 API" : "本地模型"}</span></div>
        {provider.selected && <span className="ai-selected-badge">当前配置</span>}
      </div>
      <div className="ai-provider-statuses">
        <span>{evidenceStatusLabel(provider.evidence_status)}</span>
        <span>{provider.product_status === "registered" ? "已注册只读路径" : "仅限评估"}</span>
      </div>
      <p>{provider.action}</p>
      {provider.model_id && <details className="ai-provider-technical"><summary>技术详情</summary><code>{provider.model_id}</code></details>}
    </article>
  );
}

function providerLabel(provider: AiProviderName | null): string {
  const labels: Record<AiProviderName, string> = {
    openai: "OpenAI API",
    deepseek: "DeepSeek API",
    groq: "Groq API",
    mistral: "Mistral API",
    gemini: "Google Gemini API",
    anthropic: "Anthropic Claude API",
    ollama: "Ollama 本地模型",
  };
  return provider ? labels[provider] : "未启用 Provider";
}

function confirmationLabel(provider: AiSettingsInput["provider"]): string {
  if (provider === "groq" || provider === "mistral") {
    return "确认该账户已启用无保留处理（ZDR），并同意发送当前案件材料。";
  }
  return "确认同意将当前案件材料发送至所选 AI Provider 处理。";
}

function evidenceStatusLabel(status: AiProviderSummary["evidence_status"]): string {
  const labels: Record<AiProviderSummary["evidence_status"], string> = {
    offline_injection_verified: "离线注入验证",
    synthetic_acceptance_verified: "合成验收通过",
    quality_gate_failed: "质量门禁未通过",
  };
  return labels[status];
}

function errorMessage(reason: unknown): string {
  return reason instanceof LifeCaseApiError ? reason.message : "AI 设置操作失败，请检查本地服务。";
}

export function runtimeStatusLabel(status: AiRuntimeStatus): string {
  const labels: Record<AiRuntimeStatus, string> = {
    disabled: "未启用",
    ready: "运行就绪",
    unavailable: "配置未就绪",
    evaluation_only: "仅限评估",
  };
  return labels[status];
}
