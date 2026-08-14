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
  validation_error: "请补全案件名称、目标和材料文件。",
};

export function extractionLabel(extraction: SourceExtraction): string {
  return extractionLabels[extraction.status] ?? "状态未知";
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
