import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AnswerCitations, CitationCard } from "./AnswerCitations";

describe("readable answer citations", () => {
  it("shows the real filename and a collapsed, highlighted source passage", () => {
    const markup = renderToStaticMarkup(<CitationCard index={0} unavailable={false} source={{
      sourceName: "海星 H8 保修说明.pdf", page: 2, sequence: 3,
      before: "购买凭证🧾\n", quote: "Warranty 保修期 24 个月", after: "\n请保留票据。",
    }} />);
    expect(markup).toContain("海星 H8 保修说明.pdf");
    expect(markup).toContain("第 2 页 · 第 4 段");
    expect(markup).toContain("查看引用原文");
    expect(markup).toContain("<mark>Warranty 保修期 24 个月</mark>");
    expect(markup).toContain("购买凭证🧾");
    expect(markup).not.toMatch(/<details[^>]*open/);
  });

  it("escapes imported filenames and text rather than interpreting markup", () => {
    const markup = renderToStaticMarkup(<CitationCard index={0} unavailable={false} source={{
      sourceName: "<img src=x onerror=alert(1)>.txt", page: null, sequence: 0,
      before: "", quote: "<script>synthetic</script>", after: "",
    }} />);
    expect(markup).not.toContain("<script>");
    expect(markup).not.toContain("<img");
    expect(markup).toContain("&lt;script&gt;");
    expect(markup).not.toContain("第 null 页");
  });

  it("explains a missing or changed source without offering an invented excerpt", () => {
    const markup = renderToStaticMarkup(<CitationCard index={1} source={null} unavailable />);
    expect(markup).toContain("此引用原文当前无法定位");
    expect(markup).not.toContain("查看引用原文");
    expect(markup).not.toContain("<mark>");
  });

  it("keeps an unknown answer explicitly empty", () => {
    const markup = renderToStaticMarkup(<AnswerCitations caseId="case_synthetic" citations={[]} />);
    expect(markup).toContain("0 条引用");
    expect(markup).toContain("未命中可支持当前回答的证据");
    expect(markup).not.toContain("正在读取");
  });
});
