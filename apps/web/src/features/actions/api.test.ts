import { describe, expect, it } from "vitest";

import {
  changeActionStatus,
  completeAction,
  createAction,
  decideActionApproval,
  getActions,
  getApprovals,
  requestActionApproval,
} from "./api";
import { actionKindLabel, actionStatusLabel, actionView, canAdvanceAnalysisAction } from "./presenters";
import type { ActionRecord } from "./types";

const action: ActionRecord = {
  id: "action_synthetic",
  case_id: "case_synthetic",
  kind: "prepare_communication",
  status: "ready",
  risk_level: "B",
  payload: { intent: "request_remedy", recipient_role: "merchant" },
  payload_sha256: "0".repeat(64),
  blockers: [],
  blockers_sha256: "1".repeat(64),
  requirements: [{
    claim_id: "claim_one",
    claim_version: 2,
    value_sha256: "2".repeat(64),
    field: "purchase_date",
  }],
  dependencies: [],
  deadline: {
    deadline_id: "deadline_one",
    deadline_version: 1,
    definition_sha256: "3".repeat(64),
    date_kind: "local_date",
    due_value: "2026-08-10",
    timezone: "Asia/Shanghai",
    status: "confirmed",
  },
  completion_evidence: [],
  communication_draft: "Intent: request_remedy",
  topological_position: 0,
  version: 1,
  created_at: "2026-08-03T00:00:00.000000Z",
  updated_at: "2026-08-03T00:00:00.000000Z",
};

describe("Action API", () => {
  it("does not advance saved analysis drafts whose basis changed or cannot be checked", () => {
    expect(canAdvanceAnalysisAction(action)).toBe(true);
    for (const analysis_basis_status of ["changed", "unavailable"] as const) {
      expect(canAdvanceAnalysisAction({ ...action, analysis_basis_status })).toBe(false);
    }
    expect(canAdvanceAnalysisAction({ ...action, analysis_basis_status: "current" })).toBe(true);
    expect(canAdvanceAnalysisAction({ ...action, payload: { ...action.payload, analysis_run_id: "portfolio-live-q1-one" } })).toBe(false);
  });
  it("loads the encoded Case route and retains authority fields", async () => {
    const calls: string[] = [];
    const result = await getActions("case synthetic", async (input) => {
      calls.push(String(input));
      return new Response(JSON.stringify([action]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    expect(calls).toEqual(["/api/v1/cases/case%20synthetic/actions"]);
    expect(result[0]).toMatchObject({ kind: "prepare_communication", risk_level: "B" });
    expect(result[0].deadline?.date_kind).toBe("local_date");
  });

  it("sends optimistic status commands without hidden execution", async () => {
    let captured: RequestInit | undefined;
    const result = await changeActionStatus(
      "action synthetic",
      "revalidate",
      { expected_version: 1, actor: "local_user", reason: "Synthetic refresh" },
      async (_input, init) => {
        captured = init;
        return new Response(JSON.stringify({ ...action, version: 2 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      },
    );
    expect(JSON.parse(String(captured?.body))).toEqual({
      expected_version: 1,
      actor: "local_user",
      reason: "Synthetic refresh",
    });
    expect(result.version).toBe(2);
  });

  it("creates a local follow-up and completes it only with explicit evidence", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const request = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: JSON.parse(String(init?.body)) });
      return new Response(JSON.stringify(action), {
        status: String(input).endsWith("actions") ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    await createAction("case synthetic", {
      kind: "follow_up",
      risk_level: "B",
      payload: { purpose: "Review the local case outcome" },
      requirements: [],
      dependencies: [],
      deadline: null,
      actor: "local_user",
      reason: "Create an approval-gated local action",
    }, request);
    await completeAction(action.id, {
      expected_version: 3,
      actor: "local_user",
      reason: "Recorded outcome against imported source",
      evidence: [{
        authority_type: "source",
        authority_id: "source_one",
        expected_version: null,
        authority_sha256: "4".repeat(64),
      }],
    }, request);
    expect(calls.map((item) => item.url)).toEqual([
      "/api/v1/cases/case%20synthetic/actions",
      "/api/v1/actions/action_synthetic/complete",
    ]);
    expect(calls[1].body).toMatchObject({ evidence: [{ authority_type: "source" }] });
  });

  it("uses exact Action and Approval versions for the local Approval Gate", async () => {
    const calls: Array<{ url: string; body?: unknown }> = [];
    const approval = {
      id: "approval_synthetic",
      action_id: action.id,
      case_id: action.case_id,
      action_version: action.version,
      payload_sha256: action.payload_sha256,
      summary: "Visible exact-version summary",
      summary_sha256: "4".repeat(64),
      status: "pending" as const,
      version: 1,
      requested_by: "local_user",
      requested_reason: "Synthetic request",
      created_at: action.created_at,
      updated_at: action.updated_at,
    };
    const request = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      const payload = String(input).endsWith("/approvals")
        ? [approval]
        : String(input).endsWith("/approve")
          ? { approval: { ...approval, status: "approved", version: 2 }, action }
          : approval;
      return new Response(JSON.stringify(payload), {
        status: String(input).endsWith("approval-requests") ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    };

    expect(await getApprovals("case synthetic", request)).toHaveLength(1);
    await requestActionApproval(action.id, {
      expected_action_version: 1,
      payload_sha256: action.payload_sha256,
      summary: approval.summary,
      actor: "local_user",
      reason: "Synthetic request",
    }, request);
    await decideActionApproval(action.id, "approve", {
      approval_id: approval.id,
      expected_approval_version: 1,
      expected_action_version: 1,
      actor: "local_user",
      reason: "Synthetic approval",
    }, request);

    expect(calls.map((item) => item.url)).toEqual([
      "/api/v1/cases/case%20synthetic/approvals",
      "/api/v1/actions/action_synthetic/approval-requests",
      "/api/v1/actions/action_synthetic/approve",
    ]);
    expect(calls[2].body).toMatchObject({
      approval_id: "approval_synthetic",
      expected_approval_version: 1,
      expected_action_version: 1,
    });
  });

  it("distinguishes offline and safe storage failures", async () => {
    await expect(getActions("case", async () => { throw new TypeError("offline"); }))
      .rejects.toMatchObject({ code: "network_error", retryable: true });
    await expect(getActions("case", async () => new Response(JSON.stringify({
      error: { code: "action_storage_failed", message: "Unavailable", retryable: true },
    }), { status: 503, headers: { "Content-Type": "application/json" } })))
      .rejects.toMatchObject({ code: "action_storage_failed", retryable: true });
  });
});

describe("Action presentation", () => {
  it("maps loading, empty, offline, failure, normal, and unselected states", () => {
    expect(actionView(null, "idle", 0)).toBe("unselected");
    expect(actionView("case", "loading", 0)).toBe("loading");
    expect(actionView("case", "ready", 0)).toBe("empty");
    expect(actionView("case", "offline", 0)).toBe("offline");
    expect(actionView("case", "error", 0)).toBe("failure");
    expect(actionView("case", "ready", 1)).toBe("populated");
  });

  it("keeps all Action kinds and lifecycle states visibly distinct", () => {
    expect([
      actionKindLabel("collect_evidence"),
      actionKindLabel("prepare_communication"),
      actionKindLabel("follow_up"),
      actionKindLabel("record_outcome"),
    ]).toEqual(["补齐证据", "准备沟通草稿", "安排本地跟进", "登记结果"]);
    expect(actionStatusLabel("blocked")).toBe("有阻塞");
    expect(actionStatusLabel("running")).toBe("进行中");
    expect(actionStatusLabel("approved")).toBe("已批准");
    expect(actionStatusLabel("done")).toBe("已完成");
  });
});
