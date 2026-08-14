export type CaseActivityKind =
  | "case_updated"
  | "source_imported"
  | "extraction_updated"
  | "claim_updated"
  | "agent_updated"
  | "action_updated";

export interface CaseNextActionSummary {
  id: string;
  kind: "collect_evidence" | "prepare_communication" | "follow_up" | "record_outcome";
  status:
    | "proposed"
    | "blocked"
    | "ready"
    | "running"
    | "done"
    | "failed"
    | "cancelled"
    | "approved";
  risk_level: "A" | "B" | "C";
  blocker_count: number;
  version: number;
}

export interface MissingEvidenceSummary {
  required_claims: string[];
  evidence_requirements: string[];
  total: number;
}

export interface CaseWorkbenchItem {
  id: string;
  type: string;
  template_id: string;
  template_version: number;
  title: string;
  goal: string;
  status: string;
  priority: number;
  version: number;
  is_today: boolean;
  next_action: CaseNextActionSummary | null;
  deadline_risk: "not_calculated";
  missing_evidence: MissingEvidenceSummary;
  last_activity_at: string;
  last_activity_kind: CaseActivityKind;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
}

export type WorkbenchLoadState = "loading" | "ready" | "offline" | "error";

export type WorkbenchView =
  | "loading"
  | "empty"
  | "offline"
  | "failure"
  | "populated";
