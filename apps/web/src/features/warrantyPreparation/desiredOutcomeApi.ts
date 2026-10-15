import { LifeCaseApiError } from "../intake/api";
import type { ReviewClaim } from "../review/types";

export function desiredOutcomeFitsClaim(intent: string): boolean {
  return new TextEncoder().encode(JSON.stringify(intent.normalize("NFKC").trim())).length <= 4096;
}

export async function registerDesiredOutcome(caseId: string, version: number, actor: string, reason: string,
  signal: AbortSignal, request: typeof fetch = fetch): Promise<ReviewClaim> {
  let response: Response;
  try {
    response = await request(`/api/v1/cases/${encodeURIComponent(caseId)}/desired-outcome`, {
      method: "POST", signal, cache: "no-store", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_intent_version: version, actor, reason }),
    });
  } catch { throw new LifeCaseApiError("network_error", "登记结果尚未确认，请先重新读取期望结果。", true); }
  let payload: unknown;
  try { payload = await response.json(); } catch { throw uncertainResponse(); }
  if (!response.ok) {
    const error = (payload as { error?: { code?: unknown; message?: unknown } } | null)?.error;
    if (response.status < 500 && typeof error?.code === "string" && typeof error.message === "string") {
      const guidance: Record<string, string> = {
        desired_outcome_conflict: "已有期望结果，请重新读取后复核或修正原记录。",
        preparation_intent_version_conflict: "其他窗口已保存新的诉求，请先读取最新已保存诉求并核对。",
        claim_invalid: "当前案件或已保存诉求不符合登记条件。请核对案件状态，保存非空且较短的诉求后再登记。",
        case_not_found: "当前案件无法找到，请重新读取案件列表。",
      };
      throw new LifeCaseApiError(error.code, guidance[error.code] ?? "登记条件未通过，请重新核对已保存诉求和登记人、理由。");
    }
    throw uncertainResponse();
  }
  const claim = payload as Partial<ReviewClaim> | null;
  if (!claim || claim.case_id !== caseId || claim.field !== "desired_outcome" || claim.status !== "candidate" ||
      claim.created_by !== "user" || claim.version !== 1 || typeof claim.id !== "string" || !claim.id ||
      typeof claim.value !== "string" || !claim.value.trim()) throw uncertainResponse();
  return claim as ReviewClaim;
}

function uncertainResponse(): LifeCaseApiError {
  return new LifeCaseApiError("unexpected_response", "登记结果无法确认，请先重新读取期望结果。", true);
}
