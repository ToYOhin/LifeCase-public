import { type ReactNode, useEffect, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import {
  cancelAgentRun,
  createAgentRun,
  executeAgentRun,
  getAgentRun,
  recoverAgentRun,
  retryAgentRun,
} from "./api";
import {
  agentDemoView,
  agentStageOrder,
  runStatusLabel,
  stageLabel,
} from "./presenters";
import type {
  AgentDemoLoadState,
  AgentDemoResult,
  AgentStageName,
} from "./types";

interface AgentDemoPanelProps {
  caseId: string | null;
  caseTitle: string | null;
  caseType: string | null;
  refreshKey: number;
  onChanged: () => Promise<void>;
}

export function AgentDemoPanel({
  caseId,
  caseTitle,
  caseType,
  refreshKey,
  onChanged,
}: AgentDemoPanelProps) {
  const [result, setResult] = useState<AgentDemoResult | null>(null);
  const [state, setState] = useState<AgentDemoLoadState>("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    if (!caseId) {
      setResult(null);
      setState("idle");
      return;
    }
    void load(caseId);
  }, [caseId, refreshKey]);

  async function load(targetCaseId: string) {
    setState("loading");
    try {
      setResult(await getAgentRun(targetCaseId));
      setState("ready");
    } catch (reason) {
      setResult(null);
      setState(
        reason instanceof LifeCaseApiError && reason.code === "network_error"
          ? "offline"
          : "error",
      );
    }
  }

  async function start() {
    if (!caseId) return;
    setState("running");
    setError(null);
    try {
      setResult(await createAgentRun(caseId));
      const completed = await executeAgentRun(caseId);
      setResult(completed);
      setState("ready");
      await onChanged();
    } catch (reason) {
      handleCommandError(reason);
    }
  }

  async function command(
    operation: (targetCaseId: string) => Promise<AgentDemoResult>,
  ) {
    if (!caseId) return;
    setState("running");
    setError(null);
    try {
      const next = await operation(caseId);
      setResult(next);
      setState("ready");
      await onChanged();
    } catch (reason) {
      handleCommandError(reason);
    }
  }

  function handleCommandError(reason: unknown) {
    setState(
      reason instanceof LifeCaseApiError && reason.code === "network_error"
        ? "offline"
        : "error",
    );
    setError(
      reason instanceof LifeCaseApiError
        ? agentErrorGuidance(reason.code)
        : "演示运行已安全停止，请重新读取持久化状态。",
    );
  }

  const view = agentDemoView(caseId, state, result);
  const run = result?.run ?? null;
  const supported = Boolean(
    !caseType || result?.capability.supported_case_types.some((item) => item === caseType),
  );

  return (
    <section className="agent-demo" aria-labelledby="agent-demo-title">
      <div className="agent-demo-head">
        <div>
          <p className="section-label">PORTFOLIO AGENT DEMO</p>
          <h2 id="agent-demo-title">证据 Agent 演示</h2>
          {caseTitle && <p>{caseTitle}</p>}
        </div>
        <div className="agent-demo-provider" role="note">
          <strong>确定性演示 Provider</strong>
          <span>不是实时模型输出</span>
          <small>无外部网络或写入动作</small>
        </div>
      </div>

      {view === "unselected" && (
        <AgentState title="尚未选择案件">从本地台账打开一个案件。</AgentState>
      )}
      {view === "loading" && (
        <AgentState title="正在恢复 Agent 运行" role="status">
          正在按 Case 范围读取 SQLite 中的 run、阶段和安全 trace。
        </AgentState>
      )}
      {view === "disabled" && (
        <AgentState title="演示能力默认关闭">
          现有案件读取、人工事实、导出和备份不受影响。需要在本地启动配置中明确启用。
        </AgentState>
      )}
      {view === "offline" && (
        <AgentState title="本地 API 未连接" role="alert">
          已保存状态没有丢失；服务恢复后重新读取。
          <button type="button" onClick={() => caseId && void load(caseId)}>重新连接</button>
        </AgentState>
      )}
      {view === "failure" && (
        <AgentState title="Agent 已安全停止" role="alert">
          {error ?? "没有使用不完整结果；请重新校验持久化状态。"}
          <button type="button" onClick={() => caseId && void load(caseId)}>重新读取</button>
        </AgentState>
      )}

      {(view === "ready" || view === "running" || view === "completed") && result && (
        <>
          <div className="agent-demo-notice">
            <span>只读工具：evidence_context.search.v1</span>
            <span>Agent 只创建 candidate / inferred Claim；确认和行动仍由你决定</span>
          </div>

          {!supported ? (
            <AgentState title="此案件类型尚无已审阅的抽取 Schema">
              当前演示只支持 warranty Case；不会把保修字段写入其他案件类型。
            </AgentState>
          ) : !run ? (
            <div className="agent-demo-start">
              <div>
                <h3>运行一次可审计的本地演示</h3>
                <p>使用案件目标检索当前材料，经七个固定阶段生成候选事实和有引用的回答。</p>
              </div>
              <button type="button" onClick={() => void start()} disabled={view === "running"}>
                {view === "running" ? "正在创建运行…" : "开始确定性演示"}
              </button>
            </div>
          ) : (
            <>
              <div className="agent-run-summary">
                <div><span>运行状态</span><strong>{runStatusLabel(run.status)}</strong></div>
                <div><span>模型调用</span><strong>{run.usage.calls} / {run.budget.max_calls}</strong></div>
                <div><span>Token</span><strong>{run.usage.total_tokens} / {run.budget.max_total_tokens}</strong></div>
                <div><span>成本上限</span><strong>¥{(run.budget.max_cost_minor / 100).toFixed(2)}</strong></div>
              </div>

              <ol className="agent-stage-grid" aria-label="Agent 七阶段状态">
                {agentStageOrder.map((name, index) => {
                  const trace = run.stages.find((item) => item.name === name);
                  return (
                    <li key={name} className={`agent-stage stage-${trace?.status ?? "pending"}`}>
                      <span>{String(index + 1).padStart(2, "0")}</span>
                      <div>
                        <strong>{stageLabel(name)}</strong>
                        <small>{trace ? runStatusLabel(trace.status) : "等待执行"}</small>
                        {trace?.summary && <p>{trace.summary}</p>}
                        {trace?.error_code && <code>{trace.error_code}</code>}
                      </div>
                    </li>
                  );
                })}
              </ol>

              <div className="agent-command-row">
                {run.can_execute && (
                  <button type="button" onClick={() => void command(executeAgentRun)} disabled={view === "running"}>
                    继续七阶段运行
                  </button>
                )}
                {run.can_retry && (
                  <button type="button" onClick={() => void command(retryAgentRun)} disabled={view === "running"}>
                    重试一次可恢复失败
                  </button>
                )}
                {run.recovery_required && (
                  <button type="button" onClick={() => void command(recoverAgentRun)} disabled={view === "running"}>
                    恢复中断状态
                  </button>
                )}
                {!(["cancelled", "completed"] as string[]).includes(run.status) && (
                  <button className="secondary-action" type="button" onClick={() => void command(cancelAgentRun)} disabled={view === "running"}>
                    取消未完成运行
                  </button>
                )}
              </div>

              <AgentResults result={result} />
            </>
          )}
        </>
      )}
    </section>
  );
}

function AgentResults({ result }: { result: AgentDemoResult }) {
  const run = result.run;
  if (!run) return null;
  return (
    <div className="agent-results">
      <article>
        <h3>候选 / 推断事实</h3>
        {run.claims.length === 0 ? <p>本次没有生成可写入的候选事实。</p> : (
          <ul>
            {run.claims.map((claim) => (
              <li key={claim.id}>
                <strong>{claim.field}</strong>
                <span>{valueLabel(claim.value)}</span>
                <small>{claim.status} · {claim.confidence === null ? "无置信度" : `${Math.round(claim.confidence * 100)}%`}</small>
              </li>
            ))}
          </ul>
        )}
      </article>
      <article>
        <h3>RAG 校验结果</h3>
        {!run.answer ? <p>validate 阶段尚未完成。</p> : (
          <>
            <strong className={`agent-answer-${run.answer.status}`}>
              {run.answer.status === "answered" ? "有权威材料支持" : "未知 / 无匹配"}
            </strong>
            {run.answer.statements.map((statement) => <p key={statement}>{statement}</p>)}
            {run.answer.uncertainty_reason && <p>{run.answer.uncertainty_reason}</p>}
            {run.answer.citations.map((citation, index) => (
              <code key={`${citation.segment_id}:${citation.quote_start}:${citation.quote_end}:${index}`}>
                Segment {shortId(citation.segment_id)} · 页 {citation.page ?? "—"} · quote {citation.quote_start}:{citation.quote_end}
              </code>
            ))}
          </>
        )}
        {run.tool_calls.map((tool) => (
          <p className="agent-tool-trace" key={tool.name}>
            {tool.name} · 风险 {tool.risk} · 调用 {tool.call_count} 次 · {tool.result === "match" ? "命中" : "无匹配"}
          </p>
        ))}
      </article>
    </div>
  );
}

function AgentState({ title, role, children }: { title: string; role?: "status" | "alert"; children: ReactNode }) {
  return <div className="agent-demo-state" role={role}><span>AI</span><div><h3>{title}</h3><div>{children}</div></div></div>;
}

function valueLabel(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function shortId(value: string): string {
  return value.length <= 18 ? value : `${value.slice(0, 8)}…${value.slice(-6)}`;
}

function agentErrorGuidance(code: string): string {
  if (code === "agent_integrity_failure") return "证据或阶段哈希已变化，运行已失败关闭。";
  if (code === "agent_budget_exhausted") return "本次运行达到预算上限，没有继续调用。";
  if (code === "agent_timeout") return "阶段超时并已保存为可恢复状态。";
  if (code === "agent_run_not_executable") return "当前状态不允许重复执行该命令，请重新读取。";
  return "演示运行已安全停止，请重新读取持久化状态。";
}
