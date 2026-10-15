import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { WarrantyPreparationPanel } from "./WarrantyPreparationPanel";

describe("报修准备单入口", () => {
  it("starts with explicit empty intent and no stale preview or runnable copy", () => {
    const markup = renderToStaticMarkup(<WarrantyPreparationPanel caseId="case_a" refreshKey={0} />);
    expect(markup).toContain('aria-label="中文报修准备单"');
    expect(markup).toContain("待本人核对，尚未发送");
    expect(markup).toContain("本人诉求");
    expect(markup).toContain("保存诉求");
    expect(markup).toContain("仅保存本案件诉求；未保存的修改会在离开后丢失");
    expect(markup).not.toContain("离开或刷新后需重新填写");
    expect(markup).toContain("复制准备单");
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>保存本地 TXT<\/button>/);
    expect(markup).toContain("disabled");
    expect(markup).not.toContain('aria-label="准备单预览"');
  });
});
