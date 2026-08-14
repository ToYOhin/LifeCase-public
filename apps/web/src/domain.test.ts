import { describe, expect, it } from "vitest";

import { casePhases } from "./domain";

describe("case phases", () => {
  it("keeps the evidence-backed workflow in the intended order", () => {
    expect(casePhases).toEqual([
      "材料导入",
      "事实确认",
      "缺件诊断",
      "行动审批",
      "等待结果",
      "证据归档",
    ]);
  });
});
