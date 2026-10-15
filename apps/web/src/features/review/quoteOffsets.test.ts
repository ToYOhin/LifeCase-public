import { describe, expect, it } from "vitest";
import { sourceQuoteRange, splitSourceQuote } from "./quoteOffsets";

describe("source Unicode code-point offsets", () => {
  it("registers Chinese and English quotes after an emoji using Python offsets", () => {
    expect(sourceQuoteRange("票🧾海星 H8 Warranty", "海星 H8")).toEqual({ start: 2, end: 7 });
    expect(sourceQuoteRange("票🧾海星 H8 Warranty", "Warranty")).toEqual({ start: 8, end: 16 });
  });
  it("renders exactly the quote for server offsets", () => {
    expect(splitSourceQuote("票🧾海星 H8 Warranty", 2, 7)).toEqual({ before: "票🧾", quote: "海星 H8", after: " Warranty" });
  });
  it("does not invent a match for absent or empty input", () => {
    expect(sourceQuoteRange("材料", "缺失")).toBeNull();
    expect(sourceQuoteRange("材料", "")).toBeNull();
  });
});
