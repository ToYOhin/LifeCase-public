import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  App,
  CASE_START_SELECTOR,
  CASE_WORKFLOW_SELECTOR,
  caseTransitionTargets,
  workflowNextStep,
} from "./App";
import type { CaseRecord } from "./features/intake/types";

const record: CaseRecord = {
  id: "case_synthetic",
  type: "warranty",
  title: "合成保修案件",
  goal: "整理材料",
  status: "intake",
  priority: 0,
  template_id: "warranty_claim",
  template_version: 1,
  version: 1,
  created_at: "2026-08-04T00:00:00Z",
  updated_at: "2026-08-04T00:00:00Z",
  closed_at: null,
  sources: [],
};

describe("daily-use workflow guidance", () => {
  it("shows only Case creation and the Case docket before a Case is selected", () => {
    const markup = renderToStaticMarkup(createElement(App));

    expect(markup).toContain("建立案件");
    expect(markup).toContain("今日案件台账");
    expect(markup).toContain("AI 设置");
    expect(markup).not.toContain("正在读取本机 AI 设置");
    expect(markup).not.toContain("案件 Agent 工作区");
    expect(markup).not.toContain("逐项核对事实与原始分段");
    expect(markup).not.toContain("截止日期与个人提醒");
    expect(markup).not.toContain("案件进展与恢复记录");
  });

  it("starts with Case creation and then advances through intake and review", () => {
    expect(CASE_START_SELECTOR).toBe(".case-start-panel");
    expect(CASE_WORKFLOW_SELECTOR).toBe(".case-workflow");
    expect(workflowNextStep(null)).toMatchObject({
      number: "01",
      target: ".case-start-panel",
      area: null,
      actionLabel: "建立案件",
    });
    expect(workflowNextStep(record)).toMatchObject({
      number: "02",
      target: ".case-workflow",
      area: "overview",
      actionLabel: "选择材料",
    });
    expect(
      workflowNextStep({
        ...record,
        sources: [
          {
            id: "source_synthetic",
            case_id: "case_synthetic",
            original_name: "receipt.txt",
            media_type: "text/plain",
            size_bytes: 12,
            sha256: "b".repeat(64),
            trust_label: "untrusted",
            imported_at: "2026-08-04T00:00:00Z",
            extraction: {
              status: "completed",
              extractor_version: "text.v1",
              segment_count: 1,
              notices: [],
              error_code: null,
            },
          },
        ],
      }),
    ).toMatchObject({
      number: "03",
      target: ".case-workflow",
      area: "review",
      actionLabel: "进入事实复核",
    });
  });

  it("exposes only the domain-approved next Case states", () => {
    expect(caseTransitionTargets("intake")).toEqual([
      "needs_review",
      "ready",
      "abandoned",
    ]);
    expect(caseTransitionTargets("resolved")).toEqual(["archived"]);
    expect(caseTransitionTargets("archived")).toEqual([]);
  });
});
