import { afterEach, describe, expect, it, vi } from "vitest";

import { workflowNextStep } from "../../App";
import type { ActionRecord } from "../actions/types";
import type { CaseRecord } from "../intake/types";
import type { CurrentFact } from "../warrantyPreparation/types";
import { hasCaseNextStep, nextStepFieldArea, readCaseOverview, type WarrantyOverviewSnapshot } from "./warrantyNextStep";

const types = ["return_refund", "reimbursement", "subscription_cancellation"] as const;

function fixture(type: string) {
  const record: CaseRecord = {
    id: `synthetic_${type}`, type, template_id: `synthetic_${type}_template`, template_version: 1,
    title: "隔离合成事务", goal: "核对后处理", status: "intake", priority: 0, version: 1,
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", closed_at: null,
    sources: [{ id: "synthetic_source", case_id: `synthetic_${type}`, original_name: "合成说明.txt",
      media_type: "text/plain", sha256: "a".repeat(64), size_bytes: 12, trust_label: "untrusted",
      imported_at: "2026-10-01T00:00:00Z", extraction: { status: "completed", extractor_version: "synthetic",
        segment_count: 1, notices: [], error_code: null } }],
  };
  const fact: CurrentFact = {
    claim: { id: "synthetic_claim", case_id: record.id, field: "merchant", value: "合成商户", status: "confirmed",
      confidence: null, created_by: "user", contradiction: false, version: 1,
      created_at: record.created_at, updated_at: record.updated_at },
    value_sha256: "b".repeat(64), confirmation_basis: "user",
    confirmation: { actor: "synthetic", reason: "隔离确认", occurred_at: record.created_at },
    evidence_status: "none", citations: [], conflict: false, usable_as_fact: true,
  };
  const data: WarrantyOverviewSnapshot = {
    preparation: { facts: { case_id: record.id, case_type: type, items: [fact] }, sources: record.sources, signature: "synthetic" },
    summary: { ...record, is_today: false, next_action: null, deadline_risk: "not_calculated",
      missing_evidence: { required_claims: [], evidence_requirements: [], total: 0 },
      last_activity_at: record.updated_at, last_activity_kind: "claim_updated" },
    actions: [], progress: null,
  };
  const action: ActionRecord = {
    id: "synthetic_action", case_id: record.id, kind: "follow_up", status: "ready", risk_level: "B",
    payload: { purpose: "合成跟进" }, payload_sha256: "c".repeat(64), blockers: [], blockers_sha256: "d".repeat(64),
    requirements: [], dependencies: [], deadline: null, completion_evidence: [], communication_draft: null,
    analysis_basis_status: null, topological_position: 0, version: 1,
    created_at: record.created_at, updated_at: record.updated_at,
  };
  return { record, data, fact, action };
}

describe.each(types)("%s overview next step", type => {
  it("names the actual missing fact and goes to source-backed registration", () => {
    const { record, data } = fixture(type);
    const field = type === "reimbursement" ? "amount" : "desired_outcome";
    data.summary.missing_evidence = { required_claims: [field], evidence_requirements: [], total: 1 };
    const before = JSON.stringify([record, data]);
    const step = workflowNextStep(record, data);
    expect(step).toMatchObject({ title: field === "amount" ? "补充金额" : "补充期望结果",
      area: "review", fieldTarget: { field, mode: "register" } });
    expect(step.detail).not.toMatch(/报修|保存本人诉求/);
    expect(JSON.stringify([record, data])).toBe(before);
  });

  it("reviews a candidate without reporting its value as confirmed", () => {
    const { record, data, fact } = fixture(type);
    fact.claim = { ...fact.claim, status: "candidate", value: "UNVERIFIED_SYNTHETIC" };
    fact.usable_as_fact = false;
    data.summary.missing_evidence = { required_claims: ["merchant"], evidence_requirements: [], total: 1 };
    const step = workflowNextStep(record, data);
    expect(step).toMatchObject({ title: "核实商户", area: "review", fieldTarget: { field: "merchant", mode: "review" } });
    expect(step.detail).not.toContain("UNVERIFIED_SYNTHETIC");
  });

  it("checks conflicting current facts even when the domain gap count is zero", () => {
    const { record, data, fact } = fixture(type);
    fact.conflict = true;
    fact.usable_as_fact = false;
    expect(workflowNextStep(record, data)).toMatchObject({ title: "核实商户", area: "review",
      fieldTarget: { field: "merchant", mode: "current" } });
  });

  it("opens an existing action rather than suggesting another registration", () => {
    const { record, data, action } = fixture(type);
    data.actions = [action];
    data.summary.missing_evidence = { required_claims: ["merchant"], evidence_requirements: [], total: 1 };
    expect(workflowNextStep(record, data)).toMatchObject({ area: "actions", actionId: action.id });
  });

  it("explains a blocked or failed action without promising a transaction result", () => {
    const { record, data, action } = fixture(type);
    data.actions = [{ ...action, status: "blocked", blockers: [{ code: "claim_missing", field: "merchant", authority_id: null }] }];
    expect(workflowNextStep(record, data)).toMatchObject({ title: "核对待办阻塞", area: "actions", actionId: action.id });
    data.actions = [{ ...action, status: "failed" }];
    const step = workflowNextStep(record, data);
    expect(step).toMatchObject({ title: "核对失败的待办", area: "actions", actionId: action.id });
    expect(step.detail).not.toMatch(/已退款|已报销|取消成功/);
  });

  it("offers the existing local action UI when facts and materials are ready", () => {
    const { record, data } = fixture(type);
    const step = workflowNextStep(record, data);
    expect(step).toMatchObject({ title: "登记本地行动", area: "actions" });
    expect(step.detail).not.toMatch(/报修|准备单|已发送|已解决/);
  });

  it("treats a completed result record as something to inspect, not a resolved case", () => {
    const { record, data, action } = fixture(type);
    data.actions = [{ ...action, kind: "record_outcome", status: "done", payload: { outcome: "未经核实的结果文字" } }];
    const step = workflowNextStep(record, data);
    expect(step).toMatchObject({ title: "核对处理结果", area: "actions", actionId: action.id });
    expect(step.detail).toContain("由你决定");
    expect(step.detail).not.toContain("未经核实的结果文字");
    expect(record.status).toBe("intake");
  });

  it.each(["resolved", "abandoned", "archived"] as const)("keeps %s cases in history", status => {
    const { record, data, action } = fixture(type);
    data.actions = [action];
    expect(workflowNextStep({ ...record, status }, data).area).toBe("records");
  });

  it.each(["waiting_external", "blocked"] as const)("preserves %s guidance without a concrete action", status => {
    const { record, data } = fixture(type);
    expect(workflowNextStep({ ...record, status }, data)).toMatchObject({
      title: status === "blocked" ? "检查阻塞原因" : "跟进外部回复",
      area: status === "blocked" ? "records" : "actions",
    });
  });

  it("starts with appropriate materials and checks extraction before claiming readiness", () => {
    const { record, data } = fixture(type);
    data.preparation.sources = [];
    const initial = workflowNextStep(record, data);
    expect(initial).toMatchObject({ title: "导入第一份材料", area: "overview" });
    expect(initial.detail).not.toContain("报修");
    data.preparation.sources = record.sources.map(source => ({ ...source, extraction: { ...source.extraction, status: "failed" } }));
    expect(workflowNextStep(record, data)).toMatchObject({ title: "核对材料提取结果", area: "overview" });
  });

  it("does not equate completed communication with a completed transaction", () => {
    const { record, data, action } = fixture(type);
    data.actions = [{ ...action, kind: "prepare_communication", status: "done", payload: { intent: "request_remedy" } }];
    expect(workflowNextStep(record, data)).toMatchObject({ title: "登记本地行动", area: "actions" });
    expect(record.status).toBe("intake");
  });
});

describe("overview field navigation", () => {
  it.each(types)("keeps %s outcome registration in source-backed fact review", type => {
    expect(nextStepFieldArea(type, { field: "desired_outcome", mode: "register" })).toBe("review");
  });

  it("retains warranty intent registration and existing-fact review", () => {
    expect(nextStepFieldArea("warranty", { field: "desired_outcome", mode: "register" })).toBe("actions");
    expect(nextStepFieldArea("warranty", { field: "desired_outcome", mode: "current" })).toBe("review");
  });

  it("enables only supported open cases", () => {
    for (const type of ["warranty", ...types]) {
      const { record } = fixture(type);
      expect(hasCaseNextStep(record)).toBe(true);
      expect(hasCaseNextStep({ ...record, status: "resolved" })).toBe(false);
    }
    expect(hasCaseNextStep(fixture("unknown_future_type").record)).toBe(false);
  });
});

describe("read-only overview for common cases", () => {
  afterEach(() => vi.restoreAllMocks());

  function transport(record: CaseRecord, data: WarrantyOverviewSnapshot) {
    const network = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("real network forbidden"));
    const request = vi.fn<typeof fetch>(async (url, init) => {
      if (init?.method && init.method !== "GET") throw new Error("write forbidden");
      const payloads: Record<string, unknown> = {
        [`/api/v1/cases/${record.id}/current-facts`]: data.preparation.facts,
        [`/api/v1/cases/${record.id}`]: record,
        "/api/v1/cases": [data.summary],
        [`/api/v1/cases/${record.id}/actions`]: data.actions,
      };
      if (!(String(url) in payloads)) throw new Error("unlisted request forbidden");
      return new Response(JSON.stringify(payloads[String(url)]), { status: 200 });
    });
    return { request, network };
  }

  it.each(types)("reads %s through existing same-case GETs with no progress or model request", async type => {
    const { record, data, action } = fixture(type);
    data.actions = [{ ...action, kind: "prepare_communication", payload: { analysis_run_id: "untrusted_synthetic_id" } }];
    const { request, network } = transport(record, data);
    const signal = new AbortController().signal;
    const result = await readCaseOverview(record, signal, request);
    expect(result.preparation.facts.case_type).toBe(type);
    expect(result.progress).toBeNull();
    expect(request).toHaveBeenCalledTimes(4);
    for (const [url, init] of request.mock.calls) {
      expect(String(url)).toMatch(/^\/api\/v1\/cases(?:\/synthetic_(?:return_refund|reimbursement|subscription_cancellation)(?:\/|$)|$)/);
      expect(init?.method ?? "GET").toBe("GET");
      expect(init?.cache).toBe("no-store");
      expect(init?.signal).toBe(signal);
    }
    expect(network).not.toHaveBeenCalled();
  });

  it.each(types)("rejects %s facts belonging to another case or type", async type => {
    const { record, data } = fixture(type);
    const { request } = transport(record, data);
    data.preparation.facts.case_type = "warranty";
    await expect(readCaseOverview(record, new AbortController().signal, request)).rejects.toThrow("事实或来源校验未通过");
    data.preparation.facts.case_type = type;
    data.preparation.facts.items[0].claim.case_id = "foreign_case";
    await expect(readCaseOverview(record, new AbortController().signal, request)).rejects.toThrow("事实或来源校验未通过");
  });

  it.each(types)("rejects stale %s summary and cross-case actions", async type => {
    const { record, data, action } = fixture(type);
    const { request } = transport(record, data);
    data.summary.version++;
    await expect(readCaseOverview(record, new AbortController().signal, request)).rejects.toThrow("案件提示依据不匹配");
    data.summary.version = record.version;
    data.actions = [{ ...action, case_id: "foreign_case" }];
    await expect(readCaseOverview(record, new AbortController().signal, request)).rejects.toThrow("案件提示依据不匹配");
  });

  it("does not fabricate a ready recommendation when a required read fails", async () => {
    const { record, data } = fixture("reimbursement");
    const { request, network } = transport(record, data);
    request.mockImplementation(async () => { throw new Error("synthetic read failure"); });
    await expect(readCaseOverview(record, new AbortController().signal, request)).rejects.toThrow();
    expect(network).not.toHaveBeenCalled();
  });
});
