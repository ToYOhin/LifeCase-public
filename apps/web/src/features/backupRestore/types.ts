export interface BackupArtifact {
  id: string;
  filename: string;
  sha256: string;
  size_bytes: number;
  format_version: number;
  minimum_application_version: string;
  case_count: number;
  member_count: number;
  created_at: string;
  download_url: string;
}

export interface RestoreValidation {
  validation_id: string;
  backup_sha256: string;
  format_version: number;
  minimum_application_version: string;
  case_count: number;
  member_count: number;
  database_sha256: string;
}

export interface RestoreResult {
  validation_id: string;
  runtime_id: string;
  case_count: number;
  migrated: boolean;
}

export type BackupRestoreState =
  | "loading"
  | "empty"
  | "creating"
  | "ready"
  | "selected"
  | "validating"
  | "needs_confirmation"
  | "restoring"
  | "success"
  | "offline"
  | "error";
