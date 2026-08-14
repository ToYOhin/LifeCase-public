import { describe, expect, it } from "vitest";

import {
  calendarExportUrl,
  changeDeadlineStatus,
  createSuggestedDeadline,
  getDeadlines,
} from "./api";
import {
  deadlineDateKindLabel,
  deadlineDueLabel,
  deadlineKindLabel,
  deadlineStatusLabel,
  deadlineUncertaintyLabel,
  deadlineView,
} from "./presenters";
import type { DeadlineRecord } from "./types";

const explicit: DeadlineRecord = {
  id: "deadline_explicit",
  case_id: "case_synthetic",
  kind: "explicit",
  date_kind: "local_date",
  due_value: "2026-09-01",
  timezone: "Asia/Shanghai",
  status: "confirmed",
  uncertainty: "evidence_backed_date",
  confidence: 0.95,
  rule: null,
  bases: [{
    claim_id: "claim_policy_date",
    claim_version: 2,
    value_sha256: "0".repeat(64),
    field: "policy_deadline",
    date_kind: "local_date",
  }],
  version: 2,
  definition_sha256: "1".repeat(64),
  created_at: "2026-08-03T00:00:00.000000Z",
  updated_at: "2026-08-03T00:01:00.000000Z",
};

const calculated: DeadlineRecord = {
  ...explicit,
  id: "deadline_calculated",
  kind: "calculated",
  status: "proposed",
  uncertainty: "deterministic_rule_result_requires_confirmation",
  rule: {
    id: "calendar_days_after_basis",
    version: 1,
    parameters: { days: 30 },
    parameters_sha256: "2".repeat(64),
  },
};

const suggested: DeadlineRecord = {
  ...explicit,
  id: "deadline_suggested",
  kind: "suggested",
  status: "proposed",
  uncertainty: "personal_follow_up_preference_not_external_obligation",
  confidence: null,
  bases: [],
};

describe("Deadline API", () => {
  it("loads the encoded Case-scoped route and all three kinds", async () => {
    const calls: string[] = [];
    const result = await getDeadlines("case synthetic", async (input) => {
      calls.push(String(input));
      return new Response(JSON.stringify([explicit, calculated, suggested]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    expect(calls).toEqual(["/api/v1/cases/case%20synthetic/deadlines"]);
    expect(result.map((item) => item.kind)).toEqual(["explicit", "calculated", "suggested"]);
  });

  it("distinguishes offline and safe server failures", async () => {
    await expect(getDeadlines("case", async () => { throw new TypeError("offline"); }))
      .rejects.toMatchObject({ code: "network_error", retryable: true });
    await expect(getDeadlines("case", async () => new Response(JSON.stringify({
      error: { code: "deadline_storage_failed", message: "Unavailable", retryable: true },
    }), { status: 503, headers: { "Content-Type": "application/json" } })))
      .rejects.toMatchObject({ code: "deadline_storage_failed", retryable: true });
  });

  it("builds a Case-scoped deterministic ICS download route", () => {
    expect(calendarExportUrl("case synthetic", "deadline/synthetic")).toBe(
      "/api/v1/cases/case%20synthetic/deadlines/deadline%2Fsynthetic/calendar-export",
    );
  });

  it("creates and confirms a personal follow-up date through explicit commands", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const request = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: JSON.parse(String(init?.body)) });
      return new Response(JSON.stringify(suggested), {
        status: String(input).endsWith("suggested") ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    await createSuggestedDeadline("case synthetic", {
      date_kind: "local_date",
      due_value: "2026-09-01",
      timezone: "Asia/Shanghai",
      actor: "local_user",
      reason: "Personal follow-up preference",
    }, request);
    await changeDeadlineStatus(suggested.id, "confirm", {
      expected_version: 1,
      actor: "local_user",
      reason: "Confirmed displayed date",
    }, request);
    expect(calls.map((item) => item.url)).toEqual([
      "/api/v1/cases/case%20synthetic/deadlines/suggested",
      "/api/v1/deadlines/deadline_suggested/confirm",
    ]);
    expect(calls[0].body).toMatchObject({ date_kind: "local_date" });
  });
});

describe("Deadline presentation", () => {
  it("maps loading, empty, offline, failure, normal, and unselected states", () => {
    expect(deadlineView(null, "idle", 0)).toBe("unselected");
    expect(deadlineView("case", "loading", 0)).toBe("loading");
    expect(deadlineView("case", "ready", 0)).toBe("empty");
    expect(deadlineView("case", "offline", 0)).toBe("offline");
    expect(deadlineView("case", "error", 0)).toBe("failure");
    expect(deadlineView("case", "ready", 3)).toBe("populated");
  });

  it("keeps kind, date kind, status, and uncertainty visibly distinct", () => {
    expect(deadlineKindLabel("explicit")).toBe("证据明确日期");
    expect(deadlineKindLabel("calculated")).toBe("规则计算日期");
    expect(deadlineKindLabel("suggested")).toBe("个人跟进偏好");
    expect(deadlineDateKindLabel("local_date")).toBe("本地日期");
    expect(deadlineStatusLabel("confirmed")).toBe("已确认");
    expect(deadlineDueLabel(explicit)).toBe("2026-09-01");
    expect(deadlineUncertaintyLabel(suggested.uncertainty)).toContain("不是外部义务");
  });
});
