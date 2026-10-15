# Web features

Feature folders own their transport types, API clients, presentation helpers, components, and focused Vitest coverage.

Implemented:

- `intake/`: Case creation, explicit file import, upload progress, and persisted Source recovery;
- `workbench/`: responsive Today/Case list and authoritative missing-evidence summary;
- `review/`: Case-scoped Evidence Review with confirm/correct/reject keyboard flow;
- `warrantyPreparation/`: current-facts projection, session-only explicit intent, Chinese preview/copy and source location; no business writes or model calls;
- `agentDemo/`: separate deterministic demo and consent-bound Live result/history/citation views;
- `aiCapabilities/`: global fixed-model settings and secret-free capability projection;
- `templates/`: reviewed template catalog and explicit selection;
- `deadlines/`: Case-scoped Deadline authority with distinct kind/date/status/uncertainty presentation;
- `timeline/`: deterministic authority Timeline and refresh/restart recovery.

`actions/` implements Action/Approval views; `evidencePack/` and `backupRestore/` implement local exports and recovery. Confirmed-only ICS download belongs to `deadlines/`. All consume existing API/domain authority; browser-only state is never durable authority. The complete current map is in [PROJECT_MAP](../../../../docs/PROJECT_MAP.md).
