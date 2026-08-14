export interface EvidencePackArtifact {
  id: string;
  case_id: string;
  filename: string;
  sha256: string;
  size_bytes: number;
  format_version: number;
  created_at: string;
  download_url: string;
}

export type EvidencePackLoadState =
  | "idle"
  | "loading"
  | "empty"
  | "creating"
  | "ready"
  | "offline"
  | "error";

export type EvidencePackView =
  | "unselected"
  | "loading"
  | "empty"
  | "creating"
  | "ready"
  | "offline"
  | "failure";
