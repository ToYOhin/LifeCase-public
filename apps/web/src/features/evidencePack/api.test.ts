import { describe, expect, it } from "vitest";

import { createEvidencePack, getLatestEvidencePack } from "./api";
import { evidencePackView, packSizeLabel, shortPackHash } from "./presenters";
import type { EvidencePackArtifact } from "./types";

const artifact: EvidencePackArtifact = {
  id: "art_synthetic",
  case_id: "case_synthetic",
  filename: "lifecase-evidence-pack-0123456789ab.zip",
  sha256: "0".repeat(64),
  size_bytes: 4096,
  format_version: 1,
  created_at: "2026-08-03T00:00:00.000000Z",
  download_url: "/api/v1/artifacts/art_synthetic",
};

describe("Evidence Pack API", () => {
  it("loads and creates through an encoded Case-scoped route", async () => {
    const calls: Array<[string, string]> = [];
    const request = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([String(input), init?.method ?? "GET"]);
      return new Response(JSON.stringify(artifact), {
        status: init?.method === "POST" ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    expect(await getLatestEvidencePack("case synthetic", request)).toEqual(artifact);
    expect(await createEvidencePack("case synthetic", request)).toEqual(artifact);
    expect(calls).toEqual([
      ["/api/v1/cases/case%20synthetic/evidence-pack", "GET"],
      ["/api/v1/cases/case%20synthetic/evidence-pack", "POST"],
    ]);
  });

  it("distinguishes absence, offline, and safe failures", async () => {
    const missing = async () => new Response(JSON.stringify({
      error: { code: "artifact_not_found", message: "missing", retryable: false },
    }), { status: 404, headers: { "Content-Type": "application/json" } });
    expect(await getLatestEvidencePack("case", missing)).toBeNull();
    await expect(createEvidencePack("case", async () => { throw new TypeError("offline"); }))
      .rejects.toMatchObject({ code: "network_error", retryable: true });
    await expect(createEvidencePack("case", async () => new Response(JSON.stringify({
      error: { code: "evidence_pack_integrity_failed", message: "unsafe", retryable: false },
    }), { status: 409, headers: { "Content-Type": "application/json" } })))
      .rejects.toMatchObject({ code: "evidence_pack_integrity_failed" });
  });
});

describe("Evidence Pack presentation", () => {
  it("covers unselected, loading, empty, creating, offline, failure, and ready", () => {
    expect(evidencePackView(null, "idle")).toBe("unselected");
    expect(evidencePackView("case", "loading")).toBe("loading");
    expect(evidencePackView("case", "empty")).toBe("empty");
    expect(evidencePackView("case", "creating")).toBe("creating");
    expect(evidencePackView("case", "offline")).toBe("offline");
    expect(evidencePackView("case", "error")).toBe("failure");
    expect(evidencePackView("case", "ready")).toBe("ready");
    expect(packSizeLabel(4096)).toBe("4.0 KiB");
    expect(shortPackHash("0".repeat(64))).toBe("0000000000…00000000");
  });
});
