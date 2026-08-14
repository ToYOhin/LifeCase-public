import { describe, expect, it } from "vitest";

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
  nextReviewIndex,
  parseCorrectionValue,
  reviewShortcut,
  reviewView,
} from "./presenters";
import type { EvidenceReviewResult, ReviewClaim } from "./types";

const claim: ReviewClaim = {
  id: "claim_synthetic",
  case_id: "case_synthetic",
  field: "product_name",
  value: "Synthetic headphones",
  status: "candidate",
  confidence: 0.8,
  created_by: "agent",
  contradiction: false,
  version: 1,
  created_at: "2026-08-02T00:00:00.000000Z",
  updated_at: "2026-08-02T00:00:00.000000Z",
};

const review: EvidenceReviewResult = {
  case_id: claim.case_id,
  next_claim_id: claim.id,
  items: [{ claim, citations: [], settled_claims: [], is_next: true }],
};

describe("Evidence Review API", () => {
  it("creates a candidate from an exact Case-scoped source quote", async () => {
    const calls: Array<{ url: string; body?: unknown }> = [];
    const request = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      const isSegments = String(input).endsWith("source-segments");
      return new Response(JSON.stringify(isSegments ? [{
        id: "segment_one",
        source_id: "source_one",
        source_name: "receipt.txt",
        sequence: 0,
        page: null,
        text: "Store A",
        extraction_method: "text",
        extractor_version: "text.v1",
        confidence: null,
        text_sha256: "0".repeat(64),
      }] : claim), {
        status: String(input).endsWith("claims") ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    };

    expect(await getSourceSegments("case synthetic", request)).toHaveLength(1);
    await createManualClaim("case synthetic", {
      field: "merchant_or_manufacturer",
      value: "Store A",
      segment_id: "segment_one",
      quote_start: 0,
      quote_end: 7,
      actor: "local_user",
      reason: "Selected exact receipt text",
    }, request);

    expect(calls.map((item) => item.url)).toEqual([
      "/api/v1/cases/case%20synthetic/source-segments",
      "/api/v1/cases/case%20synthetic/claims",
    ]);
    expect(calls[1].body).toMatchObject({ segment_id: "segment_one", quote_end: 7 });
  });

  it("loads the Case queue and calls all existing Claim decision routes", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const request = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      return new Response(JSON.stringify(init?.method === "POST" ? claim : review), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };

    await getEvidenceReview(claim.case_id, request);
    await confirmReviewClaim(
      claim.id,
      { expected_version: 1, basis: "user", actor: "reviewer", reason: "Reviewed" },
      request,
    );
    await correctReviewClaim(
      claim.id,
      { expected_version: 1, value: "Corrected", actor: "reviewer", reason: "Corrected" },
      request,
    );
    await rejectReviewClaim(
      claim.id,
      { expected_version: 1, actor: "reviewer", reason: "Rejected" },
      request,
    );

    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/cases/case_synthetic/evidence-review",
      "/api/v1/claims/claim_synthetic/confirm",
      "/api/v1/claims/claim_synthetic/correct",
      "/api/v1/claims/claim_synthetic/reject",
    ]);
    expect(calls[1].body).toMatchObject({ basis: "user", actor: "reviewer" });
  });

  it("maps network failures to a safe offline error", async () => {
    await expect(
      getEvidenceReview(claim.case_id, async () => {
        throw new TypeError("private network detail");
      }),
    ).rejects.toMatchObject({ code: "network_error", retryable: true });
  });
});

describe("Evidence Review presentation", () => {
  it("maps all view states and bounded next navigation", () => {
    expect(reviewView(null, "idle", 0)).toBe("unselected");
    expect(reviewView("case", "loading", 0)).toBe("loading");
    expect(reviewView("case", "ready", 0)).toBe("empty");
    expect(reviewView("case", "offline", 0)).toBe("offline");
    expect(reviewView("case", "error", 0)).toBe("failure");
    expect(reviewView("case", "ready", 1)).toBe("populated");
    expect(nextReviewIndex(0, 2)).toBe(1);
    expect(nextReviewIndex(1, 2)).toBe(1);
  });

  it("uses explicit modifier shortcuts and parses correction JSON", () => {
    expect(reviewShortcut("1", true)).toBe("confirm");
    expect(reviewShortcut("2", true)).toBe("correct");
    expect(reviewShortcut("3", true)).toBe("reject");
    expect(reviewShortcut("ArrowDown", true)).toBe("next");
    expect(reviewShortcut("1", false)).toBeNull();
    expect(parseCorrectionValue(claimValueJson({ amount_minor: 1200 }))).toEqual({
      amount_minor: 1200,
    });
    expect(() => parseCorrectionValue("not-json")).toThrow();
  });
});
