import { useState } from "react";

import {
  answerStatusLabel,
  claimFieldLabel,
  claimStatusLabel,
  claimReviewNote,
  claimValueLabel,
  completedStageCount,
  liveProviderLabel,
  liveWorkflowLabel,
  runStatusLabel,
  stageLabel,
} from "./presenters";
import type { AnalysisBasisStatus, AnalysisFact, LiveAgentResult, LiveAgentRunHistory } from "./types";
import { AnswerCitations } from "./AnswerCitations";

interface LiveAgentDossierProps {
  result: LiveAgentResult;
  busy: boolean;
  onExecute?: () => void;
  onRetry?: () => void;
  onRecover?: () => void;
  onCancel?: () => void;
  onReanalyze?: () => void;
}

export function LiveAgentDossier({
  result,
  busy,
  onExecute,
  onRetry,
  onRecover,
  onCancel,
  onReanalyze,
}: LiveAgentDossierProps) {
  const run = result.run;
  if (!run) return null;
  const completed = completedStageCount(run.stages);
  const answer = run.answer;
  const analysis = run.analysis;

  return (
    <div className="live-dossier">
      <div className="live-bound-question" aria-label="运行绑定问题">
        {analysis ? <><strong>本次问题</strong><p>{analysis.question}</p><BasisNotice status={analysis.basis_status} /></>
          : <p>旧运行未保存独立问题与事实快照；以下为原流程记录，不能判断它是否与当前事实一致。</p>}
      </div>
      <div className="live-dossier-status" aria-label="运行摘要">
        <span className={`live-status-mark live-status-${answer?.status ?? run.status}`}>
          {answer ? answerStatusLabel(answer.status) : runStatusLabel(run.status)}
        </span>
        {run.status !== "completed" && <span>{completed} / {run.stage_order.length} 步已完成</span>}
      </div>

      <div className="live-dossier-primary">
        <article className={`evidence-conclusion evidence-conclusion-${answer?.status ?? "pending"}`}>
          <h3>分析结果</h3>
          <div className="conclusion-copy">
            {answer?.status === "answered" && answer.statements.map((statement) => <p key={statement}>{statement}</p>)}
            {answer?.status === "unknown" && <p>{answer.uncertainty_reason?.trim() || "当前材料不足以回答这个问题。"}</p>}
            {!answer && <p>本次运行尚未生成最终只读回答。</p>}
          </div>

          <AnswerCitations key={`${run.id}:${run.version}`} caseId={run.case_id} citations={answer?.citations ?? []} />
          {(answer?.fact_references?.length ?? 0) > 0 && (
            <details className="analysis-fact-references">
              <summary>回答采用的事实依据</summary>
              <AnalysisFacts facts={answer!.fact_references!} />
            </details>
          )}
        </article>

        <details className="dossier-facts">
          <summary>{analysis ? "查看分析时的事实" : "查看分析事实"} · {analysis ? analysis.facts.length : run.claims.length} 项</summary>
          {analysis && <p>以下保留当时采用的内容；当前事实请到“事实复核”查看。</p>}
          {analysis ? <AnalysisFacts facts={analysis.facts} /> : run.claims.length > 0 ? (
            <dl>
              {run.claims.map((claim) => (
                <div key={claim.id}>
                  <dt>{claimFieldLabel(claim.field)}</dt>
                  <dd>{claimValueLabel(claim.field, claim.value)}</dd>
                  <small>{claimStatusLabel(claim.status)} · {claimReviewNote(claim.status)}</small>
                </div>
              ))}
            </dl>
          ) : (
            <p>本次没有生成候选事实。</p>
          )}
        </details>
      </div>

      <details className="stage-rail" data-stage-count={run.stage_order.length} open={run.status !== "completed"}>
        <summary>分析过程 · {completed} / {run.stage_order.length} 步</summary>
        <div className="dossier-section-heading">
          <div>
            <h3 id="stage-rail-title">{liveWorkflowLabel(run.stage_order)}</h3>
          </div>
          <strong>{completed} / {run.stage_order.length}</strong>
        </div>
        {run.stage_order.length === 7 && <p>此运行保留旧版七阶段顺序，继续或恢复时沿用原流程。</p>}
        {analysis && <p>继续、重试或恢复沿用本运行的问题和输入依据，不重新提取事实。</p>}
        <ol>
          {run.stage_order.map((name, index) => {
            const stage = run.stages.find((item) => item.name === name);
            return (
              <li key={name} className={`stage-step stage-${stage?.status ?? "pending"}`}>
                <details>
                  <summary>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <strong>{stageLabel(name)}</strong>
                    <small>{stage ? runStatusLabel(stage.status) : "等待执行"}</small>
                  </summary>
                  <div>
                    <small>Provider：{stage?.provider ?? "—"}</small>
                    {stage?.model_id && <small>模型：{stage.model_id}</small>}
                    {stage?.summary && <p>{stage.summary}</p>}
                    {stage?.error_code && <code>{stage.error_code}</code>}
                  </div>
                </details>
              </li>
            );
          })}
        </ol>
      </details>

      {(run.can_execute || run.can_retry || run.recovery_required || run.status !== "completed") && (
        <div className="agent-command-row">
          {run.can_execute && onExecute && (
            <button type="button" disabled={busy} onClick={onExecute}>
              {busy ? "模型调用中…" : analysis ? "开始本次问题分析" : "显式发起真实只读运行"}
            </button>
          )}
          {run.can_retry && onRetry && <button type="button" disabled={busy} onClick={onRetry}>重试一次失败阶段</button>}
          {run.recovery_required && onRecover && <button type="button" disabled={busy} onClick={onRecover}>恢复中断预留</button>}
          {run.status !== "cancelled" && run.status !== "completed" && onCancel && (
            <button className="secondary-action" type="button" disabled={busy} onClick={onCancel}>取消运行</button>
          )}
        </div>
      )}

      {result.capability.status === "ready" && onReanalyze && ["completed", "cancelled", "needs_review"].includes(run.status) && (
        <div className="agent-command-row">
          <button className="secondary-action" type="button" disabled={busy} onClick={onReanalyze}>
            重新分析
          </button>
        </div>
      )}

      <details className="runtime-details">
        <summary>
          运行详情
        </summary>
        <div className="runtime-detail-grid">
          <Detail label="Provider / 模型" value={`${run.provider ?? result.capability.provider} / ${run.model_id ?? result.capability.model_id ?? "—"}`} />
          <Detail label="Case 类型" value={result.preview?.case_type ?? "warranty"} />
          {analysis ? <Detail label="运行绑定依据 SHA-256" value={analysis.basis_sha256} />
            : <Detail label="当前材料范围（非历史快照）" value={result.preview ? `${result.preview.evidence.segment_count} Segments · ${result.preview.evidence.utf8_bytes} UTF-8 bytes` : "暂时无法核对"} />}
          <Detail label="调用" value={`${run.usage.calls} / ${run.budget.max_calls}`} />
          <Detail label="Token" value={`${run.usage.total_tokens} / ${run.budget.max_total_tokens}`} />
          <Detail label="已计成本" value={`¥${(run.usage.cost_minor / 100).toFixed(2)}`} />
        </div>
        {answer && answer.citations.length > 0 && (
          <div className="citation-technical-list">
            <strong>引用定位</strong>
            {answer.citations.map((citation, index) => (
              <code key={`${citation.segment_id}:${citation.quote_start}:${citation.quote_end}:${index}`}>
                {index + 1}. Segment {shortId(citation.segment_id)} · 页 {citation.page ?? "—"} · quote {citation.quote_start}:{citation.quote_end}
              </code>
            ))}
          </div>
        )}
      </details>

      {result.run_history.length > 1 && (
        <details className="runtime-details run-history">
          <summary>历史分析<span>{result.run_history.length} 个版本</span></summary>
          <ol>
            {result.run_history.map((item) => (
              <li key={item.id}>
                <strong>版本 {item.version}</strong>
                <span>{liveWorkflowLabel(item.stage_order)} · {runStatusLabel(item.status)} · {item.provider ?? "—"} / {item.model_id ?? "—"}</span>
                <small>{item.answer_status ? answerStatusLabel(item.answer_status) : "尚无结论"} · {item.calls} 次调用 · {item.total_tokens} Token</small>
                <HistoryAnswer item={item} caseId={run.case_id} />
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

function BasisNotice({ status }: { status: AnalysisBasisStatus | "legacy" }) {
  const notices = {
    current: "依据与当前记录一致。回答仍是只读分析，不会自动确认事实。",
    changed: "依据已变化：此回答保留运行创建时的记录。请重新分析以采用最新事实与材料。",
    unavailable: "依据暂时无法核对：请勿把此回答当作当前结论。历史问题和答案仍保留。",
    legacy: "旧运行没有绑定事实快照，无法判断依据是否变化。",
  };
  return <p className={`analysis-basis analysis-basis-${status}`} role={status === "changed" || status === "unavailable" ? "status" : undefined}>{notices[status]}</p>;
}

function AnalysisFacts({ facts }: { facts: AnalysisFact[] }) {
  if (facts.length === 0) return <p>运行创建时没有可用的已核实事实，请结合材料引用与未知项判断。</p>;
  return <dl className="analysis-facts-list">{facts.map((fact) => (
    <div key={`${fact.claim_id}:${fact.version}`}>
      <dt>{claimFieldLabel(fact.field)}</dt>
      <dd>{claimValueLabel(fact.field, fact.value)}</dd>
      <small>{claimStatusLabel(fact.status)} · 版本 {fact.version} · {fact.usable_as_fact ? "可作为当前运行事实" : "不可作为已核实事实"}</small>
      {fact.conflict && <small>存在冲突，请先复核。</small>}
      {fact.evidence_status === "unavailable" && <small>运行创建时来源不可用，不能作为有效原文依据。</small>}
      {fact.evidence_status === "none" && <small>没有原文支持引用；如有人工确认，以上述操作记录为依据。</small>}
      {fact.confirmation && <small>
        {fact.confirmation_basis === "correction" ? "人工修正" : fact.confirmation_basis === "user" ? "人工确认" : "来源核对"} · {fact.confirmation.actor}<br />
        理由：{fact.confirmation.reason}<br />记录时间：{fact.confirmation.occurred_at}
      </small>}
      {fact.citations.length > 0 && <details className="analysis-fact-source">
        <summary>查看运行时的来源记录</summary>
        <p>以下为材料原文；人工修正后的事实以上方值和操作记录为准。</p>
        {fact.citations.map((citation, index) => <blockquote key={`${citation.segment_id}:${citation.quote_start}:${index}`}>
          <strong>{citation.source_name} · 第 {citation.sequence + 1} 段{citation.page !== null ? ` · 第 ${citation.page} 页` : ""}</strong>
          <p>{citation.quote_text}</p>
        </blockquote>)}
      </details>}
    </div>
  ))}</dl>;
}

function HistoryAnswer({ item, caseId }: { item: LiveAgentRunHistory; caseId: string }) {
  const [open, setOpen] = useState(false);
  return <details className="history-answer" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>查看本版问题与回答</summary>
    <HistoryAnswerContent item={item} caseId={caseId} loadCitations={open} />
  </details>;
}

export function HistoryAnswerContent({ item, caseId, loadCitations = true }: {
  item: LiveAgentRunHistory; caseId: string; loadCitations?: boolean;
}) {
  return <>
    <p><strong>原问题：</strong>{item.question ?? "旧运行未保存独立问题"}</p>
    <BasisNotice status={item.basis_status ?? "legacy"} />
    {item.answer ? <>
      {item.answer.status === "answered" ? item.answer.statements.map((statement, index) => <p key={index}>{statement}</p>)
        : <p>{item.answer.uncertainty_reason?.trim() || "当时材料不足以回答。"}</p>}
      {loadCitations && <AnswerCitations caseId={caseId} citations={item.answer.citations} />}
      {(item.answer.fact_references?.length ?? 0) > 0 && <AnalysisFacts facts={item.answer.fact_references!} />}
    </> : <p>此运行尚无可读取的回答。</p>}
  </>;
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function shortId(value: string): string {
  return value.length <= 18 ? value : `${value.slice(0, 8)}…${value.slice(-6)}`;
}
