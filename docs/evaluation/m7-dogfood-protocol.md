# M7 Private Dogfood Protocol

Status: **OPTIONAL / NOT RUN** as of 2026-08-09.

This protocol is optional operational validation, not an automated test and not a Portfolio MVP gate. It starts only when a person deliberately begins using the Windows daily-use entry for real personal Case work. Repository development, synthetic fixtures, the short package acceptance, and elapsed calendar time before that start do not count.

## Completion gate

All conditions are required:

- at least seven elapsed 24-hour periods between the recorded local start and end dates;
- at least three distinct real Cases reach the user's intended completion outcome;
- daily-use setup/start/stop/restart and recovery are used without bypassing the package for Case work;
- no material privacy, data-loss, provenance, approval, export, Backup, or Restore defect remains unresolved;
- every limitation and aborted Case is counted; records are not removed to improve the result.

Before an operational trial is adopted, the result remains `NOT_RUN`. After it starts, fewer than seven elapsed days is `TIME_BLOCKED`; a completed trial that misses a required condition is `FAIL`, never `PASS`.

## Private procedure

1. Select a new private data directory through `scripts/lifecase.ps1 setup`; keep it outside the repository.
2. Before substantive changes, read the Backup reminder and explicitly decide whether to create a Backup. The application does not create one automatically.
3. For each real Case, import only material the user is authorized to process. Review provenance, confirm or correct Claims, inspect Deadline/Action/Approval/Timeline state, and use exports only when actually useful.
4. Stop and restart the daily-use entry during the period. Confirm the expected Case state and retained Backup remain readable.
5. Record defects locally while working, but do not copy source material, Evidence Packs, Backups, screenshots, detailed logs, object paths, database files, or personal identifiers into Git.
6. At the end, publish only the aggregate record below. Separately re-run deterministic repository verification and the unchanged M7 corpus/report checks.

OCR-required input may be replaced with a user-authorized text source, and disabled/unavailable model state must remain explicit. Neither capability limitation blocks deterministic Case reads, ICS/Evidence Pack exports, or Backup. The dogfood period must not enable live models, external services, connectors, telemetry, or automatic Backup activity.

## Aggregate-only result record

Only the following fields may enter the repository:

```yaml
protocol_version: m7-private-dogfood-v1
status: NOT_RUN | TIME_BLOCKED | FAIL | PASS
start_date: YYYY-MM-DD
end_date: YYYY-MM-DD
elapsed_days: 0
real_cases_started: 0
real_cases_completed: 0
package_start_count: 0
package_restart_count: 0
explicit_backups_created: 0
restore_validations_completed: 0
severity_counts:
  data_loss: 0
  privacy_or_scope: 0
  provenance_or_hash: 0
  approval_or_external_action: 0
  startup_or_recovery: 0
  usability_other: 0
release_blockers_open: 0
notes: "One non-sensitive aggregate sentence; no Case topic or local path."
```

Dates are date-only. Counts must include failed and abandoned attempts where applicable. `notes` must not identify a person, organization, product dispute, account, source, file, location, or remote service. Detailed observations remain private and are deleted or retained solely by the user under their own policy.

## Independent gates

A future dogfood `PASS` does not change the fixed M7 evaluation result. The product-processing gate remains at least 27/30 formally supported Cases; the reviewed subscription-cancellation slice records 27/30 without changing the evaluator threshold or corpus distribution. `NOT_RUN` means only that sustained real use is unverified. Do not alter templates, evaluator rules, threshold, or corpus distribution through this protocol.
