import { describe, expect, it } from "vitest";

import {
  authorizeLiveAgent,
  cancelLiveAgent,
  executeLiveAgent,
  getLiveAgent,
  previewLiveAgent,
  recoverLiveAgent,
  retryLiveAgent,
} from "./api";
import type { LiveAgentResult } from "./types";

const result: LiveAgentResult = {
  live_version: "live_agent_read_only.v2",
  capability: {
    status: "ready",
    provider: "deepseek",
    model_id: "deepseek-v4-flash",
    action: "Review and authorize",
    supported_case_types: ["warranty"],
  },
  preview: {
    case_type: "warranty",
    evidence: { segment_count: 2, utf8_bytes: 128 },
    budget: {
      max_calls: 14,
      max_input_tokens: 28000,
      max_output_tokens: 14000,
      max_total_tokens: 42000,
      max_cost_minor: 100,
      cost_currency: "CNY",
    },
    read_only_notice: "candidate facts and read-only answer only",
    preview_sha256: "a".repeat(64),
  },
  run: null,
  run_history: [],
};

describe("Live Agent read-only API", () => {
  it("binds each command to the displayed run even when another question is created", async () => {
    const calls: RequestInit[] = [];
    const request = async (_input: RequestInfo | URL, init: RequestInit = {}) => {
      calls.push(init);
      return new Response(JSON.stringify(result), { status: 200 });
    };
    for (const command of [executeLiveAgent, retryLiveAgent, recoverLiveAgent, cancelLiveAgent]) {
      await command("case synthetic", "portfolio-live-q1-displayed", request);
    }
    expect(calls).toHaveLength(4);
    expect(calls.map(call => JSON.parse(String(call.body)))).toEqual(
      Array(4).fill({ expected_run_id: "portfolio-live-q1-displayed" }),
    );
  });
  it("sends only the server preview hash and read-only confirmation", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const request = async (input: RequestInfo | URL, init: RequestInit = {}) => {
      calls.push({ url: String(input), init });
      return new Response(JSON.stringify(result), {
        status: init.body ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    };

    await getLiveAgent("case synthetic", request);
    await authorizeLiveAgent("case synthetic", "a".repeat(64), undefined, request);
    await executeLiveAgent("case synthetic", "legacy-displayed", request);
    await retryLiveAgent("case synthetic", "legacy-displayed", request);
    await recoverLiveAgent("case synthetic", "legacy-displayed", request);
    await cancelLiveAgent("case synthetic", "legacy-displayed", request);

    expect(calls.map((call) => [call.url, call.init.method])).toEqual([
      ["/api/v1/cases/case%20synthetic/live-agent", "GET"],
      ["/api/v1/cases/case%20synthetic/live-agent", "POST"],
      ["/api/v1/cases/case%20synthetic/live-agent/execute", "POST"],
      ["/api/v1/cases/case%20synthetic/live-agent/retry", "POST"],
      ["/api/v1/cases/case%20synthetic/live-agent/recover", "POST"],
      ["/api/v1/cases/case%20synthetic/live-agent/cancel", "POST"],
    ]);
    expect(JSON.parse(String(calls[1].init.body))).toEqual({
      preview_sha256: "a".repeat(64),
      confirm_read_only: true,
    });
    expect(String(calls[1].init.body)).not.toMatch(
      /api_key|host|endpoint|model|provider|provider_output|checkpoint|tools|budget/,
    );
    expect(calls.slice(2).map(call => JSON.parse(String(call.init.body)))).toEqual(
      Array(4).fill({ expected_run_id: "legacy-displayed" }),
    );
  });

  it("binds the original natural-language question to preview and authorization without updating the Case", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const question = "修正后的购买日期是什么？原发票还能作为依据吗？";
    const request = async (input: RequestInfo | URL, init: RequestInit = {}) => {
      calls.push({ url: String(input), init });
      return new Response(JSON.stringify({ ...result, preview: { ...result.preview, question } }), { status: 200 });
    };

    await previewLiveAgent("case synthetic", question, request);
    await authorizeLiveAgent("case synthetic", "a".repeat(64), question, request);

    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/cases/case%20synthetic/live-agent/preview",
      "/api/v1/cases/case%20synthetic/live-agent",
    ]);
    expect(calls.every((call) => call.init.method === "POST")).toBe(true);
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ question });
    expect(JSON.parse(String(calls[1].init.body))).toEqual({
      preview_sha256: "a".repeat(64), confirm_read_only: true, question,
    });
  });
});
