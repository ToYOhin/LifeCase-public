export type CaseTemplateSelector =
  | "warranty"
  | "return_refund"
  | "reimbursement"
  | "subscription_cancellation";

export interface TemplateEvidenceRequirement {
  key: string;
  description: string;
}

export interface TemplateCatalogItem {
  case_type: CaseTemplateSelector;
  template_id: string;
  template_version: number;
  title: string;
  goal_options: string[];
  required_claims: string[];
  evidence_requirements: TemplateEvidenceRequirement[];
  content_sha256: string;
}

export type TemplateCatalogLoadState = "loading" | "ready" | "offline" | "error";

export type TemplateCatalogView =
  | "loading"
  | "empty"
  | "offline"
  | "failure"
  | "populated";
