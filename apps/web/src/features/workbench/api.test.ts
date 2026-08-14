import { describe, expect, it } from "vitest";

import { listCases } from "./api";
import { activityLabel, requirementLabel, statusLabel, workbenchView } from "./presenters";
import type { CaseWorkbenchItem } from "./types";

const item: CaseWorkbenchItem = {
  id: "case_synthetic",
  type: "warranty",
  template_id: "warranty_claim",
  template_version: 1,
  title: "合成保修案件",
  goal: "整理合成材料",
  status: "intake",
  priority: 0,
  version: 1,
  is_today: true,
  next_action: {
    id: "action_synthetic",
    kind: "collect_evidence",
    status: "ready",
    risk_level: "B",
    blocker_count: 0,
    version: 1,
  },
  deadline_risk: "not_calculated",
  missing_evidence: {
    required_claims: ["product_name"],
    evidence_requirements: ["purchase_proof"],
    total: 2,
  },
  last_activity_at: "2026-08-02T08:00:00.000000Z",
  last_activity_kind: "case_updated",
  created_at: "2026-08-02T08:00:00.000000Z",
  updated_at: "2026-08-02T08:00:00.000000Z",
  closed_at: null,
};

describe("workbench API client", () => {
  it("loads the ordered local Case summary contract", async () => {
    const request = async () =>
      new Response(JSON.stringify([item]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });

    await expect(listCases(request)).resolves.toEqual([item]);
  });

  it("distinguishes offline and server failures", async () => {
    const offline = async () => {
      throw new TypeError("network unavailable");
    };
    const failed = async () =>
      new Response(
        JSON.stringify({
          error: {
            code: "case_storage_failed",
            message: "Case list could not be read",
            details: {},
            retryable: true,
            request_id: "req_synthetic",
          },
        }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      );
    const unavailableProxy = async () =>
      new Response("Bad Gateway", {
        status: 502,
        headers: { "Content-Type": "text/plain" },
      });
    const malformedServerFailure = async () =>
      new Response("Internal Server Error", {
        status: 500,
        headers: { "Content-Type": "text/plain" },
      });

    await expect(listCases(offline)).rejects.toMatchObject({ code: "network_error" });
    await expect(listCases(unavailableProxy)).rejects.toMatchObject({
      code: "network_error",
      retryable: true,
    });
    await expect(listCases(failed)).rejects.toMatchObject({
      code: "case_storage_failed",
      retryable: true,
    });
    await expect(listCases(malformedServerFailure)).rejects.toMatchObject({
      code: "unexpected_response",
      retryable: true,
    });
  });
});

describe("workbench presentation", () => {
  it("maps all five view states deterministically", () => {
    expect(workbenchView("loading", 0)).toBe("loading");
    expect(workbenchView("ready", 0)).toBe("empty");
    expect(workbenchView("offline", 0)).toBe("offline");
    expect(workbenchView("error", 0)).toBe("failure");
    expect(workbenchView("ready", 1)).toBe("populated");
  });

  it("uses human labels without inventing deadline outputs", () => {
    expect(statusLabel("blocked")).toBe("受阻");
    expect(activityLabel("source_imported")).toBe("材料导入");
    expect(activityLabel("action_updated")).toBe("行动更新");
    expect(requirementLabel("purchase_proof")).toBe("购买凭证");
  });
});
