import { getCase, LifeCaseApiError, offlineGatewayError } from "../intake/api";
import type { SourceRecord } from "../intake/types";
import { preparationSignature, splitCitation, validateProjection } from "./preparation";
import type { CurrentFacts } from "./types";

export interface PreparationSnapshot {
  facts: CurrentFacts;
  sources: SourceRecord[];
  signature: string;
}

export async function getPreparationSnapshot(caseId: string, signal: AbortSignal, request: typeof fetch = fetch): Promise<PreparationSnapshot> {
  return getCurrentFactsSnapshot(caseId, "warranty", signal, request);
}

export async function getCurrentFactsSnapshot(caseId: string, caseType: string, signal: AbortSignal, request: typeof fetch = fetch): Promise<PreparationSnapshot> {
  const scopedRequest: typeof fetch = (input, init) => request(input, { ...init, signal, cache: "no-store" });
  const [facts, record] = await Promise.all([
    getCurrentFacts(caseId, scopedRequest),
    getCase(caseId, scopedRequest),
  ]);
  try {
    validateProjection(caseId, facts, caseType);
    if (record.id !== caseId || record.type !== caseType || !Array.isArray(record.sources) ||
        record.sources.some((source) => source.case_id !== caseId)) throw new Error("案件材料不匹配");
    for (const item of facts.items) {
      for (const citation of item.citations) {
        const source = record.sources.find((entry) => entry.id === citation.source_id);
        if (!source || source.original_name !== citation.source_name) throw new Error("引用材料不匹配");
        if (await sha256(citation.text) !== citation.text_sha256 ||
            await sha256(splitCitation(citation).quote) !== citation.quote_hash) throw new Error("引用内容不一致");
      }
    }
  } catch {
    throw new LifeCaseApiError("current_facts_integrity_failed", "事实或来源校验未通过，已停止展示旧内容。");
  }
  return { facts, sources: record.sources, signature: preparationSignature(facts, record.sources) };
}

async function getCurrentFacts(caseId: string, request: typeof fetch): Promise<CurrentFacts> {
  let response: Response;
  try {
    response = await request(`/api/v1/cases/${encodeURIComponent(caseId)}/current-facts`);
  } catch {
    throw new LifeCaseApiError("network_error", "无法连接本地 LifeCase API", true);
  }
  let payload: unknown;
  try { payload = await response.json(); } catch {
    throw offlineGatewayError(response) ?? new LifeCaseApiError("unexpected_response", "本地 API 返回了无法识别的结果");
  }
  if (!response.ok) throw offlineGatewayError(response) ?? new LifeCaseApiError("current_facts_unavailable", "当前事实暂时不可用，请重新读取。", true);
  return payload as CurrentFacts;
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
