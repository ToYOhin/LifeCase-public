import { useRef, type FormEvent } from "react";

import { extractionGuidance, extractionLabel, formatFileSize, noticeLabel } from "./presenters";
import type { CaseRecord } from "./types";
import type { TemplateCatalogItem } from "../templates/types";
import { statusLabel } from "../workbench/presenters";

interface CaseOverviewPanelProps {
  caseRecord: CaseRecord;
  template: TemplateCatalogItem | undefined;
  selectedFile: File | null;
  failedSources: number;
  isIdle: boolean;
  isRestoring: boolean;
  isUploading: boolean;
  uploadProgress: number;
  onFileChange: (file: File | null) => void;
  onUpload: (event: FormEvent<HTMLFormElement>) => void;
  importNeedsRead?: boolean;
  retryUpload?: boolean;
  onReadImport?: () => void;
}

export function CaseOverviewPanel({
  caseRecord,
  template,
  selectedFile,
  failedSources,
  isIdle,
  isRestoring,
  isUploading,
  uploadProgress,
  onFileChange,
  onUpload,
  importNeedsRead = false,
  retryUpload = false,
  onReadImport,
}: CaseOverviewPanelProps) {
  const fileInput = useRef<HTMLInputElement>(null);
  return (
    <section className="desk" aria-label="案件概览与证据导入台">
      <div className="intake-column">
        <div className="section-heading compact">
          <div>
            <h2>导入材料</h2>
          </div>
        </div>
        <p className="intake-guidance">
          原文件不会移动或修改。
        </p>

        <div className="rule" />

        <form className="upload-form" onSubmit={onUpload}>
          <label className="file-picker">
            <span>{selectedFile ? selectedFile.name : "选择 TXT、EML 或文本型 PDF"}</span>
            <input
              name="file"
              ref={fileInput}
              type="file"
              accept=".txt,.eml,.pdf,text/plain,message/rfc822,application/pdf"
              onChange={(event) => { onFileChange(event.target.files?.[0] ?? null); event.currentTarget.value = ""; }}
              disabled={!isIdle || importNeedsRead}
            />
          </label>
          {selectedFile && (
            <p className="file-note">
              {formatFileSize(selectedFile.size)}
            </p>
          )}
          {isUploading && (
            <div className="progress-block" aria-live="polite">
              <div>
                <span>正在上传并验证</span>
                <strong>{uploadProgress}%</strong>
              </div>
              <progress max="100" value={uploadProgress} />
            </div>
          )}
          <button
            type="submit"
            className="secondary-action"
            disabled={!selectedFile || !isIdle || importNeedsRead}
          >
            {retryUpload ? "重试导入" : "导入并提取"}
          </button>
        </form>
        {importNeedsRead && <p role="status">先核对材料列表，避免重复上传。当前文件选择保留。</p>}
        {onReadImport && (importNeedsRead || failedSources > 0 || caseRecord.sources.some(source => source.extraction.status === "pending")) && <button type="button" className="secondary-action"
          disabled={!isIdle} onClick={onReadImport}>重新读取材料列表</button>}
        {isRestoring && <p className="intake-guidance" role="status">正在恢复案件…</p>}
      </div>

      <div className="register-column">
        <div className="register-head">
          <div>
            <h3>已导入材料</h3>
            <p>{caseRecord.goal}</p>
          </div>
          <div className="case-seal">
            <span>{caseRecord.sources.length}</span>
            <small>份材料</small>
          </div>
        </div>

        <div className="summary-strip" aria-label="材料状态摘要">
          <span>案件状态：{statusLabel(caseRecord.status)}</span>
          <span>事务类型：{template?.title ?? "已审阅事务"}</span>
          <span>需处理：{failedSources}</span>
        </div>
        <details className="technical-details">
          <summary>查看技术信息</summary>
          <span>模板 {caseRecord.template_id}@{caseRecord.template_version}</span>
          <span> · 案件版本 {caseRecord.version}</span>
        </details>

        {caseRecord.sources.length === 0 ? (
          <div className="empty-register">
            <span className="empty-glyph">⌁</span>
            <h3>还没有材料</h3>
            <p>导入后可查看文件和提取结果。</p>
          </div>
        ) : (
          <ol className="source-list">
            {caseRecord.sources.map((source, index) => (
              <li key={source.id} className="source-card">
                <div className="source-index">{String(index + 1).padStart(2, "0")}</div>
                <div className="source-main">
                  <div className="source-title-row">
                    <div>
                      <h3>{source.original_name}</h3>
                      <p>{formatFileSize(source.size_bytes)}</p>
                    </div>
                    <span className={`extraction-state state-${source.extraction.status}`}>
                      {extractionLabel(source.extraction)}
                    </span>
                  </div>
                  <details className="material-details">
                    <summary>材料详情</summary>
                    <div className="hash-rail" title={source.sha256}>
                      <span>SHA-256</span>
                      <code>{source.sha256}</code>
                    </div>
                    <div className="source-meta">
                      <span>{source.media_type}</span>
                      <span className="trust-stamp">不可信原始材料</span>
                      <span>{source.extraction.segment_count} 个文本分段</span>
                      <span>提取器 {source.extraction.extractor_version ?? "—"}</span>
                    </div>
                  </details>
                  {source.extraction.notices.length > 0 && (
                    <ul className="notice-list">
                      {source.extraction.notices.map((notice) => (
                        <li key={notice.code}>{noticeLabel(notice.code)} × {notice.count}</li>
                      ))}
                    </ul>
                  )}
                  {source.extraction.error_code && (
                    <p className="source-error">
                      状态代码：{source.extraction.error_code}。原文件已安全保留。
                    </p>
                  )}
                  {extractionGuidance(source.extraction) && <div className="source-recovery">
                    <p>{extractionGuidance(source.extraction)}</p>
                    <button type="button" className="secondary-action" disabled={!isIdle || importNeedsRead}
                      onClick={() => { fileInput.current?.focus(); fileInput.current?.click(); }}>重新选择材料</button>
                    <p><small>重新导入会新增一条材料记录，原材料与失败记录保留。</small></p>
                  </div>}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
