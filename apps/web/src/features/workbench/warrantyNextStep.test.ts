import { afterEach, describe, expect, it, vi } from "vitest";

import { workflowNextStep } from "../../App";
import type { ActionProgressRead } from "../actions/actionProgressApi";
import type { ActionRecord } from "../actions/types";
import type { CaseRecord } from "../intake/types";
import type { CurrentFact } from "../warrantyPreparation/types";
import type { CaseWorkbenchItem } from "./types";
import { readWarrantyOverview, type WarrantyOverviewSnapshot } from "./warrantyNextStep";

const record: CaseRecord = {
  id: "synthetic_warranty", type: "warranty", template_id: "warranty_claim", template_version: 1,
  title: "合成耳机报修", goal: "准备维修", status: "intake", priority: 0, version: 1,
  created_at: "2026-09-30T00:00:00Z", updated_at: "2026-09-30T00:00:00Z", closed_at: null,
  sources: [{ id: "synthetic_source", case_id: "synthetic_warranty", original_name: "合成凭证.txt",
    media_type: "text/plain", sha256: "a".repeat(64), size_bytes: 18, trust_label: "untrusted",
    imported_at: "2026-09-30T00:00:00Z",
    extraction: { status: "completed", extractor_version: "synthetic.v1", segment_count: 1, notices: [], error_code: null } }],
};

function fact(field: string, changes: Partial<CurrentFact> = {}): CurrentFact {
  return {
    claim: { id: `synthetic_${field}`, case_id: record.id, field, value: "合成值", status: "confirmed",
      confidence: null, created_by: "user", contradiction: false, version: 1,
      created_at: record.created_at, updated_at: record.updated_at },
    confirmation_basis: "user", confirmation: { actor: "synthetic", reason: "隔离人工确认", occurred_at: record.created_at },
    evidence_status: "none", citations: [], conflict: false, usable_as_fact: true, ...changes,
  };
}

const summary: CaseWorkbenchItem = {
  ...record, is_today: false, next_action: null, deadline_risk: "not_calculated",
  missing_evidence: { required_claims: [], evidence_requirements: [], total: 0 },
  last_activity_at: record.updated_at, last_activity_kind: "claim_updated",
};

const action: ActionRecord = {
  id: "synthetic_action", case_id: record.id, kind: "prepare_communication", status: "proposed", risk_level: "B",
  payload: { analysis_run_id: "synthetic_run" }, payload_sha256: "b".repeat(64), blockers: [], blockers_sha256: "c".repeat(64),
  requirements: [], dependencies: [], deadline: null, completion_evidence: [], communication_draft: "合成正文",
  analysis_basis_status: "current", topological_position: 0, version: 1,
  created_at: record.created_at, updated_at: record.updated_at,
};

function context(items: CurrentFact[] = [fact("product_name")]) {
  return { preparation: { facts: { case_id: record.id, case_type: "warranty", items }, sources: record.sources, signature: "synthetic" },
    summary: { ...summary, missing_evidence: { ...summary.missing_evidence } }, actions: [] as ActionRecord[], progress: null as ActionProgressRead | null };
}

describe("warranty overview next step", () => {
  it("takes a missing personal outcome to saved intent intake instead of demanding a source quote", () => {
    const data = context();
    data.summary.missing_evidence = { required_claims: ["desired_outcome"], evidence_requirements: [], total: 1 };
    expect(workflowNextStep(record, data)).toMatchObject({
      title: "补充期望结果", area: "actions", fieldTarget: { field: "desired_outcome", mode: "register" },
    });
    expect(workflowNextStep(record, data).detail).not.toContain("材料原文");
  });

  it("locates the exact missing field and pending outcome without confirming either", () => {
    const data = context();
    data.summary.missing_evidence = { required_claims: ["order_number"], evidence_requirements: [], total: 1 };
    expect(workflowNextStep(record, data)).toMatchObject({ fieldTarget: { field: "order_number", mode: "register" } });
    const candidate = fact("desired_outcome", { usable_as_fact: false });
    candidate.claim = { ...candidate.claim, status: "candidate" };
    data.preparation.facts.items.push(candidate);
    data.summary.missing_evidence.required_claims = ["desired_outcome"];
    expect(workflowNextStep(record, data)).toMatchObject({ area: "review", fieldTarget: { field: "desired_outcome", mode: "review" } });
    expect(candidate.claim.status).toBe("candidate");
  });

  it("locates an existing conflicting fact rather than creating a replacement", () => {
    const data = context([fact("desired_outcome", { conflict: true, usable_as_fact: false })]);
    expect(workflowNextStep(record, data)).toMatchObject({ area: "review", fieldTarget: { field: "desired_outcome", mode: "current" } });
  });

  it("moves a supported intake case to the preparation sheet without changing its lifecycle", () => {
    const data = context();
    const before = JSON.stringify([record, data]);
    expect(workflowNextStep(record, data)).toMatchObject({ title: "核对报修准备单", area: "actions" });
    expect(JSON.stringify([record, data])).toBe(before);
  });

  it("names one missing fact and explains why it needs confirmation", () => {
    const data = context();
    data.summary.missing_evidence = { required_claims: ["order_number", "purchase_date"], evidence_requirements: [], total: 2 };
    const step = workflowNextStep(record, data);
    expect(step.title).toBe("补充订单编号");
    expect(step.detail).toContain("登记并确认");
    expect(step.area).toBe("review");
    expect(step.title).not.toContain("购买日期");
  });

  it("reviews a candidate instead of reporting its unconfirmed value as a fact", () => {
    const candidate = fact("order_number", { usable_as_fact: false });
    candidate.claim = { ...candidate.claim, status: "candidate", value: "NOT_CONFIRMED" };
    const data = context([candidate]);
    data.summary.missing_evidence = { required_claims: ["order_number"], evidence_requirements: [], total: 1 };
    const step = workflowNextStep(record, data);
    expect(step.title).toBe("核实订单编号");
    expect(step.area).toBe("review");
    expect(step.detail).not.toContain("NOT_CONFIRMED");
  });

  it.each([
    ["conflict", { conflict: true }, "存在冲突"],
    ["unavailable", { evidence_status: "unavailable" as const }, "来源"],
  ])("does not trust a zero gap count when current facts have %s", (_name, changes, reason) => {
    const data = context([fact("purchase_date", { ...changes, usable_as_fact: false })]);
    const step = workflowNextStep(record, data);
    expect(step.title).toBe("核实购买日期");
    expect(step.detail).toContain(reason);
    expect(step.area).toBe("review");
  });

  it("names missing evidence without requiring an absent optional serial number", () => {
    const data = context();
    data.summary.missing_evidence = { required_claims: [], evidence_requirements: ["purchase_proof"], total: 1 };
    const step = workflowNextStep(record, data);
    expect(step.title).toBe("补充购买凭证");
    expect(step.detail).not.toContain("序列号");
    expect(step.area).toBe("review");
  });

  it("uses the domain gap summary when a serial number is actually required", () => {
    const data = context();
    data.summary.missing_evidence = { required_claims: ["serial_number"], evidence_requirements: [], total: 1 };
    expect(workflowNextStep(record, data).title).toBe("补充序列号");
  });

  it("routes missing media to a text description without claiming the media is imported", () => {
    const data = context();
    data.summary.missing_evidence = { required_claims: [], evidence_requirements: ["defect_media"], total: 1 };
    const before = JSON.stringify([record, data]);
    const step = workflowNextStep(record, data);
    expect(step).toMatchObject({
      title: "补充图片或视频说明", area: "review", fieldTarget: { field: "defect_media", mode: "register" },
    });
    expect(step.detail).toContain("TXT、EML、文本型 PDF");
    expect(step.detail).toContain("材料说明");
    expect(step.detail).toContain("本体不会导入或解析");
    expect(JSON.stringify([record, data])).toBe(before);
  });

  it.each(["candidate", "inferred"] as const)("reviews an existing %s media statement without replacing or confirming it", status => {
    const media = fact("defect_media", { usable_as_fact: false });
    media.claim = { ...media.claim, status, value: "尚未核实的影像声明" };
    const data = context([media]);
    data.summary.missing_evidence = { required_claims: [], evidence_requirements: ["defect_media"], total: 1 };
    const before = JSON.stringify([record, data]);
    const step = workflowNextStep(record, data);
    expect(step).toMatchObject({ area: "review", fieldTarget: { field: "defect_media", mode: "review" } });
    expect(step.detail).toContain("材料说明");
    expect(step.detail).toContain("本体不会导入或解析");
    expect(step.detail).not.toContain(media.claim.value);
    expect(JSON.stringify([record, data])).toBe(before);
  });

  it.each([{ conflict: true }, { evidence_status: "unavailable" as const }])("keeps an unusable confirmed media statement in current-fact review: %o", changes => {
    const data = context([fact("defect_media", { ...changes, usable_as_fact: false })]);
    const before = JSON.stringify([record, data]);
    expect(workflowNextStep(record, data)).toMatchObject({ area: "review", fieldTarget: { field: "defect_media", mode: "current" } });
    expect(JSON.stringify([record, data])).toBe(before);
  });

  it("does not let a media gap override an earlier fact gap or an existing action", () => {
    const data = context();
    data.summary.missing_evidence = { required_claims: ["order_number"], evidence_requirements: ["defect_media"], total: 2 };
    expect(workflowNextStep(record, data)).toMatchObject({ fieldTarget: { field: "order_number", mode: "register" } });
    data.actions = [action];
    expect(workflowNextStep(record, data)).toMatchObject({ area: "actions", actionId: action.id });
  });

  it("opens an existing draft instead of sending the user back through unrelated gaps", () => {
    const data = context();
    data.actions = [action];
    data.summary.missing_evidence = { required_claims: ["order_number"], evidence_requirements: [], total: 1 };
    expect(workflowNextStep(record, data)).toMatchObject({ title: "核对已有报修草案", area: "actions", actionId: action.id });
  });

  it("prioritizes an open draft whose original analysis basis changed", () => {
    const data = context();
    data.actions = [action, { ...action, id: "changed_action", analysis_basis_status: "changed" }];
    expect(workflowNextStep(record, data)).toMatchObject({ title: "核对待办依据", actionId: "changed_action", area: "actions" });
  });

  it.each(["progress", "result"] as const)("uses %s records only as a reason to inspect the existing action", kind => {
    const data = context();
    data.actions = [action];
    data.progress = { action, entries: [{ id: "synthetic_progress", action_id: action.id, case_id: record.id,
      action_version: 1, kind, text: "未经核实的自由文字：已经维修成功", actor: "synthetic", reason: "合成记录", created_at: record.updated_at }] };
    const before = JSON.stringify([record, data]);
    const step = workflowNextStep(record, data);
    expect(step.title).toBe(kind === "result" ? "核对处理结果" : "继续记录处理进展");
    expect(step.actionId).toBe(action.id);
    expect(step.detail).toContain("人工");
    expect(step.detail).not.toContain("已经维修成功");
    expect(JSON.stringify([record, data])).toBe(before);
  });

  it("keeps a completed action distinct from deciding whether the case is resolved", () => {
    const data = context();
    const done = { ...action, status: "done" as const };
    data.actions = [done];
    data.progress = { action: done, entries: [] };
    const step = workflowNextStep(record, data);
    expect(step.title).toBe("核对处理结果");
    expect(step.detail).toContain("由你决定");
    expect(record.status).toBe("intake");
  });

  it("keeps a blocked action and external approval behind the existing action UI", () => {
    const data = context();
    data.actions = [{ ...action, kind: "follow_up", risk_level: "C", status: "blocked",
      blockers: [{ code: "claim_missing", field: "order_number", authority_id: null }] }];
    const step = workflowNextStep(record, data);
    expect(step.area).toBe("actions");
    expect(step.actionId).toBe(action.id);
    expect(step.detail).toContain("订单编号");
    expect(step.actionLabel).not.toMatch(/发送|批准|完成/);
  });

  it.each(["resolved", "abandoned", "archived"] as const)("preserves %s history guidance even when gaps or open drafts remain", status => {
    const data = context();
    data.actions = [action];
    data.summary.missing_evidence = { required_claims: ["order_number"], evidence_requirements: [], total: 1 };
    expect(workflowNextStep({ ...record, status }, data).area).toBe("records");
  });

  it("does not apply warranty suggestions to other case types", () => {
    expect(workflowNextStep({ ...record, type: "reimbursement" }, context()).area).toBe("review");
  });

  it("does not turn an action activity timestamp into a claim of received replies", () => {
    const data = context();
    data.summary.last_activity_kind = "action_updated";
    const step = workflowNextStep(record, data);
    expect(step.title).toBe("核对报修准备单");
    expect(step.detail).not.toMatch(/收到回复|已发送|已解决/);
  });
});

describe("read-only warranty overview snapshot", () => {
  afterEach(() => vi.restoreAllMocks());

  function transport(data: WarrantyOverviewSnapshot, caseResponse = record) {
    // Fail closed on every unlisted URL and every write; no real network fallback.
    const network = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("real network forbidden"));
    const request = vi.fn<typeof fetch>(async (url, init) => {
      if (init?.method && init.method !== "GET") throw new Error("write forbidden");
      const payloads: Record<string, unknown> = {
        [`/api/v1/cases/${record.id}/current-facts`]: data.preparation.facts,
        [`/api/v1/cases/${record.id}`]: caseResponse,
        "/api/v1/cases": [data.summary],
        [`/api/v1/cases/${record.id}/actions`]: data.actions,
        [`/api/v1/cases/${record.id}/actions/${action.id}/progress`]: data.progress,
      };
      if (!(String(url) in payloads)) throw new Error(`unlisted URL: ${String(url)}`);
      return new Response(JSON.stringify(payloads[String(url)]), { status: 200 });
    });
    return { request, network };
  }

  it("reads only existing Case-scoped endpoints, with abort and no-store, without a model request", async () => {
    const data = context();
    data.actions = [action];
    data.progress = { action, entries: [] };
    const { request, network } = transport(data);
    const signal = new AbortController().signal;
    const before = JSON.stringify(data);
    const result = await readWarrantyOverview(record, signal, request);
    expect(result.progress?.action.id).toBe(action.id);
    expect(request).toHaveBeenCalledTimes(5);
    for (const [url, init] of request.mock.calls) {
      expect(String(url)).toMatch(/^\/api\/v1\/cases(?:\/synthetic_warranty(?:\/|$)|$)/);
      expect(init?.method ?? "GET").toBe("GET");
      expect(init?.signal).toBe(signal);
      expect(init?.cache).toBe("no-store");
    }
    expect(network).not.toHaveBeenCalled();
    expect(JSON.stringify(data)).toBe(before);
  });

  it("does not read progress for an ordinary action without a bound analysis", async () => {
    const data = context();
    data.actions = [{ ...action, kind: "collect_evidence", payload: { field: "order_number" } }];
    const { request } = transport(data);
    expect((await readWarrantyOverview(record, new AbortController().signal, request)).progress).toBeNull();
    expect(request).toHaveBeenCalledTimes(4);
  });

  it.each([
    ["version", { version: 2 }], ["template", { template_version: 2 }], ["status", { status: "ready" }],
  ])("rejects a stale or mismatched Case %s", async (_name, changes) => {
    const data = context();
    data.summary = { ...data.summary, ...changes };
    const { request } = transport(data);
    await expect(readWarrantyOverview(record, new AbortController().signal, request)).rejects.toThrow("案件提示依据不匹配");
  });

  it("rejects cross-Case current facts using the existing projection validator", async () => {
    const data = context();
    data.preparation.facts.items[0].claim.case_id = "foreign_case";
    const { request } = transport(data);
    await expect(readWarrantyOverview(record, new AbortController().signal, request)).rejects.toThrow("事实或来源校验未通过");
  });

  it("rejects a cross-Case action before fetching its progress", async () => {
    const data = context();
    data.actions = [{ ...action, case_id: "foreign_case" }];
    const { request } = transport(data);
    await expect(readWarrantyOverview(record, new AbortController().signal, request)).rejects.toThrow("案件提示依据不匹配");
    expect(request).toHaveBeenCalledTimes(4);
  });

  it("rejects a cross-Case progress record using the existing reader", async () => {
    const data = context();
    data.actions = [action];
    data.progress = { action: { ...action, case_id: "foreign_case" }, entries: [] };
    const { request } = transport(data);
    await expect(readWarrantyOverview(record, new AbortController().signal, request)).rejects.toThrow("返回记录无法确认");
  });

  it("rejects progress read from a newer version of the selected action", async () => {
    const data = context();
    data.actions = [action];
    data.progress = { action: { ...action, version: 2 }, entries: [] };
    const { request } = transport(data);
    await expect(readWarrantyOverview(record, new AbortController().signal, request)).rejects.toThrow("待办在读取期间已变化");
  });
});
