import { type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ActionPanel } from "./ActionPanel";
import { LocalActionComposer, localActionInput, localActionPayloadValue, matchesLocalAction, type LocalActionFields } from "./LocalActionComposer";
import type { ActionRecord } from "./types";
import type { CurrentFact } from "../warrantyPreparation/types";

// Inspect the actual consumer's composer without opening a browser or changing
// the production drawer's default disclosure state.
vi.mock("../workbench/LazyWorkflowTool", () => ({
  LazyWorkflowTool: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

function markup() {
  return renderToStaticMarkup(<ActionPanel caseId="synthetic-refund-case" caseTitle={null} caseType="return_refund"
    requiredClaimFields={["merchant", "order_number"]} refreshKey={0} onChanged={() => undefined} sources={[]} evidenceFields={["purchase_proof"]} />);
}

describe("Local action creation entry", () => {
  it.each(["collect_evidence", "prepare_communication", "follow_up", "record_outcome"])(
    "offers the existing %s local contract through the Case action composer", kind => {
      expect(markup()).toContain(`value="${kind}"`);
    },
  );

  it("makes clear that manual action content does not become confirmed facts", () => {
    expect(markup()).toContain("手填内容不会登记或确认事实");
  });

  it("keeps local creation approval-gated and disabled until the Case board is read", () => {
    const html = markup();
    expect(html).toContain("精确版本审批");
    expect(html).toContain("不执行外部发送");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>登记(?:审批型|本地)行动<\/button>/);
  });
});

const fields: LocalActionFields = {
  kind: "follow_up", evidenceField: "purchase_proof", intent: "request_information", recipientRole: "merchant",
  purpose: "  核对合成退款回复  ", outcome: "合成退款已到账", actor: " local_user ", reason: " 人工登记合成行动 ",
};

function basis() {
  const items: Array<CurrentFact & { value_sha256?: string }> = ["merchant", "order_number"].map((field, index) => ({
    claim: { id: `synthetic-claim-${index}`, case_id: "synthetic-refund-case", field, value: index ? "合成订单 R31" : "合成商家",
      status: "confirmed", confidence: null, created_by: "user", contradiction: false, version: 2,
      created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" },
    confirmation_basis: "user", confirmation: { actor: "local_user", reason: "核实合成事实", occurred_at: "2026-10-01T00:00:00Z" },
    evidence_status: "none", citations: [], conflict: false, usable_as_fact: true, value_sha256: String(index + 1).repeat(64),
  }));
  return { caseId: "synthetic-refund-case", caseType: "return_refund", requiredClaimFields: ["merchant", "order_number"] as string[] | null,
    facts: { case_id: "synthetic-refund-case", case_type: "return_refund", items } };
}

describe("Local action submitted content", () => {
  it("submits a local follow-up with exact required-fact authority without creating facts or binding analysis", () => {
    expect(localActionInput(fields, ["purchase_proof"], basis())).toEqual({
      kind: "follow_up", risk_level: "B", payload: { purpose: "核对合成退款回复" }, requirements: [
        { claim_id: "synthetic-claim-0", expected_claim_version: 2, value_sha256: "1".repeat(64), field: "merchant" },
        { claim_id: "synthetic-claim-1", expected_claim_version: 2, value_sha256: "2".repeat(64), field: "order_number" },
      ],
      dependencies: [], deadline: null, actor: "local_user", reason: "人工登记合成行动",
    });
  });

  it("records an outcome as an Action instead of marking the Case resolved", () => {
    const input = localActionInput({ ...fields, kind: "record_outcome" }, [], basis());
    expect(input.payload).toEqual({ outcome: "合成退款已到账" });
    expect(input).not.toHaveProperty("status");
    expect(input.risk_level).toBe("B");
  });

  it("uses the existing deterministic communication framework and visibly binds its confirmed facts", () => {
    const communication = { ...fields, kind: "prepare_communication" as const, recipientRole: "employer" };
    expect(localActionInput(communication, [], basis()).payload).toEqual({ intent: "request_information", recipient_role: "employer" });
    expect(localActionInput(communication, [], basis()).requirements).toHaveLength(2);
    const html = renderToStaticMarkup(<LocalActionComposer fields={communication} evidenceFields={[]} basis={basis()} available busy={false}
      onChange={() => undefined} onCreate={() => undefined} />);
    expect(html).toContain("合成商家");
    expect(html).toContain("合成订单 R31");
    expect(html).toContain("固定沟通框架");
    expect(html).toContain("不会发送");
    expect(html).not.toContain("draft_text");
  });

  it("does not allow an invented recipient or purpose to bypass the closed communication selection", () => {
    expect(() => localActionInput({ ...fields, kind: "prepare_communication", recipientRole: "arbitrary_target" }, [], basis())).toThrow();
    expect(() => localActionInput({ ...fields, kind: "prepare_communication", intent: "send_now" }, [], basis())).toThrow();
  });

  it("uses generic communication labels while keeping the original analysis-bound repair labels", () => {
    expect(localActionPayloadValue("intent", "request_remedy", false)).toBe("请求处理");
    expect(localActionPayloadValue("recipient_role", "merchant", false)).toBe("商家");
    expect(localActionPayloadValue("intent", "request_remedy", true)).toBe("请求协助处理报修");
    expect(localActionPayloadValue("recipient_role", "merchant", true)).toBe("商家售后");
  });

  it("keeps collection inside the current template while other local kinds remain available without evidence fields", () => {
    expect(() => localActionInput({ ...fields, kind: "collect_evidence" }, [])).toThrow();
    expect(localActionInput({ ...fields, kind: "collect_evidence" }, ["purchase_proof"]).payload)
      .toEqual({ requirement_key: "purchase_proof", requirement_type: "evidence" });
    expect(localActionInput(fields, [], basis()).kind).toBe("follow_up");
  });

  it("rejects incomplete or overlong follow-up and outcome notes while retaining edits in the rendered editor", () => {
    expect(() => localActionInput({ ...fields, purpose: " " }, [], basis())).toThrow();
    expect(() => localActionInput({ ...fields, purpose: "字".repeat(501) }, [], basis())).toThrow();
    expect(() => localActionInput({ ...fields, kind: "record_outcome", outcome: " " }, [], basis())).toThrow();
    expect(() => localActionInput({ ...fields, kind: "record_outcome", outcome: "字".repeat(501) }, [], basis())).toThrow();
    const html = renderToStaticMarkup(<LocalActionComposer fields={fields} evidenceFields={[]} basis={basis()} available busy
      onChange={() => undefined} onCreate={() => undefined} />);
    expect(html).toContain("核对合成退款回复");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>正在处理登记…<\/button>/);
    expect(html).not.toMatch(/<(?:input|textarea|select)[^>]*disabled/);
  });

  it("recognizes only the same Case and exact ordinary local payload when reading an unknown result", () => {
    const input = localActionInput(fields, [], basis());
    const item: ActionRecord = {
      id: "synthetic-action", case_id: "synthetic-refund-case", kind: "follow_up", status: "ready", risk_level: "B",
      payload: input.payload, payload_sha256: "a".repeat(64), blockers: [], blockers_sha256: "b".repeat(64),
      requirements: input.requirements.map(requirement => ({ claim_id: requirement.claim_id, field: requirement.field,
        claim_version: requirement.expected_claim_version, value_sha256: requirement.value_sha256 })),
      dependencies: [], deadline: null, completion_evidence: [], communication_draft: null,
      topological_position: 0, version: 1, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
    };
    expect(matchesLocalAction(item, item.case_id, input)).toBe(true);
    expect(matchesLocalAction(item, "another-case", input)).toBe(false);
    expect(matchesLocalAction({ ...item, payload: { purpose: "其他请求" } }, item.case_id, input)).toBe(false);
    expect(matchesLocalAction({ ...item, payload: { ...input.payload, analysis_run_id: "old-run" } }, item.case_id, input)).toBe(false);
    expect(matchesLocalAction({ ...item, risk_level: "C" }, item.case_id, input)).toBe(false);
    expect(matchesLocalAction({ ...item, requirements: [] }, item.case_id, input)).toBe(false);
    expect(matchesLocalAction({ ...item, requirements: item.requirements.map(requirement => ({ ...requirement, claim_version: 3 })) }, item.case_id, input)).toBe(false);
    expect(matchesLocalAction({ ...item, requirements: item.requirements.map(requirement => ({ ...requirement, value_sha256: "f".repeat(64) })) }, item.case_id, input)).toBe(false);
  });
});

describe("Local action required-fact gate", () => {
  it.each(["prepare_communication", "follow_up", "record_outcome"] as const)("blocks %s before the required template fields are known", kind => {
    const unknown = basis(); unknown.requiredClaimFields = null;
    expect(() => localActionInput({ ...fields, kind }, [], unknown)).toThrow();
  });

  it.each(["missing", "candidate", "conflict", "unavailable", "confirmation", "hash", "cross-case"])(
    "blocks a %s required fact instead of silently dropping the requirement", problem => {
      const current = basis();
      const fact = current.facts.items[0];
      if (problem === "missing") current.facts.items.shift();
      else if (problem === "candidate") fact.claim.status = "candidate";
      else if (problem === "conflict") fact.conflict = true;
      else if (problem === "unavailable") fact.evidence_status = "unavailable";
      else if (problem === "confirmation") fact.confirmation = null;
      else if (problem === "hash") fact.value_sha256 = undefined;
      else fact.claim.case_id = "different-case";
      expect(() => localActionInput(fields, [], current)).toThrow();
    },
  );

  it("uses the first current usable record for equivalent multi-source facts without recalculating equality", () => {
    const duplicate = basis();
    duplicate.facts.items.push({ ...duplicate.facts.items[0], claim: { ...duplicate.facts.items[0].claim,
      id: "synthetic-equivalent-source", version: 3 } });
    const input = localActionInput(fields, [], duplicate);
    expect(input.requirements.find(requirement => requirement.field === "merchant"))
      .toEqual({ claim_id: "synthetic-claim-0", expected_claim_version: 2, value_sha256: "1".repeat(64), field: "merchant" });
    expect(input.requirements).toHaveLength(2);
  });

  it("rejects duplicate Claim identities across the whole projection", () => {
    const duplicate = basis();
    duplicate.facts.items.push({ ...duplicate.facts.items[0] });
    expect(() => localActionInput(fields, [], duplicate)).toThrow();
  });

  it("still blocks genuine field conflicts and mismatched Case type", () => {
    const conflict = basis(); conflict.facts.items[0].conflict = true;
    conflict.facts.items.push({ ...conflict.facts.items[0], claim: { ...conflict.facts.items[0].claim,
      id: "synthetic-conflicting-source", value: "不同商家" } });
    expect(() => localActionInput(fields, [], conflict)).toThrow();
    const wrongType = basis(); wrongType.facts.case_type = "warranty";
    expect(() => localActionInput(fields, [], wrongType)).toThrow();
  });
});
