import { type ReactNode, useEffect, useRef, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import {
  authorizeLiveAgent, cancelLiveAgent, executeLiveAgent, getLiveAgent,
  previewLiveAgent, recoverLiveAgent, retryLiveAgent,
} from "./api";
import { liveProviderLabel } from "./presenters";
import { LiveAgentDossier } from "./LiveAgentDossier";
import type { LiveAgentResult } from "./types";
import { AnalysisActionDraft } from "../actions/AnalysisActionDraft";
import { originalActionQuestion, type ActionFollowUpTarget } from "../actions/actionFollowUpState";

interface LiveAgentPanelProps {
  caseId: string | null;
  caseTitle: string | null;
  caseType: string | null;
  refreshKey: number;
  onChanged: () => Promise<void>;
  actionFollowUp?: ActionFollowUpTarget | null;
  onOpenAction?: (caseId: string, actionId: string) => void;
}

type LoadState = "idle" | "loading" | "previewing" | "ready" | "running" | "offline" | "error";

export function LiveAgentPanel({ caseId, caseTitle, caseType, refreshKey, onChanged, actionFollowUp, onOpenAction }: LiveAgentPanelProps) {
  const [loaded, setLoaded] = useState<{ caseId: string; result: LiveAgentResult } | null>(null);
  const [state, setState] = useState<LoadState>("idle");
  const [question, setQuestion] = useState("");
  const [reviewed, setReviewed] = useState<{ caseId: string; question: string; hash: string } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [reanalyzeOpen, setReanalyzeOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadSequence = useRef(0);
  const working = useRef(false);
  const currentCase = useRef(caseId);
  currentCase.current = caseId;
  const result = loaded?.caseId === caseId ? loaded.result : null;

  useEffect(() => {
    setConfirmed(false);
    setReviewed(null);
    setReanalyzeOpen(false);
    setError(null);
    setQuestion("");
    working.current = false;
    if (!caseId || (caseType && caseType !== "warranty")) {
      setLoaded(null);
      setState(caseId ? "ready" : "idle");
      return;
    }
    void load(caseId);
    return () => { loadSequence.current += 1; };
  }, [caseId, caseType, refreshKey]);

  function isCurrent(sequence: number, targetCaseId: string) {
    return sequence === loadSequence.current && currentCase.current === targetCaseId;
  }

  function accept(response: LiveAgentResult, targetCaseId: string) {
    if (response.run && response.run.case_id !== targetCaseId) {
      throw new LifeCaseApiError("agent_integrity_failure", "分析不属于当前案件", false);
    }
    setLoaded({ caseId: targetCaseId, result: response });
  }

  async function load(targetCaseId: string) {
    const sequence = ++loadSequence.current;
    working.current = true;
    setState("loading");
    setLoaded(null);
    setReviewed(null);
    setConfirmed(false);
    setError(null);
    try {
      const response = await getLiveAgent(targetCaseId);
      if (!isCurrent(sequence, targetCaseId)) return;
      accept(response, targetCaseId);
      setQuestion(response.run?.analysis?.question ?? "");
      setState("ready");
    } catch (reason) {
      if (isCurrent(sequence, targetCaseId)) handleError(reason);
    } finally {
      if (isCurrent(sequence, targetCaseId)) working.current = false;
    }
  }

  function updateQuestion(value: string) {
    loadSequence.current += 1;
    working.current = false;
    setQuestion(value);
    setReviewed(null);
    setConfirmed(false);
    setError(null);
    setState("ready");
  }

  async function reviewQuestion() {
    const targetQuestion = question.trim();
    if (!caseId || !targetQuestion || working.current) return;
    const targetCaseId = caseId;
    const sequence = ++loadSequence.current;
    working.current = true;
    setState("previewing");
    setReviewed(null);
    setConfirmed(false);
    setError(null);
    try {
      const response = await previewLiveAgent(targetCaseId, targetQuestion);
      if (!isCurrent(sequence, targetCaseId)) return;
      if (response.preview?.question !== targetQuestion) {
        throw new LifeCaseApiError("agent_integrity_failure", "预览问题与当前输入不一致", false);
      }
      accept(response, targetCaseId);
      setReviewed({ caseId: targetCaseId, question: targetQuestion, hash: response.preview.preview_sha256 });
      setState("ready");
    } catch (reason) {
      if (isCurrent(sequence, targetCaseId)) handleError(reason);
    } finally {
      if (isCurrent(sequence, targetCaseId)) working.current = false;
    }
  }

  async function authorize() {
    if (!caseId || !reviewed || reviewed.caseId !== caseId || reviewed.question !== question.trim() || !confirmed || working.current) return;
    const targetCaseId = caseId;
    const sequence = ++loadSequence.current;
    working.current = true;
    setState("running");
    setError(null);
    setConfirmed(false);
    try {
      const response = await authorizeLiveAgent(targetCaseId, reviewed.hash, reviewed.question);
      if (!isCurrent(sequence, targetCaseId)) return;
      if (response.run?.analysis?.question !== reviewed.question) {
        throw new LifeCaseApiError("agent_integrity_failure", "运行未绑定本次问题", false);
      }
      accept(response, targetCaseId);
      setReviewed(null);
      setReanalyzeOpen(false);
      setState("ready");
      await onChanged();
    } catch (reason) {
      if (isCurrent(sequence, targetCaseId)) handleError(reason);
    } finally {
      if (isCurrent(sequence, targetCaseId)) working.current = false;
    }
  }

  async function command(operation: (targetCaseId: string, runId: string) => Promise<LiveAgentResult>) {
    if (!caseId || !result?.run || working.current) return;
    const targetCaseId = caseId;
    const targetRunId = result.run.id;
    const sequence = ++loadSequence.current;
    working.current = true;
    setState("running");
    setReviewed(null);
    setConfirmed(false);
    setError(null);
    try {
      const response = await operation(targetCaseId, targetRunId);
      if (!isCurrent(sequence, targetCaseId)) return;
      if (response.run?.id !== targetRunId) {
        throw new LifeCaseApiError("live_agent_run_stale", "分析运行已变化", false);
      }
      accept(response, targetCaseId);
      setState("ready");
      await onChanged();
    } catch (reason) {
      if (isCurrent(sequence, targetCaseId)) handleError(reason);
    } finally {
      if (isCurrent(sequence, targetCaseId)) working.current = false;
    }
  }

  function handleError(reason: unknown) {
    const apiError = reason instanceof LifeCaseApiError ? reason : null;
    setReviewed(null);
    setConfirmed(false);
    setState(apiError?.code === "network_error" ? "offline" : "error");
    setError(apiError ? liveAgentErrorGuidance(apiError.code) : "Live Agent 已安全停止。");
  }

  const run = result?.run ?? null;
  const busy = state === "running" || state === "previewing";
  const previewReady = reviewed && reviewed.caseId === caseId && reviewed.question === question.trim();
  const canAsk = !run || reanalyzeOpen;
  const followUp = actionFollowUp?.caseId === caseId ? actionFollowUp : null;
  const originalQuestion = followUp && caseId ? originalActionQuestion(followUp, caseId, result, state === "ready") : null;
  const canRestartOriginal = !!originalQuestion && result?.capability.status === "ready" &&
    (!run || ["completed", "cancelled", "needs_review"].includes(run.status));

  return (
    <section className="agent-demo live-agent" aria-labelledby="live-agent-title">
      <div className="agent-demo-head">
        <div>
          <h2 id="live-agent-title">只读证据分析</h2>
          {caseTitle && <p>{caseTitle}</p>}
        </div>
        <details className="agent-demo-provider" role="note">
          <summary>模型信息</summary>
          <div>
            <strong>{liveProviderLabel(result?.capability.provider ?? "disabled")}</strong>
            <span>{result?.capability.model_id ?? "未配置模型"}</span>
            <small>回答本次问题，不修改事实或执行外部操作。</small>
          </div>
        </details>
      </div>
      {!caseId && <LiveState title="尚未选择案件">先从本地台账打开一个案件。</LiveState>}
      {followUp && <section className="analysis-action-draft" aria-label="旧待办重新分析入口">
        <h3>旧待办需要重新分析</h3>
        <p>旧正文和历史仍保留。新待办需重新审批，旧待办不会自动取消。</p>
        <small>原待办：<code>{followUp.actionId}</code></small>
        {originalQuestion ? <p><strong>原待办绑定的问题：</strong>{originalQuestion}</p>
          : <p role="status">原问题尚未从本案运行历史中核对成功。不会改用其他运行的问题或案件目标。</p>}
        <div className="analysis-action-controls">
          <button type="button" disabled={!canRestartOriginal} onClick={() => {
            if (!canRestartOriginal || !originalQuestion) return;
            updateQuestion(originalQuestion); setReanalyzeOpen(true);
          }}>以原问题重新分析</button>
          {onOpenAction && <button type="button" disabled={busy} onClick={() => onOpenAction(followUp.caseId, followUp.actionId)}>
            返回原待办，决定保留或取消
          </button>}
        </div>
      </section>}
      {caseId && caseType !== "warranty" && <LiveState title="案件类型不受支持">当前只读分析支持保修案件。</LiveState>}
      {caseId && caseType === "warranty" && state === "loading" && (
        <LiveState title="正在读取分析记录" role="status">正在读取已保存的问题、运行和当前依据状态。</LiveState>
      )}
      {caseId && caseType === "warranty" && state === "offline" && (
        <LiveState title="本地 API 未连接" role="alert">
          当前预览已失效，请重新读取运行状态后核对。
          <button type="button" onClick={() => void load(caseId)}>重新连接</button>
        </LiveState>
      )}
      {caseId && caseType === "warranty" && state === "error" && (
        <LiveState title="Live Agent 已安全停止" role="alert">
          {error}<button type="button" onClick={() => void load(caseId)}>重新读取记录</button>
        </LiveState>
      )}
      {caseId && caseType === "warranty" && result && state !== "loading" && (
        <>
          {run && (
            <LiveAgentDossier
              key={`${caseId}:${run.id}`} result={result} busy={busy}
              onExecute={result.capability.status === "ready" ? () => void command(executeLiveAgent) : undefined}
              onRetry={result.capability.status === "ready" ? () => void command(retryLiveAgent) : undefined}
              onRecover={() => void command(recoverLiveAgent)} onCancel={() => void command(cancelLiveAgent)}
              onReanalyze={result.capability.status === "ready" ? () => { setConfirmed(false); setReviewed(null); setReanalyzeOpen(true); } : undefined}
            />
          )}
          {result.capability.status !== "ready" && (
            <LiveState title={result.capability.status === "disabled" ? "能力默认关闭" : "服务端组合未就绪"}>
              服务端当前不允许创建或执行新的模型分析，已有运行仍可回看。
            </LiveState>
          )}
          {canAsk && result.capability.status === "ready" && (
            <div className="live-question-form">
              <label htmlFor="live-agent-question">本次问题</label>
              <textarea id="live-agent-question" rows={3} maxLength={2000} value={question}
                disabled={state === "running"} onChange={(event) => updateQuestion(event.currentTarget.value)} />
              <p>分析会保留本次问题和当时依据；案件目标不变。</p>
              <div className="reanalyze-actions">
                <button type="button" disabled={!question.trim() || busy} onClick={() => void reviewQuestion()}>
                  {state === "previewing" ? "正在核对…" : "核对本次问题与依据"}
                </button>
                {run && <button type="button" className="secondary-action" disabled={state === "running"} onClick={() => {
                  loadSequence.current += 1; working.current = false; setState("ready");
                  setReanalyzeOpen(false); setReviewed(null); setConfirmed(false); setError(null);
                  setQuestion(run.analysis?.question ?? "");
                }}>取消新分析</button>}
              </div>
              {previewReady && result.preview && (
                <>
                  <div className="live-reviewed-question" role="status"><strong>本次已核对问题</strong><p>{reviewed.question}</p></div>
                  <div className="live-agent-preview" aria-label="本次问题调用前预览">
                    <div><span>服务商 / 模型</span><strong>{result.capability.provider} / {result.capability.model_id ?? "—"}</strong></div>
                    <div><span>事务类型</span><strong>{result.preview.case_type}</strong></div>
                    <div><span>材料范围</span><strong>{result.preview.evidence.segment_count} 个片段 · {result.preview.evidence.utf8_bytes} UTF-8 字节</strong></div>
                    <div><span>最大调用</span><strong>{result.preview.budget.max_calls}</strong></div>
                    <div><span>Token 上限</span><strong>{result.preview.budget.max_input_tokens} 输入 / {result.preview.budget.max_output_tokens} 输出</strong></div>
                    <div><span>成本上限</span><strong>¥{(result.preview.budget.max_cost_minor / 100).toFixed(2)}</strong></div>
                  </div>
                  <p className="live-agent-boundary">根据已核实事实和材料回答，不会确认事实或执行外部操作。</p>
                  <div className="live-agent-authorize">
                    <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.currentTarget.checked)} disabled={busy} />
                      我已核对本次问题、服务商、模型、依据范围和预算，同意创建一次只读分析。
                    </label>
                    <button type="button" disabled={!confirmed || busy} onClick={() => void authorize()}>
                      {state === "running" ? "正在创建…" : run ? "授权并创建新版本" : "授权并创建队列"}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}
      {caseId && caseType === "warranty" && <AnalysisActionDraft caseId={caseId} run={run}
        available={state === "ready" && result !== null} refreshKey={refreshKey}
        historyRunIds={result?.run_history.map((item) => item.id) ?? []} onSaved={onChanged} onOpenAction={onOpenAction} />}
    </section>
  );
}

function LiveState({ title, role, children }: { title: string; role?: "status" | "alert"; children: ReactNode }) {
  return <div className="agent-demo-state" role={role}><span>AI</span><div><h3>{title}</h3><div>{children}</div></div></div>;
}

function liveAgentErrorGuidance(code: string): string {
  if (code === "live_agent_run_stale") return "当前案件的运行已变化。请重新读取记录，核对问题后再操作。";
  if (code === "live_agent_preview_stale") return "问题、事实、材料或服务端配置已变化，请重新核对本次问题与依据。";
  if (code === "live_agent_client_unavailable") return "进程级凭据客户端未注入，没有发起网络调用。";
  if (code === "agent_budget_exhausted") return "运行已达到服务端硬预算，没有继续调用。";
  if (code === "agent_integrity_failure") return "证据或阶段权威校验失败，运行已失败关闭。";
  return "Live Agent 已安全停止；请重新读取运行记录并核对本次问题。";
}
