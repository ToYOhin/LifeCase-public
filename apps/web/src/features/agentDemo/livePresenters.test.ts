import { describe, expect, it } from "vitest";

import {
  answerStatusLabel,
  claimFieldLabel,
  claimValueLabel,
  completedStageCount,
  liveProviderLabel,
} from "./presenters";
import type { AgentStageTrace } from "./types";

describe("Live Agent demo presenters", () => {
  it("labels configured providers without relabeling disabled state", () => {
    expect(liveProviderLabel("deepseek")).toBe("DeepSeek");
    expect(liveProviderLabel("openai")).toBe("OpenAI");
    expect(liveProviderLabel("groq")).toBe("Groq");
    expect(liveProviderLabel("mistral")).toBe("Mistral");
    expect(liveProviderLabel("disabled")).toBe("默认关闭");
  });

  it("reports the completed portion of the fixed seven-stage pipeline", () => {
    const stages = [
      stage("extract", "checkpointed"),
      stage("normalize", "checkpointed"),
      stage("link_claims", "running"),
    ];

    expect(completedStageCount(stages)).toBe(2);
  });

  it("uses clear Chinese labels for answered and unknown", () => {
    expect(answerStatusLabel("answered")).toBe("已回答");
    expect(answerStatusLabel("unknown")).toBe("证据不足");
  });

  it("formats canonical claim fields without exposing raw JSON", () => {
    expect(claimFieldLabel("merchant_or_manufacturer")).toBe("商家 / 制造商");
    expect(
      claimValueLabel("amount", { amount_minor: 25900, currency: "CNY" }),
    ).toBe("¥259.00");
    expect(claimValueLabel("custom", { private: "value" })).toBe("结构化信息");
  });
});

function stage(
  name: AgentStageTrace["name"],
  status: AgentStageTrace["status"],
): AgentStageTrace {
  return {
    name,
    status,
    version: 1,
    calls: 1,
    input_tokens: 1,
    output_tokens: 1,
    cost_minor: 0,
    provider: "deepseek",
    model_id: "deepseek-v4-pro",
    error_code: null,
    retryable: null,
    summary: null,
    updated_at: "2026-08-12T00:00:00Z",
  };
}
