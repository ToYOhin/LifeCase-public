import { describe, expect, it } from "vitest";

import { getAnalysisActionDraft, saveAnalysisActionDraft, type AnalysisActionDraft } from "./analysisActionDraftApi";

const preview: AnalysisActionDraft = {
  case_id: "case synthetic", run_id: "portfolio-live-q1-synthetic", run_version: 4,
  question: "报修时应说明哪些情况？", basis_sha256: "a".repeat(64), answer_sha256: "b".repeat(64),
  draft_text: "请协助核对报修流程。", saved_action: null,
};

describe("analysis action draft transport", () => {
  it("reads only the requested Case and immutable Run without creating an Action", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const result = await getAnalysisActionDraft(preview.case_id, preview.run_id, async (url, init) => {
      calls.push({ url: String(url), init });
      return Response.json(preview);
    });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/v1/cases/case%20synthetic/analysis-action-draft?run_id=portfolio-live-q1-synthetic");
    expect(calls[0].init).toMatchObject({ method: "GET", cache: "no-store" });
    expect(result.saved_action).toBeNull();
  });

  it("saves the edited body with exact analysis and Action versions, actor and reason", async () => {
    let body: unknown;
    const existing = { id: "action_synthetic", version: 3, draft_text: "已保存正文", status: "ready" as const };
    const base = { ...preview, draft_text: existing.draft_text, saved_action: existing };
    const result = await saveAnalysisActionDraft(base, "我的修改", "synthetic_user", "核对后更新", async (_url, init) => {
      body = JSON.parse(String(init?.body));
      return Response.json({ ...base, draft_text: "我的修改", saved_action: { ...existing, draft_text: "我的修改", version: 4 } });
    });
    expect(body).toEqual({ expected_run_id: preview.run_id, expected_run_version: 4,
      basis_sha256: preview.basis_sha256, answer_sha256: preview.answer_sha256,
      draft_text: "我的修改", actor: "synthetic_user", reason: "核对后更新",
      expected_action_id: existing.id, expected_action_version: 3 });
    expect(result.saved_action?.version).toBe(4);
  });

  it.each([
    { case_id: "another_case" }, { run_id: "portfolio-live-q1-another" },
    { basis_sha256: "unverified" }, { draft_text: "x".repeat(501) },
    { saved_action: { id: "action_one", version: 1, draft_text: "different body", status: "ready" } },
  ])("rejects unbound or malformed successful reads: %j", async (change) => {
    await expect(getAnalysisActionDraft(preview.case_id, preview.run_id, async () => Response.json({ ...preview, ...change })))
      .rejects.toMatchObject({ code: "analysis_action_invalid_response" });
  });

  it("rejects an uncertain saved body instead of reporting success", async () => {
    await expect(saveAnalysisActionDraft(preview, "编辑后的内容", "user", "reviewed", async () => Response.json({
      ...preview, saved_action: { id: "action_one", version: 1, draft_text: preview.draft_text, status: "ready" },
    }))).rejects.toMatchObject({ code: "analysis_action_invalid_response" });
  });

  it("preserves server conflicts and cannot treat a failed read as an empty draft", async () => {
    await expect(getAnalysisActionDraft(preview.case_id, preview.run_id, async () => Response.json({
      error: { code: "analysis_action_stale", message: "依据已变化", retryable: true },
    }, { status: 409 }))).rejects.toMatchObject({ code: "analysis_action_stale" });
    await expect(getAnalysisActionDraft(preview.case_id, preview.run_id, async () => { throw new Error("offline"); }))
      .rejects.toMatchObject({ code: "network_error" });
  });
});
