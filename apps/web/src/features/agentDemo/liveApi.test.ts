import { describe, expect, it } from "vitest";

import {
  authorizeLiveAgent,
  cancelLiveAgent,
  executeLiveAgent,
  getLiveAgent,
  recoverLiveAgent,
  retryLiveAgent,
} from "./api";
import type { LiveAgentResult } from "./types";

const result: LiveAgentResult = {
  live_version: "live_agent_read_only.v1",
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
    await authorizeLiveAgent("case synthetic", "a".repeat(64), request);
    await executeLiveAgent("case synthetic", request);
    await retryLiveAgent("case synthetic", request);
    await recoverLiveAgent("case synthetic", request);
    await cancelLiveAgent("case synthetic", request);

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
    expect(calls.slice(2).every((call) => call.init.body === undefined)).toBe(true);
  });
});
