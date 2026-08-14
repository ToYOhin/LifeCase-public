export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type ClaimStatus =
  | "candidate"
  | "inferred"
  | "confirmed"
  | "corrected"
  | "rejected";

export interface ReviewClaim {
  id: string;
  case_id: string;
  field: string;
  value: JsonValue;
  status: ClaimStatus;
  confidence: number | null;
  created_by: "user" | "rule" | "agent";
  contradiction: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ReviewCitation {
  claim_id: string;
  case_id: string;
  source_id: string;
  source_name: string;
  segment_id: string;
  sequence: number;
  page: number | null;
  text_sha256: string;
  text: string;
  quote_start: number;
  quote_end: number;
  quote_hash: string;
  quote_text: string;
  relation: "supports" | "contradicts" | "context";
  attached_by: string;
  attached_at: string;
}

export interface EvidenceReviewItem {
  claim: ReviewClaim;
  citations: ReviewCitation[];
  settled_claims: ReviewClaim[];
  is_next: boolean;
}

export interface EvidenceReviewResult {
  case_id: string;
  next_claim_id: string | null;
  items: EvidenceReviewItem[];
}

export type ReviewLoadState = "idle" | "loading" | "ready" | "offline" | "error";
export type ReviewView =
  | "unselected"
  | "loading"
  | "empty"
  | "offline"
  | "failure"
  | "populated";

export interface ReviewDecisionInput {
  expected_version: number;
  actor: string;
  reason: string;
}

export interface SourceSegment {
  id: string;
  source_id: string;
  source_name: string;
  sequence: number;
  page: number | null;
  text: string;
  extraction_method: string;
  extractor_version: string | null;
  confidence: number | null;
  text_sha256: string;
}

export interface ManualClaimInput {
  field: string;
  value: JsonValue;
  segment_id: string;
  quote_start: number;
  quote_end: number;
  actor: string;
  reason: string;
}
