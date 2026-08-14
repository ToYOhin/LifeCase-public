import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useState,
} from "react";

import { LifeCaseApiError } from "../intake/api";
import {
  confirmReviewClaim,
  correctReviewClaim,
  createManualClaim,
  getEvidenceReview,
  getSourceSegments,
  rejectReviewClaim,
} from "./api";
import {
  claimValueJson,
  claimValueLabel,
  confidenceLabel,
  fieldLabel,
  nextReviewIndex,
  parseCorrectionValue,
  reviewShortcut,
  reviewView,
} from "./presenters";
import type {
  EvidenceReviewItem,
  EvidenceReviewResult,
  ReviewCitation,
  ReviewLoadState,
  SourceSegment,
} from "./types";
import { LazyWorkflowTool } from "../workbench/LazyWorkflowTool";

interface EvidenceReviewPanelProps {
  caseId: string | null;
  caseTitle: string | null;
  claimFields: string[];
  refreshKey: number;
  onReviewChanged: () => Promise<void>;
}

type Decision = "confirm" | "correct" | "reject";

export function EvidenceReviewPanel({
  caseId,
  caseTitle,
  claimFields,
  refreshKey,
  onReviewChanged,
}: EvidenceReviewPanelProps) {
  const [result, setResult] = useState<EvidenceReviewResult | null>(null);
  const [state, setState] = useState<ReviewLoadState>("idle");
  const [index, setIndex] = useState(0);
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  const [correctionJson, setCorrectionJson] = useState("");
  const [showCorrection, setShowCorrection] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [segments, setSegments] = useState<SourceSegment[]>([]);
  const [manualField, setManualField] = useState("");
  const [manualSegmentId, setManualSegmentId] = useState("");
  const [manualQuote, setManualQuote] = useState("");
  const [manualValue, setManualValue] = useState("");

  useEffect(() => {
    setMessage(null);
    setError(null);
    setShowCorrection(false);
    if (!caseId) {
      setResult(null);
      setSegments([]);
      setState("idle");
      return;
    }
    void loadReview(caseId);
  }, [caseId, refreshKey]);

  useEffect(() => {
    setManualField((current) =>
      claimFields.includes(current) ? current : (claimFields[0] ?? ""),
    );
  }, [claimFields]);

  const items = result?.items ?? [];
  const current = items[index] ?? null;
  const view = reviewView(caseId, state, items.length);
  const metadataReady = actor.trim().length > 0 && reason.trim().length > 0;
  const confirmationBasis = useMemo(
    () =>
      current?.citations.some((citation) => citation.relation === "supports")
        ? "evidence"
        : "user",
    [current],
  );

  async function loadReview(targetCaseId: string) {
    setState("loading");
    setError(null);
    try {
      const [loaded, loadedSegments] = await Promise.all([
        getEvidenceReview(targetCaseId),
        getSourceSegments(targetCaseId),
      ]);
      setResult(loaded);
      setSegments(loadedSegments);
      setManualSegmentId((current) =>
        loadedSegments.some((item) => item.id === current)
          ? current
          : (loadedSegments[0]?.id ?? ""),
      );
      setManualField((current) =>
        claimFields.includes(current) ? current : (claimFields[0] ?? ""),
      );
      setIndex(0);
      setCorrectionJson(
        loaded.items[0] ? claimValueJson(loaded.items[0].claim.value) : "",
      );
      setState("ready");
    } catch (reasonCaught) {
      setResult(null);
      setState(
        reasonCaught instanceof LifeCaseApiError && reasonCaught.code === "network_error"
          ? "offline"
          : "error",
      );
    }
  }

  async function createCandidate() {
    if (!caseId || busy) return;
    const segment = segments.find((item) => item.id === manualSegmentId);
    const quote = manualQuote.trim();
    const quoteStart = segment?.text.indexOf(quote) ?? -1;
    if (
      !segment ||
      !manualField ||
      !manualValue.trim() ||
      !quote ||
      quoteStart < 0 ||
      !metadataReady
    ) {
      setError("请选择模板字段和材料分段，并填写分段中完全一致的原文、事实值、操作人和理由。");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await createManualClaim(caseId, {
        field: manualField,
        value: manualValue.trim(),
        segment_id: segment.id,
        quote_start: quoteStart,
        quote_end: quoteStart + quote.length,
        actor: actor.trim(),
        reason: reason.trim(),
      });
      setManualQuote("");
      setManualValue("");
      setReason("");
      setMessage("候选事实已按所选原文登记，请在下方复核后确认。");
      await loadReview(caseId);
      await onReviewChanged();
    } catch (reasonCaught) {
      setError(
        reasonCaught instanceof LifeCaseApiError
          ? reviewErrorGuidance(reasonCaught.code)
          : "候选事实没有保存，请重新读取材料后再试。",
      );
    } finally {
      setBusy(false);
    }
  }

  function selectItem(nextIndex: number) {
    const item = items[nextIndex];
    if (!item) return;
    setIndex(nextIndex);
    setCorrectionJson(claimValueJson(item.claim.value));
    setShowCorrection(false);
    setMessage(null);
    setError(null);
  }

  async function decide(decision: Decision) {
    if (!caseId || !current || !metadataReady || busy) {
      setError("请填写复核人标识和本次判断理由。");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    const metadata = {
      expected_version: current.claim.version,
      actor: actor.trim(),
      reason: reason.trim(),
    };
    try {
      if (decision === "confirm") {
        await confirmReviewClaim(current.claim.id, {
          ...metadata,
          basis: confirmationBasis,
        });
      } else if (decision === "correct") {
        await correctReviewClaim(current.claim.id, {
          ...metadata,
          value: parseCorrectionValue(correctionJson),
        });
      } else {
        await rejectReviewClaim(current.claim.id, metadata);
      }
      setMessage(
        decision === "confirm"
          ? "事实已确认并写入审计记录。"
          : decision === "correct"
            ? "修正值已保存，原值仍保留在历史中。"
            : "候选事实已拒绝并写入审计记录。",
      );
      setReason("");
      setShowCorrection(false);
      await loadReview(caseId);
      await onReviewChanged();
    } catch (reasonCaught) {
      if (reasonCaught instanceof SyntaxError) {
        setError("修正值必须是有效 JSON，例如字符串需要写成 \"修正内容\"。");
      } else if (reasonCaught instanceof LifeCaseApiError) {
        setError(reviewErrorGuidance(reasonCaught.code));
      } else {
        setError("本次复核没有保存，请重新读取后再试。");
      }
    } finally {
      setBusy(false);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    const shortcut = reviewShortcut(event.key, event.altKey);
    if (!shortcut || view !== "populated") return;
    event.preventDefault();
    if (shortcut === "next") {
      selectItem(nextReviewIndex(index, items.length));
    } else if (shortcut === "correct") {
      if (showCorrection) void decide("correct");
      else setShowCorrection(true);
    } else {
      void decide(shortcut);
    }
  }

  return (
    <section
      className="evidence-review"
      aria-labelledby="review-title"
      onKeyDown={handleKeyDown}
    >
      <div className="review-head">
        <div>
          <p className="section-label">EVIDENCE REVIEW</p>
          <h2 id="review-title">逐项核对事实与原始分段</h2>
          {caseTitle && <p>{caseTitle}</p>}
        </div>
        <div className="review-count" aria-label="待复核数量">
          <strong>{String(items.length).padStart(2, "0")}</strong>
          <span>项待复核</span>
        </div>
      </div>

      {caseId && (
        <LazyWorkflowTool title="登记候选事实" hint="需要时从原文分段登记，不调用模型">
        <div className="manual-claim-composer">
          <div className="manual-claim-heading">
            <div><small>DETERMINISTIC INTAKE</small><h3>从原文登记候选事实</h3></div>
            <span>不调用模型</span>
          </div>
          {segments.length === 0 ? (
            <p>当前没有可读文本分段。先导入 TXT、EML 或文本型 PDF；OCR 不可用时不会阻塞其他确定性功能。</p>
          ) : (
            <>
              <div className="manual-claim-grid">
                <label>
                  模板字段
                  <select value={manualField} onChange={(event) => setManualField(event.target.value)}>
                    {claimFields.map((field) => <option key={field} value={field}>{fieldLabel(field)}</option>)}
                  </select>
                </label>
                <label>
                  材料分段
                  <select value={manualSegmentId} onChange={(event) => setManualSegmentId(event.target.value)}>
                    {segments.map((segment) => (
                      <option key={segment.id} value={segment.id}>{segment.source_name} · 段 {segment.sequence + 1}</option>
                    ))}
                  </select>
                </label>
                <label>
                  原文引用（须与下方分段完全一致）
                  <input value={manualQuote} onChange={(event) => setManualQuote(event.target.value)} maxLength={1000} />
                </label>
                <label>
                  候选事实值
                  <input value={manualValue} onChange={(event) => setManualValue(event.target.value)} maxLength={1000} />
                </label>
                <label>
                  操作人标识
                  <input value={actor} onChange={(event) => setActor(event.target.value)} maxLength={120} placeholder="例如：local_user" />
                </label>
                <label>
                  登记理由
                  <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={1000} placeholder="说明为何选择这段原文" />
                </label>
              </div>
              <pre className="manual-segment-preview">
                {segments.find((item) => item.id === manualSegmentId)?.text}
              </pre>
              <button type="button" onClick={() => void createCandidate()} disabled={busy || claimFields.length === 0}>
                登记为待复核事实
              </button>
            </>
          )}
        </div>
        </LazyWorkflowTool>
      )}

      {view !== "populated" && message && (
        <div className="review-global-feedback" aria-live="polite">{message}</div>
      )}

      {view === "unselected" && (
        <ReviewState mark="—" title="尚未选择案件">
          从上方 Today 台账打开案件；复核区不会跨案件加载事实。
        </ReviewState>
      )}
      {view === "loading" && (
        <ReviewState mark="···" title="正在重新校验证据" role="status">
          正在连接当前 ClaimSource 与权威 SourceSegment。
        </ReviewState>
      )}
      {view === "empty" && (
        <ReviewState mark="00" title="没有待复核事实">
          当前案件没有 candidate 或 inferred Claim；已确认内容仍保留在 SQLite。
        </ReviewState>
      )}
      {view === "offline" && (
        <ReviewState mark="OFF" title="本地 API 未连接" role="alert">
          启动本地 API 后再试；任何复核决定都没有写入。
          <button type="button" onClick={() => caseId && void loadReview(caseId)}>
            重新连接
          </button>
        </ReviewState>
      )}
      {view === "failure" && (
        <ReviewState mark="ERR" title="权威证据无法安全读取" role="alert">
          引用可能已变化，或本地存储暂时不可用。系统已停止展示，未静默使用旧副本。
          <button type="button" onClick={() => caseId && void loadReview(caseId)}>
            重新校验
          </button>
        </ReviewState>
      )}
      {view === "populated" && current && (
        <ReviewWorkspace
          current={current}
          index={index}
          total={items.length}
          actor={actor}
          reason={reason}
          correctionJson={correctionJson}
          confirmationBasis={confirmationBasis}
          showCorrection={showCorrection}
          busy={busy}
          metadataReady={metadataReady}
          message={message}
          error={error}
          onActor={setActor}
          onReason={setReason}
          onCorrection={setCorrectionJson}
          onShowCorrection={setShowCorrection}
          onPrevious={() => selectItem(Math.max(0, index - 1))}
          onNext={() => selectItem(nextReviewIndex(index, items.length))}
          onDecision={(decision) => void decide(decision)}
        />
      )}
    </section>
  );
}

interface ReviewStateProps {
  mark: string;
  title: string;
  role?: "status" | "alert";
  children: ReactNode;
}

function ReviewState({ mark, title, role, children }: ReviewStateProps) {
  return (
    <div className="review-state" role={role}>
      <span>{mark}</span>
      <div><h3>{title}</h3><div className="review-state-body">{children}</div></div>
    </div>
  );
}

interface ReviewWorkspaceProps {
  current: EvidenceReviewItem;
  index: number;
  total: number;
  actor: string;
  reason: string;
  correctionJson: string;
  confirmationBasis: "evidence" | "user";
  showCorrection: boolean;
  busy: boolean;
  metadataReady: boolean;
  message: string | null;
  error: string | null;
  onActor: (value: string) => void;
  onReason: (value: string) => void;
  onCorrection: (value: string) => void;
  onShowCorrection: (value: boolean) => void;
  onPrevious: () => void;
  onNext: () => void;
  onDecision: (decision: Decision) => void;
}

function ReviewWorkspace(props: ReviewWorkspaceProps) {
  const { current } = props;
  return (
    <div className="review-workspace">
      <div className="review-rail">
        <div>
          <span>FIELD {String(props.index + 1).padStart(2, "0")}/{String(props.total).padStart(2, "0")}</span>
          <strong>{fieldLabel(current.claim.field)}</strong>
        </div>
        <div className="review-nav">
          <button type="button" onClick={props.onPrevious} disabled={props.index === 0}>上一项</button>
          <button type="button" onClick={props.onNext} disabled={props.index >= props.total - 1}>下一项</button>
        </div>
      </div>

      {current.claim.contradiction && (
        <div className="contradiction-banner" role="alert">
          同一字段存在不一致的 active 候选；请逐项判断，不能自动合并。
        </div>
      )}

      <div className="review-lightbox">
        <div className="evidence-pane">
          <PaneHeading label="01 / AUTHORITY" title="来源与提取分段" />
          {current.citations.length === 0 ? (
            <div className="citation-empty">
              <strong>没有权威引用</strong>
              <p>此候选不能使用 evidence basis；若确认，将明确记录为用户确认。</p>
            </div>
          ) : (
            <div className="citation-stack">
              {current.citations.map((citation) => (
                <CitationBlock key={`${citation.segment_id}:${citation.quote_start}:${citation.relation}`} citation={citation} />
              ))}
            </div>
          )}
        </div>

        <div className="candidate-pane">
          <PaneHeading label="02 / PROPOSAL" title="候选值" />
          <span className={`claim-origin origin-${current.claim.status}`}>
            {current.claim.status === "inferred" ? "推断" : "直接提取候选"}
          </span>
          <pre className="claim-value">{claimValueLabel(current.claim.value)}</pre>
          <div className="claim-facts">
            <span>{confidenceLabel(current.claim.confidence)}</span>
            <span>来源：{current.claim.created_by}</span>
            <span>版本：{current.claim.version}</span>
          </div>
        </div>

        <div className="settled-pane">
          <PaneHeading label="03 / SETTLED" title="当前已确认值" />
          {current.settled_claims.length === 0 ? (
            <p className="settled-empty">该字段尚无 confirmed/corrected 值。</p>
          ) : (
            <ol className="settled-list">
              {current.settled_claims.map((claim) => (
                <li key={claim.id}>
                  <span>{claim.status === "corrected" ? "已修正" : "已确认"}</span>
                  <pre>{claimValueLabel(claim.value)}</pre>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      <div className="decision-console">
        <div className="decision-meta">
          <label>
            复核人标识
            <input value={props.actor} maxLength={120} onChange={(event) => props.onActor(event.target.value)} placeholder="例如：local_user" />
          </label>
          <label>
            判断理由
            <input value={props.reason} maxLength={1000} onChange={(event) => props.onReason(event.target.value)} placeholder="说明核对依据；将进入审计历史" />
          </label>
        </div>

        {props.showCorrection && (
          <label className="correction-editor">
            修正值（JSON）
            <textarea rows={4} value={props.correctionJson} onChange={(event) => props.onCorrection(event.target.value)} />
          </label>
        )}

        <div className="decision-actions">
          <button type="button" className="confirm-decision" disabled={!props.metadataReady || props.busy} onClick={() => props.onDecision("confirm")}>
            {props.confirmationBasis === "evidence" ? "依据证据确认" : "明确用户确认"}
            <small>Alt+1</small>
          </button>
          {props.showCorrection ? (
            <button type="button" className="correct-decision" disabled={!props.metadataReady || props.busy} onClick={() => props.onDecision("correct")}>
              保存修正 <small>Alt+2</small>
            </button>
          ) : (
            <button type="button" className="correct-decision" onClick={() => props.onShowCorrection(true)}>
              修正候选 <small>Alt+2</small>
            </button>
          )}
          <button type="button" className="reject-decision" disabled={!props.metadataReady || props.busy} onClick={() => props.onDecision("reject")}>
            拒绝候选 <small>Alt+3</small>
          </button>
          <span className="next-shortcut">Alt+↓ 下一项</span>
        </div>
        <div className="review-feedback" aria-live="polite">
          {props.message && <p className="success-message">{props.message}</p>}
          {props.error && <p className="error-message">{props.error}</p>}
        </div>
      </div>
    </div>
  );
}

function PaneHeading({ label, title }: { label: string; title: string }) {
  return <div className="pane-heading"><span>{label}</span><h3>{title}</h3></div>;
}

function CitationBlock({ citation }: { citation: ReviewCitation }) {
  return (
    <article className="citation-block">
      <div className="citation-meta">
        <strong>{citation.source_name}</strong>
        <span>段 {citation.sequence + 1}{citation.page ? ` · 第 ${citation.page} 页` : ""}</span>
        <span className={`relation-${citation.relation}`}>{citation.relation}</span>
      </div>
      <p className="segment-text">
        {citation.text.slice(0, citation.quote_start)}
        <mark>{citation.text.slice(citation.quote_start, citation.quote_end)}</mark>
        {citation.text.slice(citation.quote_end)}
      </p>
      <code title={citation.text_sha256}>SHA {citation.text_sha256.slice(0, 12)}…</code>
    </article>
  );
}

function reviewErrorGuidance(code: string): string {
  const guidance: Record<string, string> = {
    claim_version_conflict: "该事实已被其他操作更新，请重新读取后再判断。",
    claim_source_required: "当前没有有效支持引用，只能使用明确用户确认。",
    claim_source_invalid: "引用已变化，系统拒绝使用旧证据。",
    claim_transition_not_allowed: "当前 Claim 状态不允许这项操作，请刷新复核队列。",
    evidence_review_integrity_failed: "权威证据已变化，请重新导入或修复引用。",
    evidence_review_unavailable: "本地复核服务暂时不可用，请稍后重试。",
    claim_invalid: "复核字段不完整，请检查修正值、复核人和理由。",
    validation_error: "复核字段不完整，请检查修正值、复核人和理由。",
  };
  return guidance[code] ?? "本次复核没有保存，请重新读取后再试。";
}
