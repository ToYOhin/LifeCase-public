# Product Requirements

This document describes product requirements, not an assertion that every desired input or workflow is implemented. Current delivery and evidence are in [STATUS](STATUS.md); priorities are in [the roadmap](DEVELOPMENT_ROADMAP.md).

The current completion scope is the common, human-controlled workflow for warranty, return/refund, reimbursement and subscription cancellation: import TXT/EML/text PDF, explicitly register, review and correct facts, prepare local Actions with exact fact-version bindings, separately approve and complete them with evidence, decide whether the Case is resolved, and retain the record. Warranty additionally provides its existing Chinese preparation sheet, cited question analysis and repair-preparation draft. The current task does not extend that AI analysis to the other templates. Screenshots/scans need future OCR and are not accepted text sources today. External sending remains unimplemented. Commercial operation, hosting, multi-user service and long-term human acceptance are not completion prerequisites for this personal project.

## Product statement

LifeCase helps one person finish real administrative tasks. It converts scattered evidence into a durable case, identifies what is missing, proposes the next safe action, tracks deadlines and external commitments, and preserves a verifiable record of the outcome.

## Primary users

The MVP serves one technically comfortable Windows user managing personal and household matters. It is not designed for customer-service teams, legal case management, medical workflows, or regulated enterprise records.

## Jobs to be done

1. When I receive a receipt, invoice, screenshot, PDF, or exported email, help me decide whether it belongs to an active case.
2. When evidence is incomplete, tell me exactly what is missing and which step it blocks.
3. When a deadline or promise appears, distinguish what is explicit from what is calculated or merely suggested.
4. When I need to contact an organization, draft a concise message using only confirmed facts.
5. Before anything leaves my computer, show the recipient, content, attachments, identity, and reversibility.
6. When the matter ends, produce a complete timeline and evidence package.

## MVP case types

### Warranty

Goal: repair, replacement, or refund.

Minimum facts: merchant or manufacturer, product, purchase date, order number, desired outcome. Conditional facts include serial number and warranty policy reference.

### Return or refund

Goal: refund, price adjustment, replacement, or documented denial.

Minimum facts: merchant, item, order number, paid amount, delivery date, reason, desired outcome.

### Reimbursement

Goal: submit an expense package and record the result.

Minimum facts: expense date, merchant, amount, currency, category, business purpose, receipt.

Subscription cancellation has a reviewed product template and deterministic product coverage. Travel/logistics remains evaluation-only; it is not a supported product workflow.

## Core capabilities

### Case creation

- Create manually with an explicit title, goal and reviewed template. Creating a Case automatically from imported material is a future requirement, not a current intake behavior.
- Require a user-visible goal.
- Record business Case type separately from actual template ID and version. The reviewed bindings are `warranty -> warranty_claim@1`, `return_refund -> return_refund_claim@1`, `reimbursement -> expense_reimbursement@1`, and `subscription_cancellation -> subscription_cancellation_claim@1`.
- Require explicit selection from the closed reviewed template catalog; neither the API nor Web client guesses a type from titles, goals, or imported content.
- Default to `intake` and never infer a resolved state.

### Evidence intake

- Import only explicitly selected sources.
- Preserve originals and compute SHA-256.
- Extract text into location-stable segments.
- Label extraction method and confidence.
- Detect duplicate bytes without hiding duplicate business events.

### Fact management

- Store candidates separately from confirmed facts.
- Link facts to one or more source segments.
- Support confirm, correct, reject, and contradiction states.
- Retain correction history.

### Gap analysis

- Evaluate versioned case-template requirements.
- Explain why evidence is required.
- Link a missing item to blocked actions.
- Allow user override with a recorded reason.

### Deadlines

- Display explicit, calculated, and suggested dates differently.
- Represent a date as either a timezone-bearing UTC instant or a calendar-only local date, always with an explicit IANA timezone and visible date kind.
- Accept an explicit date only from one current, non-contradictory, confirmed or corrected Claim in the same Case; retain its Claim ID, version, value hash, and confidence.
- Calculate a date only through a closed deterministic rule ID/version and current authoritative basis Claims; retain canonical rule parameters and their hash.
- Label a suggested date as a personal follow-up preference, never an evidence fact, external obligation, or calculated deadline.
- Retain immutable history and optimistic versioning for proposed, confirmed, passed, completed, and dismissed states.
- Keep deadline risk uncalculated until a separately reviewed risk policy exists.
- Generate an `.ics` file only from a user-confirmed date.

### Actions and approvals

M5 now implements these requirements as local Case-scoped authority. They do not authorize email, browser submission, system-calendar writes, connectors, or any other external side effect.

- Produce only the closed local Action kinds `collect_evidence`, `prepare_communication`, `follow_up`, and `record_outcome`.
- Keep the implemented local lifecycle to `proposed`, `blocked`, `ready`, `approved`, `running`, `done`, `failed`, and `cancelled`; reaching `approved` requires the existing exact-version Approval authority.
- Keep dependency edges inside one Case, reject self/cyclic/cross-Case edges, and expose one stable topological order with auditable blocker codes.
- Treat only current, non-contradictory `confirmed` or `corrected` Claims as satisfied facts. Candidate, inferred, rejected, stale, and cross-Case Claims cannot make an Action ready.
- Enforce the bound Case template's forbidden-action identifiers in the domain service.
- Link an Action due date only by Deadline ID, version, and definition hash. Project the original Deadline date kind and IANA timezone instead of copying or coercing it to `due_at`.
- Bind completion evidence to current same-Case Claim, Source, or Artifact authority and require at least one valid reference before `done`.
- Generate `prepare_communication` as a local deterministic draft from explicitly bound authoritative Claims. It is not a send operation and has no recipient connector.
- Assign A/B/C/D risk levels according to the table below. Risk D is prohibited; Risk C may be approved in M5-03 but cannot execute in the MVP.
- Bind each Approval request to the exact Action version, canonical payload hash, user-visible summary hash, actor label, and decision history.
- Treat the actor as a local audit label, not an authenticated identity. Fail closed and retain the old audit row when an Action payload changes.
- Export ICS as an immediate local download only. It is not an Artifact, system-calendar event, email attachment, or external action.

### Warranty preparation sheet

The implemented Chinese sheet is a separate read-only projection of current, usable confirmed/corrected facts and explicitly entered user intent. Intent can be explicitly saved per Case with version protection; unsaved text remains a local edit and is not silently persisted. The sheet lists materials, missing/uncertain items and confirmation/source bases. Preview/copy/TXT must not modify Actions, approvals, historical drafts, Evidence Packs or Runs. Delivery revalidates authority, is not sending, and does not mark anything done. See [the user guide](WARRANTY_PREPARATION.md).

### Question analysis and human follow-through

The warranty q1 workflow binds the independent user question, retrieval expression, current confirmed/corrected facts and Source authority to one Run. Changes make previous results explicitly stale; reanalysis creates a new Run without rewriting history. A current valid result may seed a short editable repair-preparation draft, saved only by explicit user action through existing Action/Approval contracts.

Human progress and result notes are append-only records, not fact confirmation, approval, Action completion or Case resolution. Completing a preparation Action requires its own authority and evidence; deciding whether the household matter is resolved is a separate Case transition. Saved drafts and their original analysis, citations, result records and completion evidence remain reviewable. See [API contracts](API_CONTRACT.md) and [current delivery](STATUS.md).

### Evidence Pack contents

- Export sources, derived artifacts, timeline, claims, actions, approvals, manifest, and checksums.
- Distinguish original, extracted, inferred, generated, and user-confirmed content.
- Verify integrity independently of the application.

## Risk levels

| Level | Meaning | MVP behavior |
|---|---|---|
| A | Read-only local analysis | automatic |
| B | Reversible local write | explicit initial authorization or per action |
| C | External side effect | per-action confirmation; implementation disabled in MVP |
| D | Payment, signature, destructive or legal commitment | prohibited |

## Non-functional requirements

- Local API binds to loopback by default.
- Existing cases remain readable and exportable without model access.
- A failed model call cannot corrupt source, case, or approval state.
- Import and export operations are resumable or atomic.
- Sensitive text is absent from ordinary logs.
- User data can be exported and restored without vendor lock-in.
- Evidence Pack is a Case-scoped review/export artifact; whole-authority Backup is a separate versioned recovery format built from a SQLite online snapshot and referenced retained files.
- Restore validates independently and publishes only to a new clean local runtime; it never overwrites, merges, deletes, or silently repairs existing user data.
- UI exposes uncertainty, evidence, and pending approvals clearly.
- The Portfolio Agent Demo is an explicit local capability. Its default is disabled; when enabled it uses only the versioned deterministic Fake Provider, fixed seven-stage Case pipeline, existing candidate/inferred Claim provenance, and one read-only Evidence Context tool. It is not live-model output and cannot confirm Claims or execute Actions.

## MVP success criteria

- 27 of 30 evaluation cases can be created and processed.
- At least 90% of labelled critical fields are correct or explicitly uncertain.
- 100% of factual Agent claims include valid source references.
- Zero unauthorized external actions.
- Zero inferred deadlines silently presented as explicit.
- Evidence Pack integrity passes for every completed evaluation case.
- Default model budget remains at or below CNY 1 per case.
- For an operational daily-use readiness claim, the user completes three real Cases during a seven-day private trial; this is optional and `NOT_RUN` for the current Portfolio MVP. Only non-sensitive aggregate counts/outcomes may be retained for review, while source material, runtime data, exports, screenshots, detailed logs, and local paths remain outside Git.

M7-02 measures the deterministic quality and safety gates without weakening them. Its recorded synthetic-output metrics pass. After the reviewed subscription-cancellation product slice, 27 warranty/return-refund/reimbursement/subscription fixtures map to closed product templates and the 3 travel/logistics fixtures remain evaluation-only. Product-processing coverage is therefore 27/30 at the required threshold; this does not turn recorded outputs into live-model accuracy or real-use evidence.

M7-03 packages the deterministic local product for Windows daily use. It requires a user-selected or safe-default owned data directory, one loopback-only origin serving the built Web client and API, existing migration/verified pre-migration Backup ordering, explicit offline capability state, and setup/start/stop/upgrade/uninstall behavior that retains user data. Packaging success, short synthetic acceptance, optional private dogfood, and product-processing coverage remain distinct evidence and must be labelled accurately.

## Product non-goals

- general-purpose assistant behavior;
- legal, medical, or investment advice;
- autonomous negotiation;
- multi-user collaboration;
- cloud-first storage;
- mandatory model use for deterministic functions;
- automatic source deletion;
- hidden personal-profile learning.

## Open product decisions

These choices require explicit user approval before implementation:

1. Whether Personal Policy may automatically abandon very low-value cases.
2. How long deleted cases remain recoverable.
3. Which mailbox provider, if any, is supported first.
4. Whether future email sending is ever enabled.
5. Whether sensitive-field redaction is opt-in or default-on for cloud models.
