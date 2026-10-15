import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
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
  claimValueLabel,
  confidenceLabel,
  fieldLabel,
  nextReviewIndex,
  reviewShortcut,
  reviewView,
} from "./presenters";
import {
  correctionInputType,
  correctionInputValue,
  parseFieldCorrection,
} from "./correctionInput";
import type {
  EvidenceReviewItem,
  EvidenceReviewResult,
  JsonValue,
  ReviewCitation,
  ReviewLoadState,
  SourceSegment,
} from "./types";
import { LazyWorkflowTool } from "../workbench/LazyWorkflowTool";
import { sourceQuoteRange, splitSourceQuote } from "./quoteOffsets";

interface EvidenceReviewPanelProps {
  caseId: string | null;
  caseTitle: string | null;
  claimFields: string[];
  refreshKey: number;
  onReviewChanged: () => Promise<void>;
  target?: { field: string; mode: "register" | "review" };
  onImportMaterials?: () => void;
}

type Decision = "confirm" | "correct" | "reject";

export function EvidenceReviewPanel({
  caseId,
  caseTitle,
  claimFields,
  refreshKey,
  onReviewChanged,
  target,
  onImportMaterials,
}: EvidenceReviewPanelProps) {
  const [result, setResult] = useState<EvidenceReviewResult | null>(null);
  const [state, setState] = useState<ReviewLoadState>("idle");
  const [index, setIndex] = useState(0);
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  const [correctionInput, setCorrectionInput] = useState("");
  const [showCorrection, setShowCorrection] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [segments, setSegments] = useState<SourceSegment[]>([]);
  const [manualField, setManualField] = useState(target?.mode === "register" ? target.field : "");
  const [manualSegmentId, setManualSegmentId] = useState("");
  const [manualQuote, setManualQuote] = useState("");
  const [manualValue, setManualValue] = useState("");
  const contextGeneration = useRef(0);
  const readSequence = useRef(0);
  const busyRef = useRef(false);
  const panel = useRef<HTMLElement | null>(null);
  const focusedTarget = useRef<string | null>(null);
  const selectedClaim = useRef<string | null>(null);

  useEffect(() => {
    ++contextGeneration.current;
    busyRef.current = false;
    setBusy(false);
    setMessage(null);
    setError(null);
    setShowCorrection(false);
    setResult(null);
    setSegments([]);
    setReason("");
    setManualQuote("");
    setManualValue("");
    setManualField(target?.mode === "register" ? target.field : "");
    setManualSegmentId("");
    focusedTarget.current = null;
    selectedClaim.current = null;
    if (!caseId) {
      setState("idle");
    }
    return () => {
      ++contextGeneration.current;
      ++readSequence.current;
    };
  }, [caseId]);

  // A parent refresh may finish after the next editor has appeared. It must not
  // clear same-Case input or release a write still waiting for its parent.
  useEffect(() => {
    if (caseId) void loadReview(caseId, contextGeneration.current);
    return () => { ++readSequence.current; };
  }, [caseId, refreshKey]);

  useEffect(() => {
    setManualField((current) =>
      claimFields.includes(current) ? current : target?.mode === "register" && claimFields.includes(target.field) ? target.field : (claimFields[0] ?? ""),
    );
  }, [claimFields]);

  const items = result?.case_id === caseId ? result?.items ?? [] : [];
  const currentSegments = result?.case_id === caseId ? segments : [];
  const current = items[index] ?? null;
  const view = reviewView(caseId, state, items.length);
  const manualLocked = busy || state !== "ready";
  const metadataReady = actor.trim().length > 0 && reason.trim().length > 0;
  const confirmationBasis = useMemo(
    () =>
      current?.citations.some((citation) => citation.relation === "supports")
        ? "evidence"
        : "user",
    [current],
  );

  useEffect(() => {
    if (!target || state !== "ready" || busy) return;
    const key = `${caseId}:${target.mode}:${target.field}`;
    if (focusedTarget.current === key) return;
    const destination = panel.current?.querySelector<HTMLElement>(target.mode === "register"
      ? ".manual-claim-composer select" : ".review-rail");
    if (!destination) return;
    focusedTarget.current = key;
    destination.focus();
    destination.scrollIntoView({ block: "center" });
  }, [caseId, target, state, busy]);

  async function loadReview(targetCaseId: string, generation = contextGeneration.current) {
    if (generation !== contextGeneration.current) return;
    const request = ++readSequence.current;
    const isCurrent = () => generation === contextGeneration.current && request === readSequence.current;
    setState("loading");
    setError(null);
    try {
      const [loaded, loadedSegments] = await Promise.all([
        getEvidenceReview(targetCaseId),
        getSourceSegments(targetCaseId),
      ]);
      if (!isCurrent()) return;
      setResult(loaded);
      setSegments(loadedSegments);
      setManualSegmentId((current) =>
        loadedSegments.some((item) => item.id === current)
          ? current
          : (loadedSegments[0]?.id ?? ""),
      );
      // The catalog effect owns field selection; this request may have started
      // before the catalog arrived and must not restore that stale empty list.
      const retainedIndex = loaded.items.findIndex(item => item.claim.id === selectedClaim.current);
      const requestedIndex = target
        ? loaded.items.findIndex(item => item.claim.field === target.field) : -1;
      const nextIndex = retainedIndex >= 0 ? retainedIndex : Math.max(0, requestedIndex);
      selectedClaim.current = loaded.items[nextIndex]?.claim.id ?? null;
      setIndex(nextIndex);
      setCorrectionInput(
        loaded.items[nextIndex]
          ? correctionInputValue(loaded.items[nextIndex].claim.field, loaded.items[nextIndex].claim.value)
          : "",
      );
      setState("ready");
    } catch (reasonCaught) {
      if (!isCurrent()) return;
      setResult(null);
      setSegments([]);
      setState(
        reasonCaught instanceof LifeCaseApiError && reasonCaught.code === "network_error"
          ? "offline"
          : "error",
      );
    }
  }

  async function createCandidate() {
    if (!caseId || busyRef.current || state !== "ready") return;
    const segment = currentSegments.find((item) => item.id === manualSegmentId);
    const quote = manualQuote.trim();
    const range = segment ? sourceQuoteRange(segment.text, quote) : null;
    if (
      !segment ||
      !manualField ||
      !manualValue.trim() ||
      !quote ||
      !range ||
      !metadataReady
    ) {
      setError("请选择模板字段和材料分段，并填写分段中完全一致的原文、事实值、操作人和理由。");
      return;
    }
    const generation = contextGeneration.current;
    const isCurrent = () => generation === contextGeneration.current;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const created = await createManualClaim(caseId, {
        field: manualField,
        value: manualValue.trim(),
        segment_id: segment.id,
        quote_start: range.start,
        quote_end: range.end,
        actor: actor.trim(),
        reason: reason.trim(),
      });
      if (!isCurrent()) return;
      selectedClaim.current = created.id;
      setManualQuote("");
      setManualValue("");
      setReason("");
      setMessage("候选事实已按所选原文登记，请在下方复核后确认。");
      await loadReview(caseId, generation);
      if (!isCurrent()) return;
      await onReviewChanged();
    } catch (reasonCaught) {
      if (!isCurrent()) return;
      setError(
        reasonCaught instanceof LifeCaseApiError
          ? reviewErrorGuidance(reasonCaught.code)
          : "候选事实没有保存，请重新读取材料后再试。",
      );
    } finally {
      if (isCurrent()) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }

  function selectItem(nextIndex: number) {
    if (busyRef.current) return;
    const item = items[nextIndex];
    if (!item) return;
    selectedClaim.current = item.claim.id;
    setIndex(nextIndex);
    setCorrectionInput(correctionInputValue(item.claim.field, item.claim.value));
    setShowCorrection(false);
    setMessage(null);
    setError(null);
  }

  async function decide(decision: Decision) {
    if (busyRef.current) return;
    if (!caseId || !current || !metadataReady) {
      setError("请填写复核人标识和本次判断理由。");
      return;
    }
    let correction: JsonValue = null;
    if (decision === "correct") {
      try {
        correction = parseFieldCorrection(current.claim.field, current.claim.value, correctionInput);
      } catch (reasonCaught) {
        setError(reasonCaught instanceof Error ? reasonCaught.message : "请检查修正值。");
        return;
      }
    }
    const generation = contextGeneration.current;
    const isCurrent = () => generation === contextGeneration.current;
    busyRef.current = true;
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
          value: correction,
        });
      } else {
        await rejectReviewClaim(current.claim.id, metadata);
      }
      if (!isCurrent()) return;
      setMessage(
        decision === "confirm"
          ? "事实已确认并写入审计记录。"
          : decision === "correct"
            ? "修正值已保存，原值仍保留在历史中。"
            : "候选事实已拒绝并写入审计记录。",
      );
      setReason("");
      setShowCorrection(false);
      await loadReview(caseId, generation);
      if (!isCurrent()) return;
      await onReviewChanged();
    } catch (reasonCaught) {
      if (!isCurrent()) return;
      if (reasonCaught instanceof LifeCaseApiError) {
        setError(reviewErrorGuidance(reasonCaught.code));
      } else {
        setError("本次复核没有保存，请重新读取后再试。");
      }
    } finally {
      if (isCurrent()) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    const shortcut = reviewShortcut(event.key, event.altKey);
    if (!shortcut || view !== "populated" || busyRef.current) return;
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
      ref={panel}
      className="evidence-review"
      aria-busy={busy || state === "loading"}
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
        <LazyWorkflowTool title="登记候选事实" hint="需要时从原文分段登记，不调用模型" open={target?.mode === "register"}>
        <div className="manual-claim-composer">
          <div className="manual-claim-heading">
            <div><small>DETERMINISTIC INTAKE</small><h3>从原文登记候选事实</h3></div>
            <span>不调用模型</span>
          </div>
          {busy && <p role="status">正在完成本次登记或复核，请稍候再填写下一条。</p>}
          {target?.mode === "register" && !claimFields.includes(target.field) && <p role="alert">当前模板目录没有此字段，请刷新案件后核对；不会登记到其他字段。</p>}
          {currentSegments.length === 0 ? (
            <p>{state === "loading" ? "正在读取材料分段……" : state !== "ready" ? "材料尚未安全读取，请先使用下方的重新连接或校验。" : <>当前没有可读文本分段。先导入 TXT、EML 或文本型 PDF。{onImportMaterials && <button type="button" onClick={onImportMaterials}>去导入材料</button>}</>}</p>
          ) : (
            <>
              <div className="manual-claim-grid">
                <label>
                  模板字段
                  <select value={manualField} disabled={manualLocked} onChange={(event) => setManualField(event.target.value)}>
                    {claimFields.map((field) => <option key={field} value={field}>{fieldLabel(field)}</option>)}
                  </select>
                </label>
                <label>
                  材料分段
                  <select value={manualSegmentId} disabled={manualLocked} onChange={(event) => setManualSegmentId(event.target.value)}>
                    {currentSegments.map((segment) => (
                      <option key={segment.id} value={segment.id}>{segment.source_name} · 段 {segment.sequence + 1}</option>
                    ))}
                  </select>
                </label>
                <label>
                  原文引用（须与下方分段完全一致）
                  <input value={manualQuote} disabled={manualLocked} onChange={(event) => setManualQuote(event.target.value)} maxLength={1000} />
                </label>
                <label>
                  候选事实值
                  <input value={manualValue} disabled={manualLocked} onChange={(event) => setManualValue(event.target.value)} maxLength={1000} />
                </label>
                <label>
                  操作人标识
                  <input value={actor} disabled={manualLocked} onChange={(event) => setActor(event.target.value)} maxLength={120} placeholder="例如：local_user" />
                </label>
                <label>
                  登记理由
                  <input value={reason} disabled={manualLocked} onChange={(event) => setReason(event.target.value)} maxLength={1000} placeholder="说明为何选择这段原文" />
                </label>
              </div>
              <pre className="manual-segment-preview">
                {currentSegments.find((item) => item.id === manualSegmentId)?.text}
              </pre>
              <button type="button" onClick={() => void createCandidate()} disabled={manualLocked || claimFields.length === 0 || (target?.mode === "register" && !claimFields.includes(target.field))}>
                登记为待复核事实
              </button>
            </>
          )}
        </div>
        </LazyWorkflowTool>
      )}

      {target?.mode === "review" && state === "ready" && !items.some(item => item.claim.field === target.field) && <p role="status">当前已无{fieldLabel(target.field)}待复核候选。请查看上方已核实记录；下方仍可处理其他候选。</p>}

      {view !== "populated" && message && (
        <div className="review-global-feedback" aria-live="polite">{message}</div>
      )}
      {view !== "populated" && error && (
        <div className="review-global-feedback" role="alert">{error}</div>
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
          当前案件没有待复核候选；已确认内容仍保留，可在本区上方回看与修正。
        </ReviewState>
      )}
      {view === "offline" && (
        <ReviewState mark="OFF" title="本地 API 未连接" role="alert">
          当前事实暂时无法读取；若保存时连接中断，请重新连接后核对保存结果。
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
          correctionInput={correctionInput}
          confirmationBasis={confirmationBasis}
          showCorrection={showCorrection}
          busy={busy}
          metadataReady={metadataReady}
          message={message}
          error={error}
          onActor={setActor}
          onReason={setReason}
          onCorrection={setCorrectionInput}
          onShowCorrection={setShowCorrection}
          onPrevious={() => selectItem(Math.max(0, index - 1))}
          onNext={() => selectItem(nextReviewIndex(index, items.length))}
          onDecision={(decision) => void decide(decision)}
          onReload={() => caseId && void loadReview(caseId)}
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
  correctionInput: string;
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
  onReload: () => void;
}

function ReviewWorkspace(props: ReviewWorkspaceProps) {
  const { current } = props;
  const inputType = correctionInputType(current.claim.field, current.claim.value);
  return (
    <div className="review-workspace">
      <div className="review-rail" tabIndex={-1}>
        <div>
          <span>FIELD {String(props.index + 1).padStart(2, "0")}/{String(props.total).padStart(2, "0")}</span>
          <strong>{fieldLabel(current.claim.field)}</strong>
        </div>
        <div className="review-nav">
          <button type="button" onClick={props.onPrevious} disabled={props.busy || props.index === 0}>上一项</button>
          <button type="button" onClick={props.onNext} disabled={props.busy || props.index >= props.total - 1}>下一项</button>
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
            <input value={props.actor} disabled={props.busy} maxLength={120} onChange={(event) => props.onActor(event.target.value)} placeholder="例如：local_user" />
          </label>
          <label>
            判断理由
            <input value={props.reason} disabled={props.busy} maxLength={1000} onChange={(event) => props.onReason(event.target.value)} placeholder="说明核对依据；将进入审计历史" />
          </label>
        </div>

        {props.showCorrection && (
          <label className="correction-editor">
            {inputType === "json" ? "修正值（JSON）" : inputType === "date" ? "修正日期" : "修正值"}
            {inputType === "json" ? (
              <textarea rows={4} value={props.correctionInput} disabled={props.busy} onChange={(event) => props.onCorrection(event.target.value)} />
            ) : (
              <input type={inputType} step={inputType === "number" ? "1" : undefined} value={props.correctionInput} maxLength={1000} disabled={props.busy} onChange={(event) => props.onCorrection(event.target.value)} />
            )}
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
            <button type="button" className="correct-decision" disabled={props.busy} onClick={() => props.onShowCorrection(true)}>
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
          {props.error && (
            <div className="error-message" role="alert">
              <p>{props.error}</p>
              <button type="button" disabled={props.busy} onClick={props.onReload}>重新读取事实</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PaneHeading({ label, title }: { label: string; title: string }) {
  return <div className="pane-heading"><span>{label}</span><h3>{title}</h3></div>;
}

function CitationBlock({ citation }: { citation: ReviewCitation }) {
  const excerpt = splitSourceQuote(citation.text, citation.quote_start, citation.quote_end);
  return (
    <article className="citation-block">
      <div className="citation-meta">
        <strong>{citation.source_name}</strong>
        <span>段 {citation.sequence + 1}{citation.page ? ` · 第 ${citation.page} 页` : ""}</span>
        <span className={`relation-${citation.relation}`}>{citation.relation}</span>
      </div>
      <p className="segment-text">
        {excerpt.before}
        <mark>{excerpt.quote}</mark>
        {excerpt.after}
      </p>
      <code title={citation.text_sha256}>SHA {citation.text_sha256.slice(0, 12)}…</code>
    </article>
  );
}

function reviewErrorGuidance(code: string): string {
  const guidance: Record<string, string> = {
    network_error: "连接中断，暂无法确认本次保存结果。请重新读取事实后再操作。",
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
