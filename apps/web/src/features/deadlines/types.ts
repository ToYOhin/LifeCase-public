export type DeadlineKind = "explicit" | "calculated" | "suggested";
export type DeadlineDateKind = "utc_instant" | "local_date";
export type DeadlineStatus =
  | "proposed"
  | "confirmed"
  | "passed"
  | "completed"
  | "dismissed";

export interface DeadlineBasis {
  claim_id: string;
  claim_version: number;
  value_sha256: string;
  field: string;
  date_kind: DeadlineDateKind;
}

export interface DeadlineRule {
  id: string;
  version: number;
  parameters: Record<string, unknown>;
  parameters_sha256: string;
}

export interface DeadlineRecord {
  id: string;
  case_id: string;
  kind: DeadlineKind;
  date_kind: DeadlineDateKind;
  due_value: string;
  timezone: string;
  status: DeadlineStatus;
  uncertainty:
    | "evidence_backed_date"
    | "deterministic_rule_result_requires_confirmation"
    | "personal_follow_up_preference_not_external_obligation";
  confidence: number | null;
  rule: DeadlineRule | null;
  bases: DeadlineBasis[];
  version: number;
  definition_sha256: string;
  created_at: string;
  updated_at: string;
}

export interface SuggestedDeadlineInput {
  date_kind: DeadlineDateKind;
  due_value: string;
  timezone: string;
  actor: string;
  reason: string;
}

export interface DeadlineStatusInput {
  expected_version: number;
  actor: string;
  reason: string;
}

export type DeadlineLoadState = "idle" | "loading" | "ready" | "offline" | "error";
export type DeadlineView =
  | "unselected"
  | "loading"
  | "empty"
  | "offline"
  | "failure"
  | "populated";
