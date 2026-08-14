import { describe, expect, it } from "vitest";

import { applyRestore, createBackup, getLatestBackup, validateBackup } from "./api";
import { backupRestoreView, backupSizeLabel, shortBackupHash } from "./presenters";
import type { BackupArtifact, BackupRestoreState, RestoreValidation } from "./types";

const artifact: BackupArtifact = {
  id: "0".repeat(64),
  filename: "lifecase-backup-0000000000000000.zip",
  sha256: "0".repeat(64),
  size_bytes: 4096,
  format_version: 1,
  minimum_application_version: "0.1.0",
  case_count: 1,
  member_count: 5,
  created_at: "2026-08-03T00:00:00.000000Z",
  download_url: `/api/v1/backups/${"0".repeat(64)}`,
};

const validation: RestoreValidation = {
  validation_id: "0".repeat(64),
  backup_sha256: "0".repeat(64),
  format_version: 1,
  minimum_application_version: "0.1.0",
  case_count: 1,
  member_count: 5,
  database_sha256: "1".repeat(64),
};

describe("Backup and restore API", () => {
  it("loads, creates, uploads for validation, and sends explicit apply confirmation", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const request = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([String(input), init]);
      const payload = String(input).endsWith("/validate")
        ? validation
        : String(input).endsWith("/apply")
          ? { validation_id: validation.validation_id, runtime_id: "copy", case_count: 1, migrated: false }
          : artifact;
      return new Response(JSON.stringify(payload), {
        status: init?.method === "POST" && String(input) === "/api/v1/backups" ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    expect(await getLatestBackup(request)).toEqual(artifact);
    expect(await createBackup(request)).toEqual(artifact);
    expect(await validateBackup(new File(["synthetic"], "backup.zip"), request)).toEqual(validation);
    expect(await applyRestore(validation.validation_id, "copy", request)).toMatchObject({ runtime_id: "copy" });
    expect(calls.map(([path]) => path)).toEqual([
      "/api/v1/backups",
      "/api/v1/backups",
      "/api/v1/restores/validate",
      "/api/v1/restores/apply",
    ]);
    expect(calls[2][1]?.body).toBeInstanceOf(FormData);
    expect(JSON.parse(String(calls[3][1]?.body))).toEqual({
      validation_id: validation.validation_id,
      target_name: "copy",
      confirmation: "RESTORE INTO NEW DIRECTORY",
    });
  });

  it("distinguishes empty, offline, and typed failures", async () => {
    const missing = async () => new Response(JSON.stringify({
      error: { code: "backup_not_found", message: "missing", retryable: false },
    }), { status: 404, headers: { "Content-Type": "application/json" } });
    expect(await getLatestBackup(missing)).toBeNull();
    await expect(createBackup(async () => { throw new TypeError("offline"); }))
      .rejects.toMatchObject({ code: "network_error", retryable: true });
    await expect(validateBackup(new File(["bad"], "bad.zip"), async () =>
      new Response(JSON.stringify({
        error: { code: "backup_integrity_failed", message: "invalid", retryable: false },
      }), { status: 409, headers: { "Content-Type": "application/json" } })))
      .rejects.toMatchObject({ code: "backup_integrity_failed" });
  });
});

describe("Backup and restore presentation", () => {
  it("covers loading, empty, selection, validation, confirmation, restore, success, offline and failure", () => {
    const states: BackupRestoreState[] = [
      "loading", "empty", "creating", "ready", "selected", "validating",
      "needs_confirmation", "restoring", "success", "offline", "error",
    ];
    expect(states.map(backupRestoreView)).toEqual(states);
    expect(backupSizeLabel(4096)).toBe("4.0 KiB");
    expect(shortBackupHash("0".repeat(64))).toBe("0000000000…00000000");
  });
});
