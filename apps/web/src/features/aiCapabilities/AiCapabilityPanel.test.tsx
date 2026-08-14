import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  AiCapabilityCatalog,
  AiSettingsDrawer,
  AiSettingsModelField,
} from "./AiCapabilityPanel";
import type { AiCapabilities } from "./types";

const catalog: AiCapabilities = {
  catalog_version: "ai_provider_catalog.v1",
  current_provider: "deepseek",
  current_model_id: "deepseek-v4-flash",
  runtime_status: "ready",
  browser_configuration: "persistent_local_settings",
  providers: [
    provider("openai", "OpenAI API", "cloud_api", "offline_injection_verified"),
    provider("deepseek", "DeepSeek API", "cloud_api", "synthetic_acceptance_verified", true),
    provider("groq", "Groq API", "cloud_api", "offline_injection_verified"),
    provider("mistral", "Mistral API", "cloud_api", "offline_injection_verified"),
    provider("gemini", "Google Gemini API", "cloud_api", "offline_injection_verified"),
    provider("anthropic", "Anthropic Claude API", "cloud_api", "offline_injection_verified"),
    provider("ollama", "Ollama 本地模型", "local_model", "quality_gate_failed"),
  ],
};

describe("AI settings entry", () => {
  it("mounts settings only while the separate global drawer is open", () => {
    const closed = renderToStaticMarkup(
      <AiSettingsDrawer open={false} onClose={() => undefined}><p>settings sentinel</p></AiSettingsDrawer>,
    );
    const open = renderToStaticMarkup(
      <AiSettingsDrawer open onClose={() => undefined}><p>settings sentinel</p></AiSettingsDrawer>,
    );

    expect(closed).not.toContain("settings sentinel");
    expect(open).toContain("AI 设置");
    expect(open).toContain("settings sentinel");
  });

  it("lists all six cloud APIs and keeps Ollama evaluation-only", () => {
    const markup = renderToStaticMarkup(<AiCapabilityCatalog catalog={catalog} />);

    expect(markup).toContain("OpenAI API");
    expect(markup).toContain("DeepSeek API");
    expect(markup).toContain("Groq API");
    expect(markup).toContain("Mistral API");
    expect(markup).toContain("Google Gemini API");
    expect(markup).toContain("Anthropic Claude API");
    expect(markup).toContain("Ollama 本地模型");
    expect(markup).toContain("质量门禁未通过");
  });

  it("offers both official DeepSeek V4 models and keeps other providers fixed", () => {
    const deepseek = renderToStaticMarkup(
      <AiSettingsModelField provider="deepseek" modelId="deepseek-v4-pro" onChange={() => undefined} />,
    );
    const gemini = renderToStaticMarkup(
      <AiSettingsModelField provider="gemini" modelId="gemini-2.5-flash" onChange={() => undefined} />,
    );

    expect(deepseek).toContain("V4 Flash");
    expect(deepseek).toContain("V4 Pro");
    expect(deepseek).toContain("deepseek-v4-pro");
    expect(gemini).toContain("gemini-2.5-flash");
    expect(gemini).not.toContain("V4 Pro");
  });
});

function provider(
  providerName: AiCapabilities["providers"][number]["provider"],
  label: string,
  interfaceType: AiCapabilities["providers"][number]["interface_type"],
  evidenceStatus: AiCapabilities["providers"][number]["evidence_status"],
  selected = false,
): AiCapabilities["providers"][number] {
  return {
    provider: providerName,
    label,
    interface_type: interfaceType,
    product_status: providerName === "ollama" ? "evaluation_only" : "registered",
    evidence_status: evidenceStatus,
    selected,
    runtime_status: selected ? "ready" : "disabled",
    model_id: selected ? "deepseek-v4-flash" : null,
    action: "由本机设置",
  };
}
