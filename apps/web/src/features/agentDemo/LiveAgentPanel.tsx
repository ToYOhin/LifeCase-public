import { type ReactNode, useEffect, useState } from "react";

import { LifeCaseApiError } from "../intake/api";
import {
  authorizeLiveAgent,
  cancelLiveAgent,
  executeLiveAgent,
  getLiveAgent,
  recoverLiveAgent,
  retryLiveAgent,
} from "./api";
import {
  liveProviderLabel,
} from "./presenters";
import { LiveAgentDossier } from "./LiveAgentDossier";
import type { LiveAgentResult } from "./types";

interface LiveAgentPanelProps {
  caseId: string | null;
  caseTitle: string | null;
  caseType: string | null;
  refreshKey: number;
  onChanged: () => Promise<void>;
}

type LoadState = "idle" | "loading" | "ready" | "running" | "offline" | "error";

export function LiveAgentPanel({
  caseId,
  caseTitle,
  caseType,
  refreshKey,
  onChanged,
}: LiveAgentPanelProps) {
  const [result, setResult] = useState<LiveAgentResult | null>(null);
  const [state, setState] = useState<LoadState>("idle");
  const [confirmed, setConfirmed] = useState(false);
  const [reanalyzeOpen, setReanalyzeOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setConfirmed(false);
    setReanalyzeOpen(false);
    setError(null);
    if (!caseId) {
      setResult(null);
      setState("idle");
      return;
    }
    if (caseType && caseType !== "warranty") {
      setResult(null);
      setState("ready");
      return;
    }
    void load(caseId);
  }, [caseId, caseType, refreshKey]);

  async function load(targetCaseId: string) {
    setState("loading");
    try {
      setResult(await getLiveAgent(targetCaseId));
      setState("ready");
    } catch (reason) {
      handleError(reason);
    }
  }

  async function authorize() {
    if (!caseId || !result || !confirmed) return;
    setState("running");
    setError(null);
    try {
      setResult(await authorizeLiveAgent(caseId, result.preview.preview_sha256));
      setConfirmed(false);
      setReanalyzeOpen(false);
      setState("ready");
      await onChanged();
    } catch (reason) {
      handleError(reason);
    }
  }

  async function command(
    operation: (targetCaseId: string) => Promise<LiveAgentResult>,
  ) {
    if (!caseId) return;
    setState("running");
    setError(null);
    try {
      setResult(await operation(caseId));
      setState("ready");
      await onChanged();
    } catch (reason) {
      handleError(reason);
    }
  }

  function handleError(reason: unknown) {
    const apiError = reason instanceof LifeCaseApiError ? reason : null;
    setState(apiError?.code === "network_error" ? "offline" : "error");
    setError(apiError ? liveAgentErrorGuidance(apiError.code) : "Live Agent 已安全停止。");
  }

  const run = result?.run ?? null;
  const busy = state === "running";

  return (
    <section className="agent-demo live-agent" aria-labelledby="live-agent-title">
      <div className="agent-demo-head">
        <div>
          <p className="section-label">LIVE AGENT / EVIDENCE DOSSIER</p>
          <h2 id="live-agent-title">只读证据分析</h2>
          {caseTitle && <p>{caseTitle}</p>}
          <div className="live-agent-badges" aria-label="演示边界">
            <span>真实模型能力</span>
            <span>合成数据</span>
            <span>只读分析</span>
          </div>
        </div>
        <details className="agent-demo-provider" role="note">
          <summary>运行技术信息</summary>
          <div>
            <strong>{liveProviderLabel(result?.capability.provider ?? "disabled")}</strong>
            <span>{result?.capability.model_id ?? "未配置模型"}</span>
            <small>只生成候选事实与回答 · 不执行外部动作</small>
          </div>
        </details>
      </div>

      {!caseId && <LiveState title="尚未选择案件">先从本地台账打开一个案件。</LiveState>}
      {caseId && caseType !== "warranty" && (
        <LiveState title="案件类型不受支持">
          第一版只允许已审阅的 warranty Case；当前案件已失败关闭。
        </LiveState>
      )}
      {caseId && caseType === "warranty" && state === "loading" && (
        <LiveState title="正在计算服务端预览" role="status">
          正在读取精确的 Segment 范围和不可由浏览器修改的预算。
        </LiveState>
      )}
      {caseId && caseType === "warranty" && state === "offline" && (
        <LiveState title="本地 API 未连接" role="alert">
          没有发起模型调用。
          <button type="button" onClick={() => void load(caseId)}>重新连接</button>
        </LiveState>
      )}
      {caseId && caseType === "warranty" && state === "error" && (
        <LiveState title="Live Agent 已安全停止" role="alert">
          {error}
          <button type="button" onClick={() => void load(caseId)}>重新读取预览</button>
        </LiveState>
      )}

      {caseId && caseType === "warranty" && result && state !== "loading" && (
        <>
          {!run && result.capability.status === "ready" && (
            <>
              <div className="live-agent-preview" aria-label="真实模型调用前预览">
                <div><span>Provider / 模型</span><strong>{result.capability.provider} / {result.capability.model_id ?? "—"}</strong></div>
                <div><span>Case 类型</span><strong>{result.preview.case_type}</strong></div>
                <div><span>证据范围</span><strong>{result.preview.evidence.segment_count} Segments · {result.preview.evidence.utf8_bytes} UTF-8 bytes</strong></div>
                <div><span>最大调用</span><strong>{result.preview.budget.max_calls}</strong></div>
                <div><span>Token 上限</span><strong>{result.preview.budget.max_input_tokens} 输入 / {result.preview.budget.max_output_tokens} 输出</strong></div>
                <div><span>成本上限</span><strong>¥{(result.preview.budget.max_cost_minor / 100).toFixed(2)}</strong></div>
              </div>
              <p className="live-agent-boundary">
                本次运行仅可生成候选或推断事实与只读回答，不确认事实，也不执行外部行动。
              </p>
            </>
          )}

          {result.capability.status !== "ready" ? (
            <LiveState title={result.capability.status === "disabled" ? "能力默认关闭" : "服务端组合未就绪"}>
              {result.capability.status === "disabled"
                ? "服务器端只读 Agent 能力保持默认关闭。"
                : "服务器端组合尚未满足运行条件。"} 当前页面不会尝试调用网络。
            </LiveState>
          ) : !run ? (
            <div className="live-agent-authorize">
              <label>
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.currentTarget.checked)}
                  disabled={busy}
                />
                我已核对以上 Provider、模型、证据范围和硬预算，并授权创建一次只读运行。
              </label>
              <button type="button" disabled={!confirmed || busy} onClick={() => void authorize()}>
                {busy ? "正在创建…" : "授权并创建队列"}
              </button>
            </div>
          ) : (
            <LiveAgentDossier
              result={result}
              busy={busy}
              onExecute={() => void command(executeLiveAgent)}
              onRetry={() => void command(retryLiveAgent)}
              onRecover={() => void command(recoverLiveAgent)}
              onCancel={() => void command(cancelLiveAgent)}
              onReanalyze={() => {
                setConfirmed(false);
                setReanalyzeOpen(true);
              }}
            />
          )}
          {run && reanalyzeOpen && (
            <div className="live-agent-authorize reanalyze-confirm">
              <label>
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.currentTarget.checked)}
                  disabled={busy}
                />
                我已核对当前 Provider、模型、证据范围和预算，并授权保留旧 Run 后创建新的分析版本。
              </label>
              <div className="reanalyze-actions">
                <button type="button" disabled={!confirmed || busy} onClick={() => void authorize()}>
                  {busy ? "正在创建…" : "授权并创建新版本"}
                </button>
                <button className="secondary-action" type="button" disabled={busy} onClick={() => setReanalyzeOpen(false)}>
                  取消
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function LiveState({ title, role, children }: { title: string; role?: "status" | "alert"; children: ReactNode }) {
  return <div className="agent-demo-state" role={role}><span>AI</span><div><h3>{title}</h3><div>{children}</div></div></div>;
}

function liveAgentErrorGuidance(code: string): string {
  if (code === "live_agent_preview_stale") return "证据或服务端配置已变化，请重新核对预览。";
  if (code === "live_agent_client_unavailable") return "进程级凭据客户端未注入，没有发起网络调用。";
  if (code === "agent_budget_exhausted") return "运行已达到服务端硬预算，没有继续调用。";
  if (code === "agent_integrity_failure") return "证据或阶段权威校验失败，运行已失败关闭。";
  return "Live Agent 已安全停止；请重新读取服务端预览。";
}
