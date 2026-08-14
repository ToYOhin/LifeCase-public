import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CaseWorkflow } from "./CaseWorkflow";

describe("Case workflow", () => {
  it("renders the five progressive areas but mounts only the active area", () => {
    const markup = renderToStaticMarkup(
      <CaseWorkflow
        caseTitle="合成保修案件"
        caseStatus="待复核"
        caseStatusCode="needs_review"
        nextStepTitle="检查事实与材料缺口"
        nextStepDetail="确认提取状态并复核事实。"
        primaryActionLabel="进入事实复核"
        onPrimaryAction={() => undefined}
        onReturn={() => undefined}
        activeArea="review"
        onSelect={() => undefined}
        overview={<p>overview sentinel</p>}
        agent={<p>agent sentinel</p>}
        review={<p>review sentinel</p>}
        actions={<p>actions sentinel</p>}
        records={<p>records sentinel</p>}
      />,
    );

    expect(markup).toContain("案件概览");
    expect(markup).toContain("Agent 分析");
    expect(markup).toContain("事实复核");
    expect(markup).toContain("行动与截止");
    expect(markup).toContain("记录与导出");
    expect(markup).toContain("review sentinel");
    expect(markup).not.toContain("overview sentinel");
    expect(markup).not.toContain("agent sentinel");
    expect(markup).not.toContain("actions sentinel");
    expect(markup).not.toContain("records sentinel");
    expect(markup).toMatch(
      /aria-selected="true"[^>]*>.*?<strong>事实复核<\/strong>/,
    );
    expect(markup.match(/合成保修案件/g)).toHaveLength(1);
    expect(markup).toContain("进入事实复核");
    expect(markup).not.toContain("按当前任务打开案卷");
    expect(markup).toContain("workflow-area-select");
  });
});
