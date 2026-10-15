import { describe, expect, it } from "vitest";

import {
  cancelAgentRun,
  createAgentRun,
  executeAgentRun,
  getAgentRun,
  recoverAgentRun,
  retryAgentRun,
} from "./api";
import { agentDemoView, stageLabel } from "./presenters";
import type { AgentDemoResult } from "./types";

const emptyResult: AgentDemoResult = {
  demo_version: "portfolio_agent_demo.v1",
  capability: {
    status: "enabled",
    provider: "deterministic_fake",
    action: "Start the demo",
    supported_case_types: ["warranty"],
  },
  run: null,
};

describe("Portfolio Agent Demo API", () => {
  it("uses only Case-scoped fixed command routes", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    const request = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), method: init?.method ?? "GET" });
      return new Response(JSON.stringify(emptyResult), {
        status: String(input).endsWith("agent-run") && init?.method === "POST" ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    };

    await getAgentRun("case synthetic", request);
    await createAgentRun("case synthetic", request);
    await executeAgentRun("case synthetic", request);
    await retryAgentRun("case synthetic", request);
    await recoverAgentRun("case synthetic", request);
    await cancelAgentRun("case synthetic", request);

    expect(calls).toEqual([
      { url: "/api/v1/cases/case%20synthetic/agent-run", method: "GET" },
      { url: "/api/v1/cases/case%20synthetic/agent-run", method: "POST" },
      { url: "/api/v1/cases/case%20synthetic/agent-run/execute", method: "POST" },
      { url: "/api/v1/cases/case%20synthetic/agent-run/retry", method: "POST" },
      { url: "/api/v1/cases/case%20synthetic/agent-run/recover", method: "POST" },
      { url: "/api/v1/cases/case%20synthetic/agent-run/cancel", method: "POST" },
    ]);
  });

  it("maps offline and typed failures without exposing response bodies", async () => {
    await expect(
      getAgentRun("case", async () => {
        throw new TypeError("private network detail");
      }),
    ).rejects.toMatchObject({ code: "network_error", retryable: true });

    await expect(
      retryAgentRun("case", async () =>
        new Response(
          JSON.stringify({
            error: {
              code: "agent_run_not_executable",
              message: "Agent run cannot perform this command",
              retryable: false,
            },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    ).rejects.toMatchObject({ code: "agent_run_not_executable", retryable: false });
  });
});

describe("Portfolio Agent Demo presentation", () => {
  it("covers loading, disabled, offline, failure, ready, running and completed", () => {
    expect(agentDemoView(null, "idle", emptyResult)).toBe("unselected");
    expect(agentDemoView("case", "loading", null)).toBe("loading");
    expect(agentDemoView("case", "ready", {
      ...emptyResult,
      capability: { ...emptyResult.capability, status: "disabled", provider: "disabled" },
    })).toBe("disabled");
    expect(agentDemoView("case", "offline", null)).toBe("offline");
    expect(agentDemoView("case", "error", null)).toBe("failure");
    expect(agentDemoView("case", "ready", emptyResult)).toBe("ready");
    expect(agentDemoView("case", "running", emptyResult)).toBe("running");
    expect(agentDemoView("case", "ready", {
      ...emptyResult,
      run: { status: "completed" } as AgentDemoResult["run"],
    })).toBe("completed");
  });

  it("gives all fixed stages explicit human labels", () => {
    expect(stageLabel("extract")).toBe("提取候选事实");
    expect(stageLabel("normalize")).toBe("规范化");
    expect(stageLabel("link_claims")).toBe("关联事实");
    expect(stageLabel("classify_case")).toBe("识别案件类型");
    expect(stageLabel("check_gaps")).toBe("检查缺件");
    expect(stageLabel("propose_actions")).toBe("提出只读建议");
    expect(stageLabel("validate")).toBe("证据检索 / 回答");
  });
});
