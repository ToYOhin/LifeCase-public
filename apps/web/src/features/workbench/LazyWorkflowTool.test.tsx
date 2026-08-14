import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LazyWorkflowTool } from "./LazyWorkflowTool";

describe("Lazy workflow tool", () => {
  it("does not mount the tool body until the drawer is opened", () => {
    const closed = renderToStaticMarkup(
      <LazyWorkflowTool title="证据包" hint="按需恢复">
        <p>pack sentinel</p>
      </LazyWorkflowTool>,
    );
    const open = renderToStaticMarkup(
      <LazyWorkflowTool title="证据包" hint="按需恢复" open>
        <p>pack sentinel</p>
      </LazyWorkflowTool>,
    );

    expect(closed).toContain("证据包");
    expect(closed).not.toContain("pack sentinel");
    expect(open).toContain("pack sentinel");
  });
});
