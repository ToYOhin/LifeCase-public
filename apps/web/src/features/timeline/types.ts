export type TimelineEventKind =
  | "source_imported"
  | "source_extraction_status"
  | "artifact_created"
  | "case_transitioned"
  | "claim_created"
  | "claim_source_attached"
  | "claim_confirmed"
  | "claim_corrected"
  | "claim_rejected"
  | "agent_run_status"
  | "agent_stage_status"
  | "deadline_created"
  | "deadline_confirmed"
  | "deadline_passed"
  | "deadline_completed"
  | "deadline_dismissed"
  | "action_created"
  | "action_revised"
  | "action_revalidated"
  | "action_started"
  | "action_completed"
  | "action_failed"
  | "action_cancelled"
  | "action_approved"
  | "approval_requested"
  | "approval_approved"
  | "approval_rejected"
  | "approval_expired"
  | "approval_invalidated";

export interface TimelineEvent {
  event_id: string;
  case_id: string;
  kind: TimelineEventKind;
  occurred_at: string;
  authority_type: string;
  authority_id: string;
  subject_id: string;
  parent_id: string | null;
  origin: "local_import" | "extractor" | "recorded_actor" | "agent" | "artifact_service";
  actor: string | null;
  label: string | null;
  status: string | null;
  prior_status: string | null;
  version: number | null;
  reason: string | null;
}

export interface TimelineResult {
  case_id: string;
  events: TimelineEvent[];
  availability: {
    approvals: "available";
    communications: "available_as_action_projection";
  };
}

export type TimelineLoadState = "idle" | "loading" | "ready" | "offline" | "error";
export type TimelineView =
  | "unselected"
  | "loading"
  | "empty"
  | "offline"
  | "failure"
  | "populated";
