import { describe, expect, it } from "vitest";

import type { SourceSegment } from "../review/types";
import type { AgentCitation } from "./types";
import { resolveCitation } from "./citationSources";

const prefix = "购买凭证🧾\n";
const quote = "海星 H8 保修 24 个月。";
const suffix = "\n注意：保留票据。";

async function digest(text: string) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function fixture() {
  const text = prefix + quote + suffix;
  const segment: SourceSegment = {
    id: "segment_warranty", source_id: "source_receipt", source_name: "海星H8购买凭证.txt",
    sequence: 2, page: null, text, text_sha256: await digest(text),
    extraction_method: "text", extractor_version: "text.v1", confidence: null,
  };
  const citation: AgentCitation = {
    case_id: "case_warranty", source_id: segment.source_id, segment_id: segment.id,
    sequence: segment.sequence, page: segment.page, text_sha256: segment.text_sha256,
    quote_start: Array.from(prefix).length, quote_end: Array.from(prefix + quote).length,
    quote_hash: await digest(quote),
  };
  return { segment, citation };
}

describe("Case-scoped citation source presentation", () => {
  it("resolves the actual source and highlights Python code-point offsets after an emoji", async () => {
    const { segment, citation } = await fixture();
    expect(await resolveCitation("case_warranty", citation, [segment])).toEqual({
      sourceName: segment.source_name, sequence: 2, page: null, before: prefix, quote, after: suffix,
    });
  });

  it.each([
    ["other Case", { case_id: "case_other" }],
    ["other Source", { source_id: "source_other" }],
    ["other Segment", { segment_id: "segment_other" }],
    ["stale text hash", { text_sha256: "0".repeat(64) }],
    ["wrong quote hash", { quote_hash: "0".repeat(64) }],
    ["wrong sequence", { sequence: 7 }],
    ["wrong page", { page: 3 }],
    ["negative offset", { quote_start: -1 }],
    ["fractional offset", { quote_start: 1.5 }],
    ["empty quote", { quote_end: Array.from(prefix).length }],
    ["out of range", { quote_end: 10000 }],
  ])("does not display an unverified excerpt for %s", async (_name, mismatch) => {
    const { segment, citation } = await fixture();
    expect(await resolveCitation("case_warranty", { ...citation, ...mismatch }, [segment])).toBeNull();
  });

  it("does not substitute another segment when the source is missing", async () => {
    const { citation } = await fixture();
    expect(await resolveCitation("case_warranty", citation, [])).toBeNull();
  });

  it("does not trust a matching hash label when returned text differs", async () => {
    const { segment, citation } = await fixture();
    expect(await resolveCitation("case_warranty", citation, [{ ...segment, text: "其他材料" }])).toBeNull();
  });

  it("preserves text as text rather than interpreting imported HTML", async () => {
    const { segment, citation } = await fixture();
    const text = "<script>alert('synthetic')</script>";
    const hash = await digest(text);
    const resolved = await resolveCitation("case_warranty", {
      ...citation, text_sha256: hash, quote_start: 0, quote_end: Array.from(text).length, quote_hash: hash,
    }, [{ ...segment, text, text_sha256: hash }]);
    expect(resolved?.quote).toBe(text);
  });
});
