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
import type { CaseRecord, CaseStatus } from "./features/intake/types";

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

const source: CaseRecord["sources"][number] = {
  id: "source_synthetic",
  case_id: record.id,
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
};

const lifecycleGuidance = [
  ["needs_review", "检查事实与材料缺口", "review", "进入事实复核"],
  ["ready", "安排后续行动", "actions", "查看行动与截止"],
  ["in_progress", "继续处理已有行动", "actions", "查看行动与截止"],
  ["waiting_external", "跟进外部回复", "actions", "查看跟进与截止"],
  ["blocked", "检查阻塞原因", "records", "查看阻塞记录"],
  ["resolved", "核对处理结果", "records", "查看结案记录"],
  ["abandoned", "查看放弃记录", "records", "查看结案记录"],
  ["archived", "查阅归档记录", "records", "查看归档记录"],
] satisfies Array<[CaseStatus, string, string, string]>;

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

  it.each(lifecycleGuidance)(
    "uses %s lifecycle guidance regardless of imported material count",
    (status, title, area, actionLabel) => {
      for (const sources of [[], [source]]) {
        expect(workflowNextStep({ ...record, status, sources })).toMatchObject({
          title,
          area,
          actionLabel,
          target: CASE_WORKFLOW_SELECTOR,
        });
      }
    },
  );

  it.each(["resolved", "abandoned", "archived"] as const)(
    "never sends a closed %s Case back to intake or fact review",
    (status) => {
      for (const sources of [[], [source]]) {
        const nextStep = workflowNextStep({ ...record, status, sources });

        expect(nextStep.area).toBe("records");
        expect(nextStep.actionLabel).not.toMatch(/选择材料|事实复核/);
        expect(nextStep.detail).not.toMatch(/导入第一份|先确认提取状态/);
      }
      expect(caseTransitionTargets(status)).toEqual(
        status === "archived" ? [] : ["archived"],
      );
    },
  );

  it("recomputes guidance from the restored lifecycle status without changing Case data", () => {
    const before = { ...record, status: "in_progress" as const, sources: [source] };
    const after = { ...before, status: "resolved" as const, version: 2 };
    const snapshot = JSON.stringify([before, after]);

    expect(workflowNextStep(before).area).toBe("actions");
    expect(workflowNextStep(after).area).toBe("records");
    expect(JSON.stringify([before, after])).toBe(snapshot);
  });
});
