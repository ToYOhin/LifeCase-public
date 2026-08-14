import type { FormEvent } from "react";

import { extractionLabel, formatFileSize, noticeLabel } from "./presenters";
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
}: CaseOverviewPanelProps) {
  return (
    <section className="desk" aria-label="案件概览与证据导入台">
      <div className="intake-column">
        <div className="section-heading compact">
          <div>
            <p className="section-label">CASE OVERVIEW</p>
            <h2>案件材料</h2>
          </div>
          <span className="step-mark">01</span>
        </div>
        <p className="intake-guidance">
          导入当前案件材料，并在右侧检查提取状态。原文件保持只读。
        </p>

        <div className="rule" />

        <div className="section-heading compact intake-action-heading">
          <div>
            <p className="section-label">MATERIAL INTAKE</p>
            <strong>导入材料</strong>
          </div>
          <span className="step-mark">下一步</span>
        </div>
        <form className="upload-form" onSubmit={onUpload}>
          <label className="file-picker">
            <span>{selectedFile ? selectedFile.name : "选择 TXT、EML 或文本型 PDF"}</span>
            <input
              name="file"
              type="file"
              accept=".txt,.eml,.pdf,text/plain,message/rfc822,application/pdf"
              onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
              disabled={!isIdle}
            />
          </label>
          {selectedFile && (
            <p className="file-note">
              {formatFileSize(selectedFile.size)} · 原文件只读，不会移动或删除
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
            disabled={!selectedFile || !isIdle}
          >
            导入并提取
          </button>
        </form>
        {isRestoring && <p className="intake-guidance" role="status">正在恢复案件…</p>}
      </div>

      <div className="register-column">
        <div className="register-head">
          <div>
            <p className="section-label">EVIDENCE REGISTER</p>
            <h3>证据登记册</h3>
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
            <h3>证据登记册是空的</h3>
            <p>选择材料后，这里会显示哈希、信任标签和提取结果。</p>
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
                      <p>{source.media_type} · {formatFileSize(source.size_bytes)}</p>
                    </div>
                    <span className={`extraction-state state-${source.extraction.status}`}>
                      {extractionLabel(source.extraction)}
                    </span>
                  </div>
                  <div className="hash-rail" title={source.sha256}>
                    <span>SHA-256</span>
                    <code>{source.sha256}</code>
                  </div>
                  <div className="source-meta">
                    <span className="trust-stamp">不可信原始材料</span>
                    <span>{source.extraction.segment_count} 个文本分段</span>
                    <span>提取器 {source.extraction.extractor_version ?? "—"}</span>
                  </div>
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
                  <div className="viewer-placeholder">
                    <span>正文查看器</span>
                    <p>当前保留来源状态；外部发送、提交和连接器执行不在本工作台开放。</p>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
