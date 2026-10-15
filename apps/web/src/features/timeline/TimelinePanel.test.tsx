import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TimelineItem, TimelinePanel } from "./TimelinePanel";
import type { TimelineEvent } from "./types";

const event: TimelineEvent = {
  event_id: "case_transitioned:transition_synthetic",
  case_id: "case_synthetic",
  kind: "case_transitioned",
  occurred_at: "2026-09-21T01:00:00.000000Z",
  authority_type: "case_transition",
  authority_id: "transition_synthetic_long_reference",
  subject_id: "case_synthetic_long_reference",
  parent_id: "parent_synthetic_long_reference",
  origin: "recorded_actor",
  actor: "合成核实人",
  label: "已整理报修材料",
  status: "needs_review",
  prior_status: "intake",
  version: 2,
  reason: "等待人工核对保修日期",
};
const outsideDetails = (markup: string) => markup.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g, "");

describe("案件记录简洁呈现", () => {
  it("names the record view without an English engineering heading", () => {
    const markup = renderToStaticMarkup(<TimelinePanel caseId={null} caseTitle={null} refreshKey={0} />);
    expect(markup).toContain('<h2 id="timeline-title">案件记录</h2>');
    expect(markup).not.toContain("CASE CHRONICLE");
    expect(markup).not.toContain("SQLite");
  });

  it("keeps time, human decision and status visible while folding complete internal references", () => {
    const markup = renderToStaticMarkup(<TimelineItem event={event} index={0} />);
    const reference = markup.match(/<details\b([^>]*)>([\s\S]*?)<\/details>/);
    expect(reference).not.toBeNull();
    expect(reference?.[1]).not.toMatch(/\bopen(?:\s|=|$)/);
    for (const value of [event.authority_id, event.subject_id, event.parent_id]) {
      expect(reference?.[2]).toContain(value);
      expect(outsideDetails(markup)).not.toContain(value);
    }
    const visible = outsideDetails(markup);
    expect(visible).toContain(`dateTime="${event.occurred_at}"`);
    expect(visible).toContain("案件状态变化");
    expect(visible).toContain("已整理报修材料");
    expect(visible).toContain("待整理 → 待复核");
    expect(visible).toContain("合成核实人");
    expect(visible).toContain("等待人工核对保修日期");
    expect(visible).toContain("版本 2");
    expect(visible.replace(/<[^>]*>/g, "")).not.toContain("case_transition");
  });

  it("retains separate historical events in supplied order without turning a result note into completion", () => {
    const progress: TimelineEvent = {
      ...event, event_id: "action_progress:synthetic", kind: "action_progress_recorded",
      authority_type: "action_progress", authority_id: "progress_synthetic_long_reference",
      label: "result", status: null, prior_status: null, reason: "仅记录售后回复，尚待核实结果",
    };
    const markup = renderToStaticMarkup(<ol>
      <TimelineItem event={event} index={0} />
      <TimelineItem event={progress} index={1} />
    </ol>);
    const visible = outsideDetails(markup);
    expect(markup.match(/<li\b/g)).toHaveLength(2);
    expect(visible.indexOf("案件状态变化")).toBeLessThan(visible.indexOf("人工处理记录已保存"));
    expect(visible).toContain("人工结果备注（不代表待办完成）");
    expect(visible).toContain("仅记录售后回复，尚待核实结果");
    expect(visible).toContain("<span>01</span>");
    expect(visible).toContain("<span>02</span>");
  });
});
