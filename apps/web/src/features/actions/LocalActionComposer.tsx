import { actionKindLabel } from "./presenters";
import type { ActionCreateInput, ActionKind, ActionRecord } from "./types";
import { requirementLabel } from "../workbench/presenters";
import { claimValueLabel, fieldLabel } from "../review/presenters";
import type { CurrentFact, CurrentFacts } from "../warrantyPreparation/types";

export interface LocalActionFields {
  kind: ActionKind;
  evidenceField: string;
  intent: string;
  recipientRole: string;
  purpose: string;
  outcome: string;
  actor: string;
  reason: string;
}

export interface LocalActionBasis {
  caseId: string;
  caseType: string;
  requiredClaimFields: string[] | null;
  facts: CurrentFacts | null;
}

function usableFact(item: CurrentFact, caseId: string): boolean {
  if (item.claim.case_id !== caseId || !["confirmed", "corrected"].includes(item.claim.status) ||
    !item.usable_as_fact || item.conflict || item.claim.contradiction || item.evidence_status === "unavailable" ||
    !item.confirmation?.actor.trim() || !item.confirmation.reason.trim() ||
    !Number.isSafeInteger(item.claim.version) || item.claim.version <= 0 ||
    !item.value_sha256 || !/^[0-9a-f]{64}$/.test(item.value_sha256) ||
    item.citations.some(citation => citation.relation === "contradicts")) return false;
  return item.confirmation_basis === "evidence"
    ? item.evidence_status === "valid" && item.citations.some(citation => citation.relation === "supports")
    : item.confirmation_basis === "user" || item.confirmation_basis === "correction";
}

function requiredFacts(basis?: LocalActionBasis | null): { facts: CurrentFact[]; missing: string[]; issue: string | null } {
  if (!basis?.requiredClaimFields?.length) return { facts: [], missing: [], issue: "事务模板尚未读到，请重新读取后再登记。" };
  const fields = basis.requiredClaimFields;
  if (!basis.facts || basis.facts.case_id !== basis.caseId || basis.facts.case_type !== basis.caseType ||
    new Set(fields).size !== fields.length || new Set(basis.facts.items.map(item => item.claim.id)).size !== basis.facts.items.length) {
    return { facts: [], missing: fields, issue: "当前同案事实未能安全读取。" };
  }
  const facts: CurrentFact[] = [];
  const missing: string[] = [];
  for (const field of fields) {
    const matches = basis.facts.items.filter(item => item.claim.field === field && usableFact(item, basis.caseId));
    if (matches.length > 0) facts.push(matches[0]);
    else missing.push(field);
  }
  return { facts, missing, issue: missing.length ? "请先到事实复核区补齐或核对所需事实。" : null };
}

const kinds: ActionKind[] = ["collect_evidence", "prepare_communication", "follow_up", "record_outcome"];
const intents = { request_information: "询问信息", request_remedy: "请求处理", follow_up: "跟进回复" };
const recipients = { merchant: "商家", employer: "单位报销方", service_provider: "服务提供方" };

export function localActionPayloadValue(key: string, value: string, analysisBound: boolean): string {
  if (key === "intent") {
    if (analysisBound && value === "request_remedy") return "请求协助处理报修";
    return Object.hasOwn(intents, value) ? intents[value as keyof typeof intents] : value;
  }
  if (key === "recipient_role") {
    if (analysisBound && value === "merchant") return "商家售后";
    return Object.hasOwn(recipients, value) ? recipients[value as keyof typeof recipients] : value;
  }
  return value;
}

export function localActionInput(fields: LocalActionFields, evidenceFields: string[], basis?: LocalActionBasis | null): ActionCreateInput {
  const actor = fields.actor.trim();
  const reason = fields.reason.trim();
  if (!kinds.includes(fields.kind) || !actor || actor.length > 120 || !reason || reason.length > 1000) {
    throw new Error("请选择行动类型，并填写操作人和登记理由。");
  }
  let payload: Record<string, string>;
  if (fields.kind === "collect_evidence") {
    if (!evidenceFields.includes(fields.evidenceField)) throw new Error("请选择当前案件模板中的待补材料。");
    payload = { requirement_key: fields.evidenceField, requirement_type: "evidence" };
  } else if (fields.kind === "prepare_communication") {
    if (!Object.hasOwn(intents, fields.intent) || !Object.hasOwn(recipients, fields.recipientRole)) {
      throw new Error("请选择沟通目的和接收方角色。");
    }
    payload = { intent: fields.intent, recipient_role: fields.recipientRole };
  } else {
    const text = (fields.kind === "follow_up" ? fields.purpose : fields.outcome).trim();
    if (!text || Array.from(text).length > 500 || text.includes("\0")) throw new Error("请填写不超过 500 字的跟进目的或结果记录。");
    payload = fields.kind === "follow_up" ? { purpose: text } : { outcome: text };
  }
  let requirements: ActionCreateInput["requirements"] = [];
  if (fields.kind !== "collect_evidence") {
    const bound = requiredFacts(basis);
    if (bound.issue) throw new Error(`${bound.issue}${bound.missing.length ? ` ${bound.missing.map(fieldLabel).join("、")}` : ""}`);
    requirements = bound.facts.map(item => ({ claim_id: item.claim.id, expected_claim_version: item.claim.version,
      value_sha256: item.value_sha256!, field: item.claim.field }));
  }
  return { kind: fields.kind, risk_level: "B", payload, requirements, dependencies: [], deadline: null, actor, reason };
}

export function matchesLocalAction(item: ActionRecord, caseId: string, input: ActionCreateInput): boolean {
  return item.case_id === caseId && item.kind === input.kind && item.risk_level === "B" &&
    Array.isArray(item.requirements) && item.requirements.length === input.requirements.length &&
    input.requirements.every(expected => item.requirements.some(actual => actual.claim_id === expected.claim_id &&
      actual.field === expected.field && actual.claim_version === expected.expected_claim_version && actual.value_sha256 === expected.value_sha256)) &&
    Array.isArray(item.dependencies) && item.dependencies.length === 0 && item.deadline === null &&
    Boolean(item.payload) && Object.keys(item.payload).length === Object.keys(input.payload).length &&
    Object.entries(input.payload).every(([key, value]) => item.payload[key] === value);
}

interface Props {
  fields: LocalActionFields;
  evidenceFields: string[];
  available: boolean;
  busy: boolean;
  basis?: LocalActionBasis | null;
  onReviewFacts?: () => void;
  onReadFacts?: () => void;
  onChange: (updates: Partial<LocalActionFields>) => void;
  onCreate: () => void;
}

export function LocalActionComposer({ fields, evidenceFields, available, busy, basis, onReviewFacts, onReadFacts, onChange, onCreate }: Props) {
  let valid = false;
  try { localActionInput(fields, evidenceFields, basis); valid = true; } catch { /* Incomplete edits remain visible. */ }
  const bound = fields.kind === "collect_evidence" ? null : requiredFacts(basis);
  return <div className="action-composer">
    <div><strong>登记本地行动</strong><span>Risk B · 需要精确版本审批 · 不执行外部发送</span></div>
    <p>手填内容不会登记或确认事实。行动完成仍需单独核对材料，案件解决由你另行决定。</p>
    <label>行动类型<select value={fields.kind} onChange={event => onChange({ kind: event.target.value as ActionKind })}>
      {kinds.map(kind => <option key={kind} value={kind}>{actionKindLabel(kind)}</option>)}
    </select></label>
    {fields.kind === "collect_evidence" && <label>待补材料
      <select value={fields.evidenceField} onChange={event => onChange({ evidenceField: event.target.value })}>
        {evidenceFields.length === 0 && <option value="">模板没有可选材料项</option>}
        {evidenceFields.map(field => <option key={field} value={field}>{requirementLabel(field)}</option>)}
      </select>
    </label>}
    {fields.kind === "prepare_communication" && <>
      <p>按下方已核实事实生成固定沟通框架；不会发送，也不新增事实或分析引用。</p>
      <label>沟通目的<select value={fields.intent} onChange={event => onChange({ intent: event.target.value })}>
        {Object.entries(intents).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <label>接收方角色<select value={fields.recipientRole} onChange={event => onChange({ recipientRole: event.target.value })}>
        {Object.entries(recipients).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
    </>}
    {fields.kind === "follow_up" && <label>跟进目的<textarea value={fields.purpose} maxLength={500} rows={3}
      onChange={event => onChange({ purpose: event.target.value })} /></label>}
    {fields.kind === "record_outcome" && <label>结果记录<textarea value={fields.outcome} maxLength={500} rows={3}
      onChange={event => onChange({ outcome: event.target.value })} /></label>}
    {bound && <div className="action-authority-list">
      <strong>本次绑定的已核实事实</strong>
      <small>同值多来源时，采用当前显示的事实记录。</small>
      {bound.facts.map(item => <span key={item.claim.id}>{fieldLabel(item.claim.field)}：{claimValueLabel(item.claim.value)} · 第 {item.claim.version} 版</span>)}
      {bound.issue && <p role="status">{bound.issue}{bound.missing.length > 0 && <> 尚需核对：{bound.missing.map(fieldLabel).join("、")}。</>}</p>}
      {bound.issue && onReviewFacts && <button type="button" disabled={busy} onClick={onReviewFacts}>进入事实复核</button>}
      {bound.issue && onReadFacts && <button type="button" disabled={busy} onClick={onReadFacts}>重新读取事实</button>}
    </div>}
    <label>操作人<input value={fields.actor} maxLength={120} onChange={event => onChange({ actor: event.target.value })} /></label>
    <label>理由<input value={fields.reason} maxLength={1000} onChange={event => onChange({ reason: event.target.value })} /></label>
    <button type="button" disabled={!available || busy || !valid} onClick={onCreate}>{busy ? "正在处理登记…" : "登记本地行动"}</button>
  </div>;
}
