export type AgentStageName =
  | "extract"
  | "normalize"
  | "link_claims"
  | "classify_case"
  | "check_gaps"
  | "propose_actions"
  | "validate";

export interface AgentDemoCapability {
  status: "enabled" | "disabled";
  provider: "deterministic_fake" | "disabled";
  action: string;
  supported_case_types: ["warranty"];
}

export interface AgentUsage {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_minor: number;
  cost_currency: "CNY";
}

export interface AgentBudget {
  max_calls: number;
  max_input_tokens: number;
  max_output_tokens: number;
  max_total_tokens: number;
  max_cost_minor: number;
  cost_currency: "CNY";
}

export interface AgentStageTrace {
  name: AgentStageName;
  status: "running" | "checkpointed" | "needs_review" | "cancelled";
  version: number;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_minor: number;
  provider: string | null;
  model_id: string | null;
  error_code: string | null;
  retryable: boolean | null;
  summary: string | null;
  updated_at: string;
}

export interface AgentClaim {
  id: string;
  field: string;
  value: unknown;
  status: string;
  confidence: number | null;
  created_by: "agent";
  version: number;
}

export interface AgentCitation {
  case_id: string;
  source_id: string;
  segment_id: string;
  sequence: number;
  page: number | null;
  text_sha256: string;
  quote_start: number;
  quote_end: number;
  quote_hash: string;
}

export interface AgentAnswer {
  status: "answered" | "unknown";
  statements: string[];
  citations: AgentCitation[];
  uncertainty_reason: string | null;
}

export interface AgentToolCall {
  name: "evidence_context.search.v1";
  risk: "A";
  call_count: 1;
  result: "match" | "no_match";
}

export interface AgentRun {
  id: string;
  case_id: string;
  status: "queued" | "running" | "checkpointed" | "needs_review" | "cancelled" | "completed";
  version: number;
  current_stage: AgentStageName | null;
  last_completed_stage: AgentStageName | null;
  cancellation_requested: boolean;
  can_execute: boolean;
  can_retry: boolean;
  recovery_required: boolean;
  provider: string | null;
  model_id: string | null;
  budget: AgentBudget;
  usage: AgentUsage;
  stages: AgentStageTrace[];
  claims: AgentClaim[];
  answer: AgentAnswer | null;
  tool_calls: AgentToolCall[];
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface AgentDemoResult {
  demo_version: "portfolio_agent_demo.v1";
  capability: AgentDemoCapability;
  run: AgentRun | null;
}

export interface LiveAgentCapability {
  status: "disabled" | "unavailable" | "ready";
  provider: "disabled" | "openai" | "groq" | "mistral" | "deepseek" | "gemini" | "anthropic";
  model_id: string | null;
  action: string;
  supported_case_types: ["warranty"];
}

export interface LiveAgentPreview {
  case_type: "warranty";
  evidence: {
    segment_count: number;
    utf8_bytes: number;
  };
  budget: AgentBudget;
  read_only_notice: string;
  preview_sha256: string;
}

export interface LiveAgentResult {
  live_version: "live_agent_read_only.v1";
  capability: LiveAgentCapability;
  preview: LiveAgentPreview;
  run: AgentRun | null;
  run_history: LiveAgentRunHistory[];
}

export interface LiveAgentRunHistory {
  version: number;
  id: string;
  status: AgentRun["status"];
  provider: string | null;
  model_id: string | null;
  answer_status: AgentAnswer["status"] | null;
  calls: number;
  total_tokens: number;
  created_at: string;
  completed_at: string | null;
}

export type AgentDemoLoadState = "idle" | "loading" | "ready" | "running" | "offline" | "error";
export type AgentDemoView =
  | "unselected"
  | "loading"
  | "disabled"
  | "offline"
  | "failure"
  | "ready"
  | "running"
  | "completed";
