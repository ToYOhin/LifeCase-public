import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CaseAgentWorkspace, FakeDemoDrawer } from "./CaseAgentWorkspace";

describe("Case Agent workspace", () => {
  it("keeps global AI settings out of the case-scoped Agent workspace", () => {
    const markup = renderToStaticMarkup(
      <CaseAgentWorkspace
        caseId="case_synthetic"
        caseTitle="合成保修案件"
        caseType="warranty"
        refreshKey={0}
        onChanged={async () => undefined}
      />,
    );

    expect(markup).not.toContain("AI 设置");
    expect(markup).toContain("只读证据分析");
    expect(markup).toContain("离线开发演示");
    expect(markup.indexOf("只读证据分析")).toBeLessThan(markup.indexOf("离线开发演示"));
    expect(markup).not.toContain("证据 Agent 演示");
    expect(markup).toMatch(/<details class="fake-demo-drawer">/);
    expect(markup).not.toMatch(/<details class="fake-demo-drawer" open/);
  });

  it("mounts Fake Demo content only while its disclosure is open", () => {
    const closed = renderToStaticMarkup(
      <FakeDemoDrawer open={false} onToggle={() => undefined}>
        <p>fake demo sentinel</p>
      </FakeDemoDrawer>,
    );
    const open = renderToStaticMarkup(
      <FakeDemoDrawer open onToggle={() => undefined}>
        <p>fake demo sentinel</p>
      </FakeDemoDrawer>,
    );

    expect(closed).not.toContain("fake demo sentinel");
    expect(closed).not.toContain("Fake Provider");
    expect(open).toContain("fake demo sentinel");
    expect(open).toContain("Fake Provider");
  });
});
