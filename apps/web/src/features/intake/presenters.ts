import type { SourceExtraction } from "./types";

const extractionLabels: Record<string, string> = {
  completed: "已提取",
  completed_with_notices: "已提取 · 有安全提示",
  failed: "提取失败",
  ocr_required: "需要 OCR · 当前关闭",
  pending: "等待提取",
};

const errorGuidance: Record<string, string> = {
  case_not_found: "案件不存在，请重新建立案件。",
  media_type_unsupported: "请选择 TXT、EML 或文本型 PDF。",
  source_too_large: "文件超过本机设置的大小限制，请选择更小的材料。",
  source_path_invalid: "文件无法读取，请重新选择一个普通文件。",
  source_integrity_failed: "文件完整性校验失败，请检查原文件后重试。",
  source_storage_failed: "本地存储暂时不可用，请稍后重试。",
  extraction_failed: "文本提取未完成。请先核对已导入材料，再处理文件后重新选择。",
  validation_error: "请补全案件名称、目标和材料文件。",
};

export function extractionLabel(extraction: SourceExtraction): string {
  return extractionLabels[extraction.status] ?? "状态未知";
}

export function extractionGuidance(extraction: SourceExtraction): string | null {
  if (!["failed", "ocr_required", "pending"].includes(extraction.status)) return null;
  if (extraction.status === "ocr_required") return "这份 PDF 没有可提取的文本。当前不支持 OCR，请另存为含文本的 PDF，或提供 TXT 材料。";
  if (extraction.status === "pending") return "材料已保存，尚未完成提取。请先重新读取材料列表，核对后再决定是否重新导入。";
  const guidance: Record<string, string> = {
    TEXT_ENCODING_INVALID: "文本不是有效的 UTF-8 编码。请将副本另存为 UTF-8 TXT 后重新选择。",
    EMPTY_CONTENT: "文件没有可提取的文字。请检查内容后选择含文字的材料。",
    EMAIL_INVALID: "邮件格式无法读取。请重新导出有效的 EML 文件。",
    EMAIL_BODY_UNSUPPORTED: "邮件没有可读取的纯文本正文。请将正文另存为 TXT 后导入。",
    PDF_MALFORMED: "PDF 文件格式损坏或不完整。请重新导出可正常打开的 PDF。",
    PDF_ENCRYPTED: "当前无法提取加密 PDF。请提供你有权使用的未加密副本。",
    PDF_LIMIT_EXCEEDED: "PDF 超过提取限制。请另存为较小的文本型 PDF 后导入。",
    EXTRACTION_TIMEOUT: "文本提取超时。可稍后人工重试，或选择较小的材料。",
    SEGMENT_PERSISTENCE_FAILED: "文本结果未能保存。请恢复本地存储后再人工重试。",
    OBJECT_INTEGRITY_FAILED: "保存文件的完整性无法核对。请检查原文件，保留原件并重新选择材料。",
  };
  return guidance[extraction.error_code ?? ""] ?? "材料已保存，但未能提取文本。请检查文件，处理后重新选择材料。";
}

export function errorGuidanceFor(code: string, fallback: string): string {
  return errorGuidance[code] ?? fallback;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function noticeLabel(code: string): string {
  if (code === "ATTACHMENT_SKIPPED") {
    return "附件已安全跳过";
  }
  if (code === "HTML_SKIPPED") {
    return "HTML 正文已安全跳过";
  }
  return code;
}
