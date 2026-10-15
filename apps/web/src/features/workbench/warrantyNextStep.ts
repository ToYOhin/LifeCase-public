import { getActions } from "../actions/api";
import { getActionProgress, type ActionProgressRead } from "../actions/actionProgressApi";
import { actionKindLabel } from "../actions/presenters";
import type { ActionRecord } from "../actions/types";
import type { CaseRecord } from "../intake/types";
import { getCurrentFactsSnapshot, type PreparationSnapshot } from "../warrantyPreparation/api";
import { listCases } from "./api";
import type { WorkflowArea } from "./CaseWorkflow";
import { requirementLabel } from "./presenters";
import type { CaseWorkbenchItem } from "./types";

export interface WarrantyOverviewSnapshot {
  preparation: PreparationSnapshot;
  summary: CaseWorkbenchItem;
  actions: ActionRecord[];
  progress: ActionProgressRead | null;
}

export interface WarrantyNextStep {
  title: string;
  detail: string;
  area: WorkflowArea;
  actionLabel: string;
  actionId?: string;
  fieldTarget?: { field: string; mode: "register" | "review" | "current" };
}

export function hasWarrantyNextStep(record: CaseRecord): boolean {
  return record.type === "warranty" && hasCaseNextStep(record);
}

export function hasCaseNextStep(record: CaseRecord): boolean {
  return ["warranty", "return_refund", "reimbursement", "subscription_cancellation"].includes(record.type) &&
    !["resolved", "abandoned", "archived"].includes(record.status);
}

export function nextStepFieldArea(caseType: string, target: NonNullable<WarrantyNextStep["fieldTarget"]>): WorkflowArea {
  return caseType === "warranty" && target.field === "desired_outcome" && target.mode === "register" ? "actions" : "review";
}

function overviewAction(actions: ActionRecord[], includeLocalResults = false): ActionRecord | undefined {
  const open = actions.filter(action => !["done", "cancelled"].includes(action.status));
  return open.find(action => ["changed", "unavailable"].includes(action.analysis_basis_status ?? ""))
    ?? open[0]
    ?? actions.find(action => action.status === "done" &&
      (action.payload.analysis_run_id || (includeLocalResults && action.kind === "record_outcome")));
}

export async function readWarrantyOverview(record: CaseRecord, signal: AbortSignal, request: typeof fetch = fetch): Promise<WarrantyOverviewSnapshot> {
  if (record.type !== "warranty") throw new Error("案件提示依据不匹配");
  return readCaseOverview(record, signal, request);
}

export async function readCaseOverview(record: CaseRecord, signal: AbortSignal, request: typeof fetch = fetch): Promise<WarrantyOverviewSnapshot> {
  const scopedRequest: typeof fetch = (url, init) => request(url, { ...init, signal, cache: "no-store" });
  const [preparation, cases, actions] = await Promise.all([
    getCurrentFactsSnapshot(record.id, record.type, signal, scopedRequest), listCases(scopedRequest), getActions(record.id, scopedRequest),
  ]);
  const summary = cases.find(item => item.id === record.id);
  if (!summary || summary.type !== record.type || summary.status !== record.status || summary.version !== record.version ||
      summary.template_id !== record.template_id || summary.template_version !== record.template_version ||
      !Array.isArray(summary.missing_evidence?.required_claims) || !Array.isArray(summary.missing_evidence?.evidence_requirements) ||
      !Array.isArray(actions) || actions.some(action => action.case_id !== record.id)) throw new Error("案件提示依据不匹配");
  const selected = overviewAction(actions, record.type !== "warranty");
  const progress = record.type === "warranty" && selected?.kind === "prepare_communication" && selected.payload.analysis_run_id
    ? await getActionProgress(record.id, selected.id, scopedRequest) : null;
  if (progress && (progress.action.version !== selected?.version || progress.action.status !== selected.status ||
      progress.action.payload_sha256 !== selected.payload_sha256 || progress.action.analysis_basis_status !== selected.analysis_basis_status)) {
    throw new Error("待办在读取期间已变化");
  }
  return { preparation, summary, actions, progress };
}

export function warrantyNextStep(record: CaseRecord, data: WarrantyOverviewSnapshot): WarrantyNextStep | null {
  return record.type === "warranty" ? caseNextStep(record, data) : null;
}

export function caseNextStep(record: CaseRecord, data: WarrantyOverviewSnapshot): WarrantyNextStep | null {
  if (!hasCaseNextStep(record) || data.summary.id !== record.id || data.summary.type !== record.type ||
      data.preparation.facts.case_id !== record.id || data.preparation.facts.case_type !== record.type ||
      data.actions.some(action => action.case_id !== record.id)) return null;
  const warranty = record.type === "warranty";
  const purpose = warranty ? "报修内容" : "后续处理内容";
  const selected = overviewAction(data.actions, !warranty);
  if (selected) {
    const base = { area: "actions" as const, actionLabel: "查看这条待办", actionId: selected.id };
    if (["changed", "unavailable"].includes(selected.analysis_basis_status ?? "")) {
      return { ...base, title: "核对待办依据", detail: "已有待办的分析依据已变化或暂时无法核对；先查看原记录，再决定后续处理。" };
    }
    if (selected.status === "done") {
      return { ...base, title: "核对处理结果", detail: warranty
        ? "报修准备待办已完成；请核对处理记录与证据，再由你决定案件是否解决。"
        : "已有完成的结果待办；请核对记录与证据，再由你决定案件是否解决。" };
    }
    const latest = data.progress?.action.id === selected.id ? data.progress.entries.at(-1) : undefined;
    if (latest?.kind === "result") {
      return { ...base, title: "核对处理结果", detail: "这条待办已有人工结果记录；请核对记录与证据，再决定是否继续处理。" };
    }
    if (latest) {
      return { ...base, title: "继续记录处理进展", detail: "这条待办已有人工进展记录；先查看已有记录，再补充本次进展或结果。" };
    }
    if (selected.blockers.length || selected.status === "blocked") {
      const field = selected.blockers.find(blocker => blocker.field)?.field;
      return { ...base, title: "核对待办阻塞", detail: field
        ? `已有待办仍受${requirementLabel(field)}等条件阻塞；先在待办中核对所需依据。`
        : "已有待办仍受条件阻塞；先查看阻塞原因及审批要求，再决定如何处理。" };
    }
    if (selected.status === "failed") {
      return { ...base, title: "核对失败的待办", detail: "这条待办未成功完成；先查看原记录与原因，再决定如何继续，避免重复登记。" };
    }
    return { ...base, title: warranty && selected.kind === "prepare_communication" ? "核对已有报修草案" : `查看${actionKindLabel(selected.kind)}`,
      detail: "已有保存的待办；先核对内容、依据与审批要求，再继续处理。" };
  }
  // Keep explicitly recorded lifecycle guidance when there is no concrete Action.
  if (["blocked", "waiting_external"].includes(record.status)) return null;
  if (!data.preparation.sources.length) {
    const materialReason = record.type === "reimbursement" ? "费用票据用于核对商户、日期与金额"
      : record.type === "subscription_cancellation" ? "订阅或取消请求材料用于定位本次事务"
      : "购买凭证用于核对商品与交易信息";
    return { title: "导入第一份材料", detail: `${materialReason}；先导入 TXT、EML 或文本型 PDF。`,
      area: "overview", actionLabel: "选择材料" };
  }
  if (data.preparation.sources.every(source => !["completed", "completed_with_notices"].includes(source.extraction.status))) {
    return { title: "核对材料提取结果", detail: "当前材料尚无可读文本；先核对提取状态，必要时补充可读材料。",
      area: "overview", actionLabel: "查看材料状态" };
  }
  const items = data.preparation.facts.items.filter(item => item.claim.status !== "rejected");
  const fields = [...new Set(items.map(item => item.claim.field))];
  for (const field of fields) {
    const group = items.filter(item => item.claim.field === field);
    const label = requirementLabel(field);
    if (group.some(item => item.conflict || item.claim.contradiction)) {
      return { title: `核实${label}`, detail: `${label}存在冲突；先对照来源核实，避免把矛盾信息写入${purpose}。`, area: "review", actionLabel: "进入事实复核",
        fieldTarget: { field, mode: group.some(item => ["confirmed", "corrected"].includes(item.claim.status)) ? "current" : "review" } };
    }
    if (!group.some(item => item.usable_as_fact && ["confirmed", "corrected"].includes(item.claim.status)) &&
        group.some(item => ["confirmed", "corrected"].includes(item.claim.status))) {
      return { title: `核实${label}`, detail: `${label}的来源或确认依据当前不可用；先重新核实，再用于${purpose}。`, area: "review", actionLabel: "进入事实复核", fieldTarget: { field, mode: "current" } };
    }
  }
  const missing = data.summary.missing_evidence;
  const field = missing.required_claims[0] ?? missing.evidence_requirements[0];
  if (field) {
    const label = requirementLabel(field);
    const candidate = items.some(item => item.claim.field === field && ["candidate", "inferred"].includes(item.claim.status));
    if (warranty && field === "desired_outcome" && !candidate) {
      return { title: `补充${label}`, detail: "期望结果尚未核实；先保存本人诉求，再明确登记为待核实期望结果，最后由你确认。",
        area: "actions", actionLabel: "补充期望结果", fieldTarget: { field, mode: "register" } };
    }
    if (warranty && field === "defect_media") {
      return { title: `${candidate ? "核实" : "补充"}图片或视频说明`, detail: candidate
        ? "图片或视频说明尚未确认；先核对候选及其来源。确认只针对材料说明，图片或视频本体不会导入或解析。"
        : "当前仅支持 TXT、EML、文本型 PDF。请先在文本材料中说明已有图片或视频，再从原文登记并确认；此处记录的是材料说明，图片或视频本体不会导入或解析。",
      area: "review", actionLabel: candidate ? "核实材料说明" : "登记材料说明", fieldTarget: { field, mode: candidate ? "review" : "register" } };
    }
    return { title: `${candidate ? "核实" : "补充"}${label}`, detail: candidate
      ? `${label}尚未确认；请核对候选及其来源，确认后才能作为${warranty ? "报修" : "后续处理"}依据。`
      : `当前缺少可用的${label}；请选择对应材料原文，登记并确认，才能用于准备${purpose}。`,
    area: "review", actionLabel: "进入事实复核", fieldTarget: { field, mode: candidate ? "review" : "register" } };
  }
  if (!warranty) {
    return { title: "登记本地行动", detail: "当前未发现待处理的事实或材料缺项；可在行动区选择准备沟通、跟进或记录结果。登记不会发送内容或替你决定案件结果。",
      area: "actions", actionLabel: "查看本地行动" };
  }
  return { title: "核对报修准备单", detail: "当前未发现待处理的事实或材料缺项；在准备单核对依据并填写本人诉求，再准备报修内容。",
    area: "actions", actionLabel: "查看报修准备单" };
}
