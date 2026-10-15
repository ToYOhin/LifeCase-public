import type { SourceSegment } from "../review/types";
import type { AgentCitation } from "./types";

export interface ResolvedCitation {
  sourceName: string;
  sequence: number;
  page: number | null;
  before: string;
  quote: string;
  after: string;
}

// Presentation consistency only: domain authority remains on the server.
export async function resolveCitation(
  caseId: string,
  citation: AgentCitation,
  segments: SourceSegment[],
): Promise<ResolvedCitation | null> {
  if (citation.case_id !== caseId) return null;
  const segment = segments.find((item) =>
    item.id === citation.segment_id && item.source_id === citation.source_id &&
    item.sequence === citation.sequence && item.page === citation.page &&
    item.text_sha256 === citation.text_sha256,
  );
  if (!segment) return null;

  // Python quote offsets count Unicode code points, not JavaScript UTF-16 units.
  const characters = Array.from(segment.text);
  const { quote_start: start, quote_end: end } = citation;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) ||
    start < 0 || end <= start || end > characters.length) return null;
  const quote = characters.slice(start, end).join("");
  if (await sha256(segment.text) !== citation.text_sha256 ||
    await sha256(quote) !== citation.quote_hash) return null;

  return {
    sourceName: segment.source_name,
    sequence: segment.sequence,
    page: segment.page,
    before: characters.slice(0, start).join(""),
    quote,
    after: characters.slice(end).join(""),
  };
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
