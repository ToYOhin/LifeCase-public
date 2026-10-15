import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CaseOverviewPanel } from "./CaseOverviewPanel";
import type { CaseRecord } from "./types";

const caseRecord: CaseRecord = {
  id: "case_a", type: "warranty", template_id: "warranty_claim", template_version: 1,
  title: "合成报修", goal: "准备维修材料", status: "intake", priority: 0, version: 1,
  created_at: "2026-09-22T00:00:00Z", updated_at: "2026-09-22T00:00:00Z", closed_at: null,
  sources: [{ id: "source_a", case_id: "case_a", original_name: "合成报修记录.txt", media_type: "text/plain",
    sha256: "a".repeat(64), size_bytes: 2048, trust_label: "untrusted", imported_at: "2026-09-22T00:00:00Z",
    extraction: { status: "failed", extractor_version: "synthetic-extractor.v1", segment_count: 2,
      notices: [{ code: "ATTACHMENT_SKIPPED", count: 1 }], error_code: "synthetic_extraction_failed" } }],
};

describe("material overview disclosure", () => {
  it("lets an already saved pending Source re-read its status without another upload", () => {
    const pending = { ...caseRecord, sources: [{ ...caseRecord.sources[0], extraction: { ...caseRecord.sources[0].extraction, status: "pending", error_code: null } }] };
    const markup = renderToStaticMarkup(<CaseOverviewPanel caseRecord={pending} template={undefined}
      selectedFile={null} failedSources={0} isIdle isRestoring={false} isUploading={false} uploadProgress={0}
      onReadImport={() => undefined} onFileChange={() => undefined} onUpload={() => undefined} />);
    expect(markup).toContain("重新读取材料列表</button>");
  });
  it("gives saved extraction failures a manual file-selection entry without resubmitting automatically", () => {
    const failed = { ...caseRecord, sources: [{ ...caseRecord.sources[0], extraction: { ...caseRecord.sources[0].extraction, error_code: "TEXT_ENCODING_INVALID" } }] };
    const markup = renderToStaticMarkup(<CaseOverviewPanel caseRecord={failed} template={undefined}
      selectedFile={null} failedSources={1} isIdle isRestoring={false} isUploading={false} uploadProgress={0}
      onFileChange={() => undefined} onUpload={() => undefined} />);
    expect(markup).toContain("UTF-8"); expect(markup).toContain("重新选择材料"); expect(markup).toContain("新增一条");
  });

  it("offers read-only recovery while the upload result is uncertain and blocks another upload", () => {
    const markup = renderToStaticMarkup(<CaseOverviewPanel caseRecord={caseRecord} template={undefined}
      selectedFile={new File(["synthetic"], "preserved.txt")} failedSources={1} isIdle isRestoring={false} isUploading={false} uploadProgress={0}
      importNeedsRead retryUpload onReadImport={() => undefined} onFileChange={() => undefined} onUpload={() => undefined} />);
    expect(markup).toContain("preserved.txt"); expect(markup).toContain("重新读取材料列表");
    expect(markup).toMatch(/<button[^>]*type="submit"[^>]*disabled=""/);
  });

  it("keeps the selected file and extraction failures visible while hiding technical metadata by default", () => {
    const markup = renderToStaticMarkup(<CaseOverviewPanel caseRecord={caseRecord} template={undefined}
      selectedFile={null} failedSources={1} isIdle isRestoring={false} isUploading={false} uploadProgress={0}
      onFileChange={() => undefined} onUpload={() => undefined} />);
    const initiallyVisible = markup.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g, "");
    expect(initiallyVisible).toContain("合成报修记录.txt");
    expect(initiallyVisible).toContain("2.0 KB");
    expect(initiallyVisible).toContain("提取失败");
    expect(initiallyVisible).toContain("附件已安全跳过");
    expect(initiallyVisible).toContain("synthetic_extraction_failed");
    expect(initiallyVisible).not.toContain("a".repeat(64));
    expect(initiallyVisible).not.toContain("synthetic-extractor.v1");
    expect(initiallyVisible).not.toContain("text/plain ·");
    expect(markup).toContain("a".repeat(64));
    expect(markup).toContain("synthetic-extractor.v1");
    expect(markup).not.toMatch(/<details[^>]*\sopen(?:[\s=>])/);
    expect(markup).not.toContain("正文查看器");
  });
});
