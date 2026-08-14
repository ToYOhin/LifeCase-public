import { describe, expect, it } from "vitest";

import { getTimeline } from "./api";
import {
  eventStatus,
  shortAuthority,
  timelineKindLabel,
  timelineOriginLabel,
  timelineView,
} from "./presenters";
import type { TimelineEvent, TimelineResult } from "./types";

const event: TimelineEvent = {
  event_id: "case_transitioned:transition_synthetic",
  case_id: "case_synthetic",
  kind: "case_transitioned",
  occurred_at: "2026-08-02T01:00:00.000000Z",
  authority_type: "case_transition",
  authority_id: "transition_synthetic_long_reference",
  subject_id: "case_synthetic",
  parent_id: null,
  origin: "recorded_actor",
  actor: "reviewer",
  label: null,
  status: "needs_review",
  prior_status: "intake",
  version: 2,
  reason: "Synthetic transition",
};

const timeline: TimelineResult = {
  case_id: "case_synthetic",
  events: [event],
  availability: {
    approvals: "available",
    communications: "available_as_action_projection",
  },
};

describe("Timeline API", () => {
  it("loads the Case-scoped route", async () => {
    const calls: string[] = [];
    const result = await getTimeline("case synthetic", async (input) => {
      calls.push(String(input));
      return new Response(JSON.stringify(timeline), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    expect(calls).toEqual(["/api/v1/cases/case%20synthetic/timeline"]);
    expect(result).toEqual(timeline);
  });

  it("maps network and safe server failures", async () => {
    await expect(
      getTimeline("case", async () => {
        throw new TypeError("private network detail");
      }),
    ).rejects.toMatchObject({ code: "network_error", retryable: true });

    await expect(
      getTimeline("case", async () =>
        new Response(
          JSON.stringify({
            error: { code: "timeline_unavailable", message: "Unavailable", retryable: true },
          }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
      ),
    ).rejects.toMatchObject({ code: "timeline_unavailable", retryable: true });
  });
});

describe("Timeline presentation", () => {
  it("maps all recovery states", () => {
    expect(timelineView(null, "idle", 0)).toBe("unselected");
    expect(timelineView("case", "loading", 0)).toBe("loading");
    expect(timelineView("case", "ready", 0)).toBe("empty");
    expect(timelineView("case", "offline", 0)).toBe("offline");
    expect(timelineView("case", "error", 0)).toBe("failure");
    expect(timelineView("case", "ready", 1)).toBe("populated");
  });

  it("uses human labels without hiding authority", () => {
    expect(timelineKindLabel(event.kind)).toBe("案件状态变化");
    expect(timelineOriginLabel(event.origin)).toBe("已记录操作者");
    expect(eventStatus(event)).toBe("intake → needs_review");
    expect(shortAuthority(event.authority_id)).toBe("transiti…erence");
  });
});
