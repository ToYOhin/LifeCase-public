import { requirementLabel } from "../workbench/presenters";
import type { JsonValue, ReviewLoadState, ReviewView } from "./types";

export type ReviewShortcut = "confirm" | "correct" | "reject" | "next";

export function reviewView(
  caseId: string | null,
  state: ReviewLoadState,
  count: number,
): ReviewView {
  if (!caseId) return "unselected";
  if (state === "loading" || state === "idle") return "loading";
  if (state === "offline") return "offline";
  if (state === "error") return "failure";
  return count === 0 ? "empty" : "populated";
}

export function fieldLabel(field: string): string {
  return requirementLabel(field);
}

export function claimValueLabel(value: JsonValue): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export function claimValueJson(value: JsonValue): string {
  return JSON.stringify(value, null, 2);
}

export function parseCorrectionValue(value: string): JsonValue {
  return JSON.parse(value) as JsonValue;
}

export function confidenceLabel(value: number | null): string {
  return value === null ? "置信度未提供" : `置信度 ${Math.round(value * 100)}%`;
}

export function reviewShortcut(
  key: string,
  altKey: boolean,
): ReviewShortcut | null {
  if (!altKey) return null;
  if (key === "1") return "confirm";
  if (key === "2") return "correct";
  if (key === "3") return "reject";
  if (key === "ArrowDown") return "next";
  return null;
}

export function nextReviewIndex(current: number, count: number): number {
  if (count <= 0) return 0;
  return Math.min(current + 1, count - 1);
}
