import { describe, expect, it } from "vitest";

import { createCase, getCase, transitionCase, uploadSource } from "./api";
import { errorGuidanceFor, extractionLabel, formatFileSize } from "./presenters";
import type { CaseRecord } from "./types";

const persistedCase: CaseRecord = {
  id: "case_synthetic",
  type: "warranty",
  template_id: "warranty_claim",
  template_version: 1,
  title: "合成保修案件",
  goal: "整理合成材料",
  status: "intake",
  priority: 0,
  version: 1,
  created_at: "2026-08-01T00:00:00.000000Z",
  updated_at: "2026-08-01T00:00:00.000000Z",
  closed_at: null,
  sources: [],
};

describe("intake API client", () => {
  it("creates and restores a persisted case through typed contracts", async () => {
    const calls: string[] = [];
    const request = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${String(input)}`);
      return new Response(JSON.stringify(persistedCase), {
        status: init?.method === "POST" ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    };

    const created = await createCase(
      { title: "合成保修案件", goal: "整理合成材料", template_id: "warranty" },
      request,
    );
    const restored = await getCase(created.id, request);

    expect(restored).toEqual(persistedCase);
    expect(calls).toEqual([
      "POST /api/v1/cases",
      "GET /api/v1/cases/case_synthetic",
    ]);
  });

  it("turns safe API errors into actionable UI guidance", async () => {
    const request = async () =>
      new Response(
        JSON.stringify({
          error: {
            code: "source_too_large",
            message: "Selected file exceeds the configured size limit",
            details: {},
            retryable: false,
            request_id: "req_synthetic",
          },
        }),
        { status: 413, headers: { "Content-Type": "application/json" } },
      );

    await expect(getCase("case_synthetic", request)).rejects.toMatchObject({
      code: "source_too_large",
      retryable: false,
    });
    expect(errorGuidanceFor("source_too_large", "fallback")).toContain("大小限制");
  });

  it("uses the exact Case version for an explicit lifecycle transition", async () => {
    let body: unknown;
    const transition = {
      id: "transition_one",
      case_id: persistedCase.id,
      prior_status: "intake",
      new_status: "ready",
      prior_version: 1,
      new_version: 2,
      actor: "local_user",
      reason: "Ready for local action",
      transitioned_at: persistedCase.updated_at,
    };
    const result = await transitionCase(persistedCase.id, {
      expected_version: 1,
      target_status: "ready",
      actor: "local_user",
      reason: "Ready for local action",
    }, async (_input, init) => {
      body = JSON.parse(String(init?.body));
      return new Response(JSON.stringify(transition), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    expect(body).toMatchObject({ expected_version: 1, target_status: "ready" });
    expect(result.new_version).toBe(2);
  });

  it("reports real multipart upload progress before resolving Source state", async () => {
    const progress: number[] = [];
    const opened: string[] = [];
    const listeners = new Map<string, EventListener>();
    let progressListener: EventListener | undefined;
    const source = {
      id: "src_synthetic",
      case_id: persistedCase.id,
      original_name: "evidence.txt",
      media_type: "text/plain",
      sha256: "a".repeat(64),
      size_bytes: 9,
      trust_label: "untrusted",
      imported_at: persistedCase.created_at,
      extraction: {
        status: "completed",
        extractor_version: "text.v1",
        segment_count: 1,
        notices: [],
        error_code: null,
      },
    };
    const request = {
      status: 201,
      response: source,
      responseType: "",
      upload: {
        addEventListener: (_type: string, listener: EventListener) => {
          progressListener = listener;
        },
      },
      open: (method: string, url: string) => opened.push(`${method} ${url}`),
      addEventListener: (type: string, listener: EventListener) => listeners.set(type, listener),
      send: (body: XMLHttpRequestBodyInit | Document | null) => {
        expect(body).toBeInstanceOf(FormData);
        progressListener?.({
          lengthComputable: true,
          loaded: 5,
          total: 10,
        } as ProgressEvent);
        listeners.get("load")?.({} as Event);
      },
    } as unknown as XMLHttpRequest;

    const result = await uploadSource(
      persistedCase.id,
      new File(["synthetic"], "evidence.txt", { type: "text/plain" }),
      (value) => progress.push(value),
      () => request,
    );

    expect(result).toEqual(source);
    expect(opened).toEqual(["POST /api/v1/cases/case_synthetic/sources"]);
    expect(progress).toEqual([50, 100]);
  });
});

describe("intake presentation", () => {
  it("renders extraction state and byte sizes without document content", () => {
    expect(
      extractionLabel({
        status: "ocr_required",
        extractor_version: "pypdf.v1",
        segment_count: 0,
        notices: [],
        error_code: "OCR_REQUIRED",
      }),
    ).toBe("需要 OCR · 当前关闭");
    expect(formatFileSize(1536)).toBe("1.5 KB");
  });
});
