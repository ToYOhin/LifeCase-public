export type ActionKind =
  | "collect_evidence"
  | "prepare_communication"
  | "follow_up"
  | "record_outcome";

export type ActionStatus =
  | "proposed"
  | "blocked"
  | "ready"
  | "running"
  | "done"
  | "failed"
  | "cancelled"
  | "approved";

export type ActionRiskLevel = "A" | "B" | "C";

export interface ActionBlocker {
  code: string;
  field: string | null;
  authority_id: string | null;
}

export interface ActionRequirement {
  claim_id: string;
  claim_version: number;
  value_sha256: string;
  field: string;
}

export interface ActionDependency {
  action_id: string;
  action_version: number;
  status: ActionStatus;
}

export interface ActionDeadline {
  deadline_id: string;
  deadline_version: number;
  definition_sha256: string;
  date_kind: "utc_instant" | "local_date";
  due_value: string;
  timezone: string;
  status: string;
}

export interface ActionCompletionEvidence {
  authority_type: "claim" | "source" | "artifact";
  authority_id: string;
  authority_version: number | null;
  authority_sha256: string;
}

export interface ActionRecord {
  id: string;
  case_id: string;
  kind: ActionKind;
  status: ActionStatus;
  risk_level: ActionRiskLevel;
  payload: Record<string, string>;
  payload_sha256: string;
  blockers: ActionBlocker[];
  blockers_sha256: string;
  requirements: ActionRequirement[];
  dependencies: ActionDependency[];
  deadline: ActionDeadline | null;
  completion_evidence: ActionCompletionEvidence[];
  communication_draft: string | null;
  topological_position: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ActionStatusInput {
  expected_version: number;
  actor: string;
  reason: string;
}

export interface ActionCreateInput {
  kind: ActionKind;
  risk_level: ActionRiskLevel;
  payload: Record<string, string>;
  requirements: [];
  dependencies: [];
  deadline: null;
  actor: string;
  reason: string;
}

export interface ActionCompleteInput extends ActionStatusInput {
  evidence: Array<{
    authority_type: "claim" | "source" | "artifact";
    authority_id: string;
    expected_version: number | null;
    authority_sha256: string;
  }>;
}

export type ApprovalStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "expired"
  | "invalidated";

export interface ApprovalRecord {
  id: string;
  action_id: string;
  case_id: string;
  action_version: number;
  payload_sha256: string;
  summary: string;
  summary_sha256: string;
  status: ApprovalStatus;
  version: number;
  requested_by: string;
  requested_reason: string;
  created_at: string;
  updated_at: string;
}

export interface ApprovalRequestInput {
  expected_action_version: number;
  payload_sha256: string;
  summary: string;
  actor: string;
  reason: string;
}

export interface ApprovalDecisionInput {
  approval_id: string;
  expected_approval_version: number;
  expected_action_version: number;
  actor: string;
  reason: string;
}

export interface ApprovalDecisionResult {
  approval: ApprovalRecord;
  action: ActionRecord;
}

export type ActionLoadState = "idle" | "loading" | "ready" | "offline" | "error";
export type ActionView =
  | "unselected"
  | "loading"
  | "empty"
  | "offline"
  | "failure"
  | "populated";
