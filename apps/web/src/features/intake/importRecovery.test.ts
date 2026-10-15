import { describe, expect, it } from "vitest";
import { importRequiresRead, LifeCaseApiError, readImportCase, uploadSource } from "./api";
import { extractionGuidance } from "./presenters";
import type { CaseRecord, SourceRecord } from "./types";

const source: SourceRecord = { id: "source_a", case_id: "case_a", original_name: "synthetic.txt", media_type: "text/plain",
  sha256: "a".repeat(64), size_bytes: 10, trust_label: "untrusted", imported_at: "2026-09-24T00:00:00Z",
  extraction: { status: "failed", error_code: "TEXT_ENCODING_INVALID", extractor_version: "text.v1", segment_count: 0, notices: [] } };
const record: CaseRecord = { id: "case_a", title: "合成导入", goal: "检查重试", type: "warranty", template_id: "warranty_claim", template_version: 1,
  status: "intake", priority: 0, version: 1, created_at: source.imported_at, updated_at: source.imported_at, closed_at: null, sources: [source] };

describe("material import recovery", () => {
  it("does not confuse retryability with whether a Source may already be saved", () => {
    for (const code of ["extraction_failed", "source_integrity_failed"]) {
      expect(importRequiresRead(new LifeCaseApiError(code, "synthetic", false))).toBe(true);
    }
    expect(importRequiresRead(new LifeCaseApiError("media_type_unsupported", "synthetic", false))).toBe(false);
  });
  it("explains saved extraction failures and the required manual correction instead of suggesting blind retries", () => {
    expect(extractionGuidance(source.extraction)).toContain("UTF-8");
    expect(extractionGuidance({ ...source.extraction, error_code: "PDF_ENCRYPTED" })).toContain("未加密");
    expect(extractionGuidance({ ...source.extraction, status: "ocr_required", error_code: "OCR_REQUIRED" })).toContain("文本");
    expect(extractionGuidance({ ...source.extraction, error_code: "EXTRACTION_TIMEOUT" })).toContain("超时");
    expect(extractionGuidance({ ...source.extraction, error_code: "unrecognized" })).toContain("未能提取");
    expect(extractionGuidance({ ...source.extraction, status: "completed", error_code: null })).toBeNull();
  });

  it("rechecks the uploaded source through a fresh same-Case read without repeating POST", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const request: typeof fetch = async (url, init) => { calls.push([String(url), init]); return new Response(JSON.stringify(record)); };
    expect(await readImportCase(record.id, source, request)).toEqual(record);
    expect(calls).toEqual([["/api/v1/cases/case_a", { cache: "no-store" }]]);
  });

  it("rejects wrong Case, missing saved source and changed source identity without accepting another material", async () => {
    for (const response of [{ ...record, id: "case_b" }, { ...record, sources: [] },
      { ...record, sources: [{ ...source, case_id: "case_b" }] },
      { ...record, sources: [{ ...source, sha256: "b".repeat(64) }] },
    ]) await expect(readImportCase(record.id, source, async () => new Response(JSON.stringify(response)))).rejects.toThrow();
    await expect(readImportCase(record.id, null, async () => new Response("{}", { status: 503 }))).rejects.toThrow();
    expect(await readImportCase(record.id, null, async () => new Response(JSON.stringify(record)))).toEqual(record);
  });

  it("does not accept a successful upload response from another Case or with unrecognizable extraction state", async () => {
    for (const response of [{ ...source, case_id: "case_b" }, { ...source, extraction: null }, null]) {
      const listeners = new Map<string, EventListener>();
      const xhr = { status: 201, response, open: () => undefined, responseType: "", upload: { addEventListener: () => undefined },
        addEventListener: (type: string, listener: EventListener) => listeners.set(type, listener),
        send: () => listeners.get("load")?.({} as Event) } as unknown as XMLHttpRequest;
      await expect(uploadSource(record.id, new File(["synthetic"], "synthetic.txt"), () => undefined, () => xhr))
        .rejects.toMatchObject({ code: "unexpected_response", retryable: true });
    }
  });
});
