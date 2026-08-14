import type { CaseTemplateSelector } from "../templates/types";

export interface SourceNotice {
  code: string;
  count: number;
}

export interface SourceExtraction {
  status: string;
  extractor_version: string | null;
  segment_count: number;
  notices: SourceNotice[];
  error_code: string | null;
}

export interface SourceRecord {
  id: string;
  case_id: string;
  original_name: string;
  media_type: string;
  sha256: string;
  size_bytes: number;
  trust_label: string;
  imported_at: string;
  extraction: SourceExtraction;
}

export interface CaseRecord {
  id: string;
  type: string;
  template_id: string;
  template_version: number;
  title: string;
  goal: string;
  status: CaseStatus;
  priority: number;
  version: number;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  sources: SourceRecord[];
}

export type CaseStatus =
  | "intake"
  | "needs_review"
  | "ready"
  | "in_progress"
  | "waiting_external"
  | "blocked"
  | "resolved"
  | "abandoned"
  | "archived";

export interface CaseTransitionInput {
  expected_version: number;
  target_status: CaseStatus;
  actor: string;
  reason: string;
}

export interface CaseTransitionRecord {
  id: string;
  case_id: string;
  prior_status: CaseStatus;
  new_status: CaseStatus;
  prior_version: number;
  new_version: number;
  actor: string;
  reason: string;
  transitioned_at: string;
}

export interface CaseCreateInput {
  title: string;
  goal: string;
  template_id: CaseTemplateSelector;
}

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
    retryable: boolean;
    request_id: string;
  };
}
