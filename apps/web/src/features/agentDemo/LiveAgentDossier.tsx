import {
  agentStageOrder,
  answerStatusLabel,
  claimFieldLabel,
  claimStatusLabel,
  claimValueLabel,
  completedStageCount,
  liveProviderLabel,
  runStatusLabel,
  stageLabel,
} from "./presenters";
import type { LiveAgentResult } from "./types";

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

  return (
    <div className="live-dossier">
      <div className="live-dossier-status" aria-label="运行摘要">
        <span className={`live-status-mark live-status-${answer?.status ?? run.status}`}>
          {answer ? answerStatusLabel(answer.status) : runStatusLabel(run.status)}
        </span>
        <span>{completed} / 7 阶段完成</span>
        <span>{run.provider ?? liveProviderLabel(result.capability.provider)} · {run.model_id ?? result.capability.model_id}</span>
      </div>

      <div className="live-dossier-primary">
        <article className={`evidence-conclusion evidence-conclusion-${answer?.status ?? "pending"}`}>
          <p className="dossier-kicker">Agent 结论</p>
          <h3>{answer ? answerStatusLabel(answer.status) : "等待证据校验"}</h3>
          <div className="conclusion-copy">
            {answer?.status === "answered" && answer.statements.map((statement) => <p key={statement}>{statement}</p>)}
            {answer?.status === "unknown" && <p>当前材料不足以回答这个问题。</p>}
            {!answer && <p>七阶段尚未生成最终只读回答。</p>}
          </div>

          <div className="evidence-rail" aria-label="回答引用">
            <div className="evidence-rail-heading">
              <span aria-hidden="true" />
              <strong>{answer?.citations.length ?? 0} 条引用</strong>
            </div>
            {answer && answer.citations.length > 0 ? (
              <ol>
                {answer.citations.map((citation, index) => (
                  <li key={`${citation.segment_id}:${citation.quote_start}:${citation.quote_end}:${index}`}>
                    <span className="evidence-index">{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <strong>证据 {index + 1}</strong>
                      <small>{citation.page === null ? "来源材料" : `第 ${citation.page} 页`}</small>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="evidence-empty">未命中可支持当前回答的证据。</p>
            )}
          </div>
        </article>

        <section className="dossier-facts" aria-labelledby="dossier-facts-title">
          <div className="dossier-section-heading">
            <div>
              <p className="dossier-kicker">未经确认</p>
              <h3 id="dossier-facts-title">候选事实</h3>
            </div>
            <span>{run.claims.length} 项</span>
          </div>
          {run.claims.length > 0 ? (
            <dl>
              {run.claims.map((claim) => (
                <div key={claim.id}>
                  <dt>{claimFieldLabel(claim.field)}</dt>
                  <dd>{claimValueLabel(claim.field, claim.value)}</dd>
                  <small>{claimStatusLabel(claim.status)} · 模型生成，尚未确认</small>
                </div>
              ))}
            </dl>
          ) : (
            <p>本次没有生成候选事实。</p>
          )}
        </section>
      </div>

      <section className="stage-rail" aria-labelledby="stage-rail-title">
        <div className="dossier-section-heading">
          <div>
            <p className="dossier-kicker">可恢复工作流</p>
            <h3 id="stage-rail-title">七阶段</h3>
          </div>
          <strong>{completed} / 7</strong>
        </div>
        <ol>
          {agentStageOrder.map((name, index) => {
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
      </section>

      {(run.can_execute || run.can_retry || run.recovery_required || run.status !== "completed") && (
        <div className="agent-command-row">
          {run.can_execute && onExecute && (
            <button type="button" disabled={busy} onClick={onExecute}>
              {busy ? "模型调用中…" : "显式发起真实只读运行"}
            </button>
          )}
          {run.can_retry && onRetry && <button type="button" disabled={busy} onClick={onRetry}>重试一次失败阶段</button>}
          {run.recovery_required && onRecover && <button type="button" disabled={busy} onClick={onRecover}>恢复中断预留</button>}
          {run.status !== "cancelled" && run.status !== "completed" && onCancel && (
            <button className="secondary-action" type="button" disabled={busy} onClick={onCancel}>取消运行</button>
          )}
        </div>
      )}

      {onReanalyze && ["completed", "cancelled", "needs_review"].includes(run.status) && (
        <div className="agent-command-row">
          <button className="secondary-action" type="button" disabled={busy} onClick={onReanalyze}>
            重新分析
          </button>
        </div>
      )}

      <details className="runtime-details">
        <summary>
          运行详情
          <span>{run.usage.calls} 次调用 · {run.usage.total_tokens} Token</span>
        </summary>
        <div className="runtime-detail-grid">
          <Detail label="Provider / 模型" value={`${run.provider ?? result.capability.provider} / ${run.model_id ?? result.capability.model_id ?? "—"}`} />
          <Detail label="Case 类型" value={result.preview.case_type} />
          <Detail label="证据范围" value={`${result.preview.evidence.segment_count} Segments · ${result.preview.evidence.utf8_bytes} UTF-8 bytes`} />
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
                <span>{runStatusLabel(item.status)} · {item.provider ?? "—"} / {item.model_id ?? "—"}</span>
                <small>{item.answer_status ? answerStatusLabel(item.answer_status) : "尚无结论"} · {item.calls} 次调用 · {item.total_tokens} Token</small>
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function shortId(value: string): string {
  return value.length <= 18 ? value : `${value.slice(0, 8)}…${value.slice(-6)}`;
}
