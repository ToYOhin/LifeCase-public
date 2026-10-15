import type { ReviewCitation, ReviewClaim } from "../review/types";

export interface CurrentFact {
  claim: ReviewClaim;
  value_sha256?: string;
  confirmation_basis: "evidence" | "user" | "correction" | null;
  confirmation: { actor: string; reason: string; occurred_at: string } | null;
  evidence_status: "valid" | "unavailable" | "none";
  citations: ReviewCitation[];
  conflict: boolean;
  usable_as_fact: boolean;
}

export interface CurrentFacts {
  case_id: string;
  case_type: string;
  items: CurrentFact[];
}
