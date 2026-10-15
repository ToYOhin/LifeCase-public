import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ActionPanel } from "../actions/ActionPanel";
import { DeadlinePanel } from "../deadlines/DeadlinePanel";
import { EvidenceReviewPanel } from "../review/EvidenceReviewPanel";

describe("workflow panel density", () => {
  it("keeps manual fact registration closed until requested", () => {
    const markup = renderToStaticMarkup(
      <EvidenceReviewPanel caseId="case_synthetic" caseTitle={null} claimFields={[]} refreshKey={0} onReviewChanged={async () => undefined} />,
    );
    expect(markup).toContain("登记候选事实");
    expect(markup).not.toContain("原文引用（须与下方分段完全一致）");
  });

  it("keeps date registration closed until requested", () => {
    const markup = renderToStaticMarkup(
      <DeadlinePanel caseId="case_synthetic" caseTitle={null} refreshKey={0} onChanged={async () => undefined} />,
    );
    expect(markup).toContain("登记跟进日期");
    expect(markup).not.toContain("为什么要在这一天跟进");
  });

  it("keeps Action registration closed until requested", () => {
    const markup = renderToStaticMarkup(
      <ActionPanel caseId="case_synthetic" caseTitle={null} caseType="warranty" requiredClaimFields={null}
        refreshKey={0} onChanged={async () => undefined} sources={[]} evidenceFields={[]} />,
    );
    expect(markup).toContain("登记本地行动");
    expect(markup).not.toContain("模板没有可选材料项");
  });
});
