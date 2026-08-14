import type {
  EvidencePackLoadState,
  EvidencePackView,
} from "./types";

export function evidencePackView(
  caseId: string | null,
  state: EvidencePackLoadState,
): EvidencePackView {
  if (!caseId) return "unselected";
  if (state === "idle" || state === "loading") return "loading";
  if (state === "empty") return "empty";
  if (state === "creating") return "creating";
  if (state === "ready") return "ready";
  if (state === "offline") return "offline";
  return "failure";
}

export function shortPackHash(value: string): string {
  return value.length > 20 ? `${value.slice(0, 10)}…${value.slice(-8)}` : value;
}

export function packSizeLabel(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  return `${(sizeBytes / 1024).toFixed(1)} KiB`;
}
