import { useEffect, useState } from "react";

import { getSourceSegments } from "../review/api";
import { resolveCitation, type ResolvedCitation } from "./citationSources";
import type { AgentCitation } from "./types";

interface AnswerCitationsProps {
  caseId: string;
  citations: AgentCitation[];
}

interface SourceState {
  caseId: string;
  citations: AgentCitation[];
  status: "ready" | "error";
  sources: (ResolvedCitation | null)[];
}

export function AnswerCitations({ caseId, citations }: AnswerCitationsProps) {
  const [state, setState] = useState<SourceState | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState(null);
    if (citations.length === 0) return;
    const request: typeof fetch = (input, init) => fetch(input, { ...init, signal: controller.signal });
    void (async () => {
      try {
        // One Case-scoped read for the whole answer, never a request per citation.
        const segments = citations.some((item) => item.case_id === caseId)
          ? await getSourceSegments(caseId, request) : [];
        const sources = await Promise.all(citations.map((item) => resolveCitation(caseId, item, segments)));
        if (!controller.signal.aborted) setState({ caseId, citations, status: "ready", sources });
      } catch {
        if (!controller.signal.aborted) setState({ caseId, citations, status: "error", sources: [] });
      }
    })();
    return () => controller.abort();
  }, [caseId, citations, attempt]);

  // Never render a previous Case/answer while the new effect is starting.
  const current = state?.caseId === caseId && state.citations === citations ? state : null;
  return (
    <div className="evidence-rail" aria-label="回答引用">
      <div className="evidence-rail-heading">
        <span aria-hidden="true" />
        <strong>{citations.length} 条引用</strong>
      </div>
      {citations.length === 0 ? (
        <p className="evidence-empty">未命中可支持当前回答的证据。</p>
      ) : (
        <>
          {!current && <p className="evidence-empty" role="status">正在读取引用来源…</p>}
          {current?.status === "error" && (
            <div className="citation-load-error" role="alert">
              <p>暂时无法读取引用原文，请检查本地连接后重试。分析记录未改变。</p>
              <button type="button" className="secondary-action" onClick={() => { setState(null); setAttempt((value) => value + 1); }}>
                重新读取引用
              </button>
            </div>
          )}
          <ol>
            {citations.map((citation, index) => (
              <CitationCard
                key={`${citation.case_id}:${citation.source_id}:${citation.segment_id}:${citation.quote_start}:${citation.quote_end}:${index}`}
                index={index}
                source={current?.sources[index] ?? null}
                unavailable={current?.status === "ready"}
              />
            ))}
          </ol>
        </>
      )}
    </div>
  );
}

export function CitationCard({ index, source, unavailable }: {
  index: number;
  source: ResolvedCitation | null;
  unavailable: boolean;
}) {
  return (
    <li className="answer-citation">
      <span className="evidence-index">{String(index + 1).padStart(2, "0")}</span>
      <div>
        <strong>证据 {index + 1}{source && ` · ${source.sourceName}`}</strong>
        {source ? (
          <>
            <small>{source.page !== null && `第 ${source.page} 页 · `}第 {source.sequence + 1} 段</small>
            <details className="citation-original" onToggle={(event) => {
              if (event.currentTarget.open) {
                event.currentTarget.querySelector("mark")?.scrollIntoView({ block: "nearest" });
              }
            }}>
              <summary>查看引用原文</summary>
              <p className="citation-original-hint">材料提取原文 · 高亮部分为本条引用</p>
              <blockquote>{source.before}<mark>{source.quote}</mark>{source.after}</blockquote>
            </details>
          </>
        ) : (
          <small>{unavailable ? "此引用原文当前无法定位，材料可能已变化；未显示未经核对的内容。" : "来源尚未读取"}</small>
        )}
      </div>
    </li>
  );
}
