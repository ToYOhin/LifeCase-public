import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CaseDocket } from "./CaseDocket";
import type { CaseWorkbenchItem } from "./types";

const item: CaseWorkbenchItem = {
  id: "case_synthetic",
  type: "warranty",
  template_id: "warranty_claim",
  template_version: 1,
  title: "合成保修案件",
  goal: "整理材料",
  status: "intake",
  priority: 0,
  version: 1,
  is_today: true,
  next_action: null,
  deadline_risk: "not_calculated",
  missing_evidence: { required_claims: [], evidence_requirements: [], total: 0 },
  last_activity_at: "2026-08-13T00:00:00Z",
  last_activity_kind: "case_updated",
  created_at: "2026-08-13T00:00:00Z",
  updated_at: "2026-08-13T00:00:00Z",
  closed_at: null,
};

describe("Case docket presentation", () => {
  it("keeps the full docket visible before selection", () => {
    const markup = renderToStaticMarkup(
      <CaseDocket mode="full" state="ready" cases={[item]} onOpen={() => undefined} onRefresh={() => undefined} onCreate={() => undefined} />,
    );

    expect(markup).toContain("今日案件台账");
    expect(markup).toContain("合成保修案件");
    expect(markup).not.toContain("切换案件（1）");
  });

  it("keeps the selected-Case docket closed behind one switcher", () => {
    const markup = renderToStaticMarkup(
      <CaseDocket mode="switcher" state="ready" cases={[item]} onOpen={() => undefined} onRefresh={() => undefined} onCreate={() => undefined} />,
    );

    expect(markup).toMatch(/<details class="case-switcher">/);
    expect(markup).toContain("切换案件（1）");
    expect(markup).not.toMatch(/<details class="case-switcher" open/);
  });

  it("directs an empty docket to the Case creation region", () => {
    const markup = renderToStaticMarkup(
      <CaseDocket mode="full" state="ready" cases={[]} onOpen={() => undefined} onRefresh={() => undefined} onCreate={() => undefined} />,
    );

    expect(markup).toContain("在上方建立第一份本地案件");
    expect(markup).not.toContain("从下面建立");
  });
});
