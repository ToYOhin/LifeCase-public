# Web features

Feature folders own their transport types, API clients, presentation helpers, components, and focused Vitest coverage.

Implemented:

- `intake/`: Case creation, explicit file import, upload progress, and persisted Source recovery;
- `workbench/`: responsive Today/Case list and authoritative missing-evidence summary;
- `review/`: Case-scoped Evidence Review with confirm/correct/reject keyboard flow;
- `deadlines/`: Case-scoped Deadline authority with distinct kind/date/status/uncertainty presentation;
- `timeline/`: deterministic authority Timeline and refresh/restart recovery.

M5 Action/Approval UI lives in `actions/` only after its domain/API authority, and confirmed-only ICS download lives with `deadlines/`. Future M6 Evidence Pack UI must likewise be added only after its domain/API authority exists; browser-only state is never durable authority.
