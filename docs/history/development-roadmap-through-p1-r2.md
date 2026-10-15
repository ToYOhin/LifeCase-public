> 历史快照：2026-09-18 文档整理前的 docs/DEVELOPMENT_ROADMAP.md。保留当时原文（含后来已过时的 Current/候选/授权表述），不是当前状态或待执行指令。以 [当前状态](../STATUS.md) 和 [当前路线](../DEVELOPMENT_ROADMAP.md) 为准。

# Detailed Development Roadmap

## Delivery strategy

LifeCase is delivered as thin vertical slices. Every slice must leave the repository runnable and must add one observable capability. Do not build all database tables, all screens, or all Agent tools in parallel.

The primary acceptance story is a warranty case:

> Import a purchase receipt and fault screenshot, extract evidence-backed facts, confirm uncertain fields, identify missing evidence, generate a safe action plan, and export a verifiable Evidence Pack.

## Current execution position

- Complete: M0 Foundation, M1 Evidence Intake, M2 Case Core, M3 Evidence Agent, and M4 Case Workbench.
- Complete: M5-01 Deadline engine, M5-02 Action planner, and M5-03 Approval gate/ICS export.
- Complete: M6-01 Evidence Pack and M6-02 Backup and restore.
- Complete: M7-01 Additional templates, M7-02 Thirty-case evaluation implementation, M7-03 Windows daily-use package code, and its short synthetic acceptance.
- Complete: the reviewed `subscription_cancellation_claim@1` product-coverage slice; the unchanged fixed corpus now records 27/30 product-processed Cases.
- Complete: the narrow M7 daily-use Web usability defect closure: readable native selectors, first-screen Case creation, current-Case/next-step navigation, and restart recovery without adding domain or model capability.
- Complete: P1-01 user-visible, Case-scoped, deterministic Agent Demo over the existing StageRunner/extraction/RAG boundaries.
- Complete and released: P1-R1 evidence, exact-commit CI, annotated `v0.1.0`, and the formal GitHub Release at `b6decb4`.
- Complete and published to main: P1-02 independent loopback-only Ollama structured Provider boundary at `b3e37b9`; exact-commit backend/Web CI passed in run `31502496839`. It remains outside the existing `v0.1.0` tag and GitHub Release.
- Implemented with an unsatisfied live-model gate: P1-03 controlled Ollama Agent evaluation and strict compatibility diagnosis. The deterministic seam and safe classifier pass offline tests, but no reviewed installed model/configuration completed the strict seven-stage contract.
- Implemented and offline live-ready: P1-04 composes the existing OpenAI Responses adapter into a separate explicit, warranty-only, read-only product API/Web path. Offline injected-client tests pass; real credential composition and real-model acceptance have not run.
- Implemented and offline live-ready: P1-05A adds a server-owned Provider Registry/capability contract and fixed Groq Chat profile. OpenAI behavior is preserved; Groq requires organization ZDR confirmation and has only offline injected-client proof.
- Implemented and offline live-ready: P1-05B adds a fixed Mistral Chat profile for `mistral-small-2603`. It requires organization ZDR confirmation and has offline seven-stage injected-client proof; the separate moving-alias connectivity smoke is not fixed-profile acceptance.
- Implemented diagnostic boundary: P1-05C adds safe field-level extraction failure codes and authority-preserving quote normalization. Large 3 identified `quote_authority`, but post-correction acceptance was blocked by remote protocol/timeout failures; Medium 3.5 was not called.
- Implemented and synthetic-validated: P1-05D fixes the DeepSeek JSON Object validate contract, passes answered/unknown warranty Cases, and composes the accepted fixed profile into `/live-agent` behind process injection and server data-use confirmation. Default remains disabled.
- Implemented and locally verified: P1-08 simplifies only the Web workflow over the P1-07 demo-complete baseline. The unselected screen stays focused on creation/listing; a selected Case mounts one of five progressive areas, with status/danger/export/backup/Fake tools disclosed on demand. API, Provider, authority, persistence and Fake behavior are unchanged.
- Implemented and locally verified: P1-09A preserves formal case-file language while merging selected-Case guidance, lowering internal panel density, collapsing the duplicate docket and keeping the canonical five-area workflow responsive without changing business behavior.
- Implemented and locally verified: P1-10 adds a server-owned, read-only AI interface catalog and a lazy Agent-area entry for the four existing cloud API boundaries plus Ollama. It adds no browser configuration, Provider behavior, model call or release claim.
- Implemented and locally verified: P1-11 moves AI configuration into a global drawer, persists one allowlisted fixed-model Key in Windows Credential Manager, adds Gemini and Anthropic adapters, and preserves the read-only Case authority. No live model was called.
- Implemented and verified: P1-12 adds DeepSeek V4 Flash/V4 Pro selection, actual-model Run summaries, bounded retrieval fallback and consent-bound versioned reanalysis. One V4 Pro answered Run and one preserved V4 Pro unknown Run form the fixed synthetic pair.
- Implemented as a local documentation candidate: P1-13 turns the persisted P1-12 synthetic evidence into a public portfolio walkthrough and responsive repository screenshots without changing product behavior or calling a model.
- In release closeout: P1-R2 prepares a local `v0.2.0` candidate from P1-02～P1-13 plus an explicitly authorized packaging-only Windows installer. Source entry, EXE asset publication, exact-candidate CI, tag, and formal Release remain separate evidence/authorization gates.
- M8 remains future work; no external write connector is authorized by the current roadmap state.

The responsive Web workbench covers desktop and mobile browser viewports from one codebase. Native desktop and mobile applications are not parallel deliverables in the current roadmap.

## Milestone overview

| Milestone | Outcome | Estimate | Exit gate |
|---|---|---:|---|
| M0 Foundation | Runnable, tested, documented repository | 1–2 days | API tests and Web build pass |
| M1 Evidence Intake | Immutable local source ingestion | 3–4 days | Import, dedupe, segment, retrieve |
| M2 Case Core | Warranty case lifecycle without AI | 3–4 days | Deterministic case end-to-end |
| M3 Evidence Agent | Structured extraction with provenance | 4–5 days | Five-case extraction evaluation |
| M4 Case Workbench | User can review and operate a case | 4–5 days | Browser acceptance path passes |
| M5 Deadlines and Actions | Safe next-step planning and reminders | 3–4 days | Date types and approval gates pass |
| M6 Evidence Pack | Verifiable export, backup, recovery | 3–4 days | Export/restore integrity passes |
| M7 Daily-use hardening | Three case types and 30-case eval | 5–7 days | MVP Definition of Done passes |
| P1 Portfolio Agent Demo | Observable read-only Agent run | 2–3 days | Persisted fake-provider run/retry/recovery demo passes |
| P1-02 Ollama Provider | Loopback-only structured adapter | 1 day | Offline boundary tests and adapter smoke pass |
| P1-03 Ollama evaluation | Controlled seven-stage local evaluation | 1–2 days | Quality, authority, recovery, and budget gates all pass |
| P1-04 Live Agent beta | Explicit Case-scoped OpenAI read-only composition | 1–2 days | Offline product path passes; real synthetic acceptance is separate |
| P1-05A Provider Registry | Fixed multi-provider profiles and Groq adapter | 1–2 days | Offline OpenAI/Groq composition and fail-closed capability gates pass |
| P1-05B Mistral Provider | Fixed official Mistral Chat profile | 1 day | Offline Mistral composition and fail-closed ZDR/client/model gates pass |
| P1-05C Mistral compatibility | Safe business diagnostics and contract correction | 1 day | Offline authority regressions pass; two stable Large 3 Cases remain open |
| P1-05D DeepSeek acceptance | Fixed JSON Object adapter, two-Case gate and read-only composition | 1 day | Answered/unknown live Cases and offline product path pass |
| P1-08 Workflow simplification | Progressive Case workspace with lazy UI mounting | 1 day | UI regressions and three-viewport synthetic browser acceptance pass |
| P1-09A Formal workflow density | Compact Case control and lower-density formal panels | 1 day | Formal-copy regressions, lazy forms and three-viewport browser acceptance pass |
| P1-10 AI interface catalog | Safe visibility for existing cloud/local Provider boundaries | 1 day | Secret-free catalog, lazy UI and offline/browser regressions pass |
| P1-11 Persistent AI settings | Restart-stable local configuration for six fixed cloud APIs | 1–2 days | OS credential restart/clear proof, offline adapter tests and responsive UI pass |
| P1-12 Versioned V4 analysis | Fixed DeepSeek model choice, truthful history and retrieval repair | 1 day | V4 Pro answered/unknown pair plus offline/browser regressions pass |
| P1-13 Portfolio presentation | Public walkthrough, resume path and persisted-result screenshots | 1 day | Three-viewport no-call screenshot/docs gate passes |
| P1-R2 v0.2.0 release candidate | Source entry plus optional per-user Windows installer | 1 day | Local release/installer gates pass; signing, exact-SHA CI and publication remain pending |
| M8 Connectors | Optional read-only email/calendar | 4–7 days | OAuth and revocation tests pass |

M0–M7 form the released Portfolio MVP. P1-01 is the released deterministic portfolio enhancement; P1-02 is a later mainline Provider boundary outside `v0.1.0`; P1-03 has a failed live-model exit gate; P1-04/P1-05A/P1-05B are live-ready product/Registry boundaries without fixed-profile real-model evidence; P1-05D adds synthetic fixed-model DeepSeek evidence and read-only product composition; P1-05C's Mistral live gate remains open; P1-12 is the demo-complete product baseline; P1-13 is presentation-only; P1-R2 packages the unchanged daily-use runtime for source and per-user Windows installation; and M8 is explicitly later work.

## M0 — Foundation

### M0-01 Repository baseline

Deliverables:

- monorepo layout;
- root configuration and ignore rules;
- Python and npm lockfiles;
- PowerShell development scripts;
- public engineering and safety rules.

Acceptance:

- fresh clone can resolve dependencies from lockfiles;
- no runtime data or secret file is tracked;
- root README contains working setup and verification commands.

### M0-02 API and Web smoke path

Deliverables:

- FastAPI application factory;
- `/api/v1/system/health` response contract;
- React case-loop shell;
- API and front-end unit smoke tests.

Acceptance:

- health returns the exact versioned contract;
- OpenAPI includes the health route;
- Web type check, unit test, and production build pass.

### M0-03 Project authority documents

Deliverables:

- product, architecture, data, Agent, security, API, testing, status, and ADR documents;
- CI validation workflow.

Acceptance:

- every README link resolves;
- `CONTRIBUTING.md`, security documents, and status documents contain public commands, boundaries, and Definition of Done;
- live status distinguishes verified and pending checks.

## M1 — Evidence Intake

### M1-01 SQLite foundation

Scope:

- add a migration runner using standard `sqlite3`;
- create `case_record`, `source`, `source_segment`, `artifact`, and `schema_migration` tables;
- enable foreign keys and WAL mode;
- store all timestamps as UTC ISO 8601 strings.

Tests:

- migrate an empty database;
- rerun migrations idempotently;
- reject missing foreign keys;
- recover from an interrupted migration using a temporary database.

Acceptance:

- `uv run pytest apps/api/tests/integration/test_migrations.py` passes;
- schema matches `docs/DATA_MODEL.md`;
- migration has an explicit rollback or documented forward-repair path.

### M1-02 Content-addressed source storage

Scope:

- accept an explicitly selected path;
- resolve and validate it before reading;
- stream SHA-256 calculation;
- copy source bytes into `objects/<2>/<2>/<sha256>` without changing the original;
- deduplicate identical bytes;
- enforce configured file-size and media-type limits.

Tests:

- duplicate file produces one object and two source records only when intended;
- path traversal and missing file fail safely;
- original file remains byte-identical;
- interrupted copy does not leave a valid-looking object.

Acceptance:

- import returns `source_id`, SHA-256, media type, size, and trust label;
- no API accepts an arbitrary unvalidated storage destination.

### M1-03 Text extraction

Scope:

- strict UTF-8/UTF-8 BOM plain text and safe EML parsing first;
- text-based PDF parsing second, with OCR explicitly disabled;
- parser-level content/signature validation, bounded work, and a child-process timeout;
- stable bounded segments with page, position, extraction method, confidence, text hash, and extractor version;
- idempotent whole-batch replacement in an explicit SQLite transaction.

Tests:

- synthetic UTF-8 and Chinese text fixtures;
- malformed file and timeout behavior;
- segment order and stable location references;
- instruction-like content remains data.

Acceptance:

- all extracted text can be traced to a source and stable location;
- OCR-required, malformed, encrypted, and unsupported content fail with safe typed errors;
- OCR is never silently enabled.

### M1-04 Source API and import UI

Scope:

- `POST /cases`, `POST /cases/{id}/sources`, and source detail endpoint;
- upload progress and explicit user-selected files;
- evidence viewer placeholder showing filename, hash, trust, and extraction status.

Acceptance:

- browser path creates a case and imports a synthetic source;
- duplicate and unsupported files receive actionable errors;
- no original file content is printed to logs.

### M1-05 Local Retrieval Foundation

Scope:

- build a rebuildable SQLite FTS5 index over SourceSegment text;
- require Case-scoped filtering for every retrieval request;
- rank lexical matches with BM25;
- return stable source and segment citations;
- add a deterministic index rebuild command and integrity checks;
- establish a Chinese synthetic retrieval evaluation set.

Acceptance:

- no query can retrieve a segment from another Case;
- deleting and rebuilding the index preserves authoritative SourceSegment rows;
- Chinese synthetic queries meet documented retrieval thresholds;
- no embedding model or vector database is introduced by default.

## M2 — Deterministic Case Core

### M2-01 Case aggregate and state machine

States: `intake`, `needs_review`, `ready`, `in_progress`, `waiting_external`, `blocked`, `resolved`, `abandoned`, `archived`.

Scope:

- typed transition commands;
- transition guards;
- append-only transition history;
- optimistic concurrency version.

Acceptance:

- invalid transitions are impossible through both service and API;
- transition history explains actor, reason, prior version, and new version.

### M2-02 Claim provenance

Scope:

- candidate, inferred, confirmed, corrected, and rejected claim states;
- ClaimSource join with quote hash;
- explicit user confirmation record;
- contradiction flag when two active candidates disagree.

Acceptance:

- Agent-created Claim without a source cannot become confirmed;
- corrected values retain prior values and reasons.

### M2-03 Warranty template engine

Scope:

- parse and validate the versioned warranty YAML template;
- keep business Case type `warranty` distinct from its explicit `warranty_claim@1` registry binding;
- compute missing required evidence;
- express conditional requirements and close conditions;
- reject unknown template keys.

Acceptance:

- deterministic fixture produces the expected missing-evidence list;
- template version is recorded on the case.

### M2-04 Evidence Context Builder

Scope:

- filter retrieval results by Case and evidence policy;
- deduplicate overlapping hits and expand adjacent segments deterministically;
- enforce a configured evidence-context budget;
- validate every citation and `text_sha256` against authoritative rows;
- produce a typed Evidence Context that a later Agent stage can consume.

Acceptance:

- context construction is deterministic for the same database state and budget;
- invalid, stale, cross-Case, or over-budget evidence is rejected;
- the builder performs no model call and has no external side effect.

## M3 — Evidence Agent

### M3-01 Provider boundary

Scope:

- internal `ModelProvider` protocol;
- disabled provider and deterministic fake provider;
- OpenAI Responses API provider behind configuration;
- cost, latency, model ID, and request-scope logging without document bodies.

Acceptance:

- CI uses only the fake provider;
- provider-specific response objects never enter domain services;
- disabled provider leaves deterministic features usable.

Implemented in M3-01: frozen versioned provider-neutral contracts; explicit disabled/fake/openai configuration; disabled and scope-scripted fake providers; and a current Responses structured-output adapter over an injected async client. The default remains offline and no live call, credential integration, SDK dependency, stage runner, persistence, retry, or budget orchestration is introduced.

### M3-02 Stage runner

Stages: `extract`, `normalize`, `link_claims`, `classify_case`, `check_gaps`, `propose_actions`, `validate`.

Scope:

- typed input/output schema per stage;
- one repair attempt for schema failure;
- stage checkpointing and resume;
- per-case token/cost/call budget;
- cancellation and timeout.

Acceptance:

- a failed stage resumes from the previous checkpoint;
- no stage retries forever;
- budget exhaustion produces `needs_review`, not partial silent success.

Implemented in M3-02: a fixed seven-stage registry with versioned immutable envelopes, empty tool allowlists, one schema-repair ceiling, explicit timeouts and stop conditions; one Case-scoped durable run with per-run/per-stage call, input/output token, and CNY minor-unit budgets; transactional pre-call reservation and conservative interrupted-call recovery; validated bounded checkpoints; optimistic resume/cancellation; and deterministic synthetic integration coverage. The default Case cost ceiling is CNY 1. Raw stage input, prompts, complete evidence, provider response objects, credentials, and remote error bodies are not persisted. No HTTP/UI, business Claim extraction, retrieval tool, or RAG behavior is added.

### M3-03 Evidence-backed extraction

Scope:

- extract merchant, product, purchase date, order number, amount, and warranty-relevant identifiers;
- require source references for fact candidates;
- reject nonexistent source references;
- expose confidence and uncertainty reasons.

Acceptance:

- five synthetic warranty cases meet the evaluation thresholds;
- every factual output has a valid source link or is labeled unknown/inferred.

### M3-04 Evidence-backed RAG integration

Scope:

- expose retrieval to the Agent as a controlled read-only tool;
- require structured model output;
- validate `SourceRef`, quote hash, and Case scope before accepting output;
- return `unknown` when evidence is absent or validation fails;
- do not introduce a vector database by default.

Acceptance:

- fabricated, stale, and cross-Case citations fail closed;
- retrieval cannot mutate cases, sources, tools, or system instructions;
- evaluation proves evidence-backed answers and explicit unknown behavior.

The required RAG implementation order is: M1-03 → M1-04 → M1-05 → M2 → M2-04 → M3. Planning may be refined earlier, but code must not skip these gates.

## M4 — Case Workbench

### M4-01 Case list and Today view

Deliver a responsive list with status, next action, deadline risk, missing evidence, and last activity. Empty, loading, offline, and failure states are first-class.

### M4-02 Evidence review

Deliver side-by-side source, extracted segment, candidate value, and confirmed value. Keyboard review should support confirm, correct, reject, and next uncertain field.

### M4-03 Timeline and recovery

Render source imports, transitions, decisions, approvals, communications, and artifacts in one ordered timeline. Refreshing or restarting the API must not lose progress.

M4 exit gate:

- Playwright completes the warranty path through evidence confirmation;
- all uncertainty and source links are visible without opening developer tools.

## M5 — Deadlines and Safe Actions

### M5-01 Deadline engine

- explicit dates come directly from confirmed evidence;
- calculated dates require a versioned deterministic rule and basis claims;
- suggested dates are personal follow-up preferences;
- timezone and date kind are always visible.

Implemented in M5-01: immutable explicit/calculated/suggested kinds; UTC-instant versus local-date values with required IANA timezone and DST gap/overlap handling; Case-scoped migration 8 authority; current confirmed/corrected Claim basis validation on write and read; one closed versioned calendar-day rule; proposed/confirmed/passed/completed/dismissed optimistic commands; atomic immutable history; thin APIs; a responsive provenance-visible workbench; and Timeline projection from committed Deadline events. Deadline risk remains uncalculated. No Action, Approval, ICS, external call, or model path was added.

### M5-02 Action planner

- add the closed `collect_evidence`, `prepare_communication`, `follow_up`, and `record_outcome` kinds with proposed/blocked/ready/running/done/failed/cancelled optimistic history;
- add Case-scoped acyclic dependency edges, deterministic topological ordering, canonical blocker snapshots, risk A/B/C/D, and same-Case completion-evidence references;
- deterministic validation prevents a non-collection Action becoming ready with missing, candidate, inferred, contradictory, stale, or cross-Case required Claims;
- bind an optional due date only to a revalidated M5-01 Deadline ID/version/definition hash and preserve its UTC-instant or local-date semantics;
- generate communication drafts locally and deterministically from explicitly bound confirmed/corrected Claims; add no send/submit connector;
- keep `approved` unreachable and add no Approval/ICS table, command, route, or UI in this slice.

Implemented in M5-02: migration 9 persists the Case-scoped Action aggregate, bindings, immutable contiguous history, and optimistic lifecycle; the service owns deterministic blockers, graph order, revalidation, completion evidence, communication drafts, and risk enforcement; thin APIs, authoritative `next_action`, responsive recovery states, and committed Timeline projection are available. This slice itself did not admit `approved`; M5-03 adds that state through separate authority.

### M5-03 Approval gate and ICS export

- add pending/approved/rejected/expired/invalidated immutable Approval authority; approval binds the local actor label, Action version, visible summary and hash, payload hash, decision, and time;
- only M5-03 may move a qualifying ready Action to approved; Risk A needs no approval, Risk B/C require the defined gate, Risk C still cannot execute, and Risk D remains prohibited;
- modified Action payload invalidates prior pending/approved authority in the same transaction while preserving immutable audit history;
- confirmed Deadline authority exports an RFC 5545-compatible deterministic `.ics` HTTP download; local dates remain all-day values and UTC instants remain UTC;
- no email send or browser submit implementation in MVP.

Implemented in M5-03: migration 10 forward-rebuilds the Action status constraint and adds exact-version Approval request/event authority. Risk B starts only after approval, Risk C can be approved but not executed, and Risk D remains uncreatable. Revision, revalidation, or cancellation invalidates active approval in the same transaction. ICS is a pure deterministic read over a fully validated same-Case confirmed Deadline and returns immediate HTTP bytes without database, Artifact, connector, or calendar writes.

M5 exit gate:

- Action dependency, blocker, required-Claim, Deadline-link, optimistic-concurrency, rollback, restart, and deterministic-order tests pass;
- approval bypass tests fail closed;
- payload mutation invalidates prior approval while preserving immutable audit history;
- ICS export accepts only confirmed Deadline authority, is deterministic for identical committed input, and creates no system-calendar or external side effect;
- explicit/calculated/suggested dates never render as the same type.
- full backend/Web regression plus desktop and 390 px browser recovery acceptance passes.

## M6 — Evidence Pack, Backup, and Recovery

### M6-01 Evidence Pack

Export `manifest.json`, `timeline.md`, source copies, derived files, communications, approvals, and `checksums.sha256`. Original, derived, generated, and confirmed layers remain separate.

Implemented in M6-01: one deterministic Case-scoped ZIP snapshot contains revalidated source copies, derived segments/Claims/artifacts, generated Deadline/Action/communication projections, confirmed Case/Claim/Deadline/Approval authority, and a committed Timeline. Fixed ZIP metadata plus canonical JSON/order make unchanged authority byte-identical and idempotently reuse one retained `artifact.type=pack`. Portable-path, member/total-size, manifest-membership, stored-file, and SHA-256 checks run before publication and again before download. Thin create/latest/download APIs and a responsive recovery/download surface are available. No migration, dependency, backup/restore, connector, model, network, or external effect was added.

### M6-02 Backup and restore

- SQLite online backup;
- object manifest reconciliation;
- versioned export format;
- restore into a clean data directory;
- pre-migration automatic backup.

Implemented in M6-02: `format_version=1` whole-authority ZIPs use SQLite's online backup API rather than copying live WAL/SHM files. A canonical manifest binds the database snapshot and every Source/Artifact-referenced retained file to portable relative paths, authority IDs, Case scope, size, and SHA-256. Creation uses isolated temporary files, complete independent verification, and content-addressed no-overwrite publication. Validation rejects unsafe/colliding paths, symbolic links, missing/extra/stale members, size/hash drift, unsupported format/application versions, migration drift/future migrations, and SQLite integrity/foreign-key failures. Apply revalidates a persisted ticket after restart, accepts only a new target name under the recovery root, runs the existing migration and Case/Claim/Deadline/Action/Approval/Timeline/Evidence Pack read boundaries in staging, then atomically publishes a new runtime directory. Startup creates a verified pre-migration backup only for an existing database with pending migrations. No business migration or dependency was added.

M6 exit gate:

- modify one exported byte and integrity verification fails;
- restore reproduces the case, sources, claims, timeline, and artifacts.

## M7 — MVP Daily-use Hardening

### M7-01 Additional templates

Add return/refund and reimbursement templates only after warranty evaluation passes. Reuse domain rules; do not fork the engine per case type.

Implemented in M7-01: the closed reviewed registry now contains `warranty -> warranty_claim@1`, `return_refund -> return_refund_claim@1`, and `reimbursement -> expense_reimbursement@1`. Case creation explicitly selects one business type and persists the exact template identity/version. The same strict parser, canonical Claim authority reader, finite gap/close evaluator, Action planner, Timeline, Evidence Pack, and Backup/Restore boundaries serve all three types. A thin read-only catalog and responsive explicit selector expose identities and gaps without loading arbitrary templates. All three resources are packaged in the API wheel. No migration, dependency, model call, template editor, M7-02 dataset, M7-03 package/reminder, or M8 connector was added.

### M7-02 Thirty-case evaluation

- 8 return/refund;
- 8 warranty;
- 7 reimbursement;
- 4 subscription cancellation;
- 3 travel/logistics claim.

Release thresholds are defined in `TESTING_AND_EVALUATION.md`.

Implemented in M7-02: fixed versioned `m7-v1` fixtures contain exactly 30 synthetic Cases with separate input, expected Claim/gap/Deadline/Action labels, metadata, and recorded observations. A strict offline evaluator rejects unsafe paths, schema/member/scope collisions, stale or cross-Case citations, invalid hashes, unknown Deadline rules, unsafe Action risk, and zero denominators; it emits canonical JSON and concise Markdown without wall-clock or machine-path data. At the original M7-02 baseline, only the 23 warranty/return-refund/reimbursement Cases counted as product-processed and the 7 subscription/travel records remained evaluation-only. That truthful `FAIL` baseline motivated the later separately reviewed product-coverage closure below; M7-02 itself added no product template, API/Web flow, migration, dependency, model call, or M7-03/M8 capability.

### M7-03 Windows daily-use package

- one-command setup and start;
- local data directory selection;
- backup reminder;
- useful error messages for OCR/model unavailability;
- optional seven-day personal dogfood with at least three completed real cases for a sustained daily-use readiness claim.

Implementation and acceptance order:

1. package contract: one reversible PowerShell entry point, locked existing runtimes, explicit/validated data root, loopback-only binding, and no-overwrite install/start behavior;
2. runtime composition: serve the built Web assets and API from one loopback origin, apply the existing migration/pre-migration Backup order, probe SQLite/FTS and health, and leave no false running state after partial failure;
3. recovery and operator UX: typed actionable failures for invalid/unwritable paths, port conflicts, missing static assets, OCR-required input, disabled model capability, and migration/Backup failures; show verified Backup state without silently creating, deleting, uploading, or scheduling anything;
4. short synthetic package acceptance: complete one new fictional Case through Claim review, Deadline, Action, Approval, ICS, Timeline, Evidence Pack, Backup, exit, restart, and recovery; this proves package integration only;
5. optional seven-day private dogfood: if sustained daily-use readiness will be claimed, complete at least three real Cases while keeping all personal materials, runtime data, exports, screenshots, and detailed logs outside Git. A shorter or synthetic run must remain explicitly unverified.

The product-processing gate is satisfied by the separately reviewed `subscription_cancellation_claim@1` capability: all four fixed subscription-cancellation Cases traverse the same Case, Claim provenance, template gap, Deadline, Action/Approval, Timeline, Evidence Pack, and Backup/Restore services, so the unchanged corpus records 27/30 product-processed Cases. The evaluator threshold, corpus distribution, expected records, and three evaluation-only travel/logistics Cases are unchanged. This does not provide real-use evidence.

Implemented in the M7-03 code slice: steps 1–3 use the existing Python/npm locks with no new dependency, while focused integration tests cover safe path ownership, same-origin readiness, port/static/migration/pre-migration Backup failures, process cleanup, explicit offline capabilities, read-only Backup reminder, restart recovery, and uninstall retention. Step 4 completed as a separately recorded short acceptance. Under ADR 0005, step 5 is optional and recorded as `NOT_RUN`; M7 is locally complete for the Portfolio MVP without claiming sustained real-use readiness.

### M7 product-coverage closure

Implemented after M7-03: the closed registry, Case-create API, and explicit Web selector add only `subscription_cancellation -> subscription_cancellation_claim@1`. The strict immutable YAML resource is packaged in the API wheel and uses the five cross-template release Claims plus explicit reference-evidence and conditional cancellation-confirmation requirements. Four fixed subscription-cancellation fixtures pass a real offline product path with source provenance, deterministic gaps, all three Deadline semantics, Risk C record-only Approval behavior, Risk A evidence collection, Timeline, Evidence Pack, restart, and Backup/Restore. No dependency, migration, model runtime, Agent HTTP/UI/tool, OCR, embedding, vector database, Docker, connector, evaluator threshold, expected record, or corpus distribution changed.

### M7 daily-use Web usability defect closure

Implemented as a narrow release defect fix after the package and coverage slices: the first screen now establishes a Case before presenting result-oriented work areas, Windows native selector text and options use explicit readable colors and inherited typography, and an opened Case shows a plain-language next step plus direct navigation to material intake, fact review, deadlines, actions/approvals, export, and timeline. Template identity and Case version remain available under explicit technical details instead of competing with the primary task. This changes no API, authority record, template, dependency, migration, model/runtime, Agent tool, connector, evaluator, corpus, or dogfood result.

### M7 deterministic browser operation closure

Implemented as the final deterministic browser loop for the Portfolio MVP: one exact user-selected SourceSegment quote can create a template-bound candidate Claim through a thin HTTP command and the existing Claim provenance service; the browser can then use the existing Claim decision, suggested personal-date, Action/Approval, Case lifecycle, ICS, Evidence Pack, Backup/Restore, and Timeline authorities without duplicating domain rules. The complete synthetic daily-use path survives a process restart and remains usable at 390 px. The server recomputes quote hashes, validates same-Case/template authority, owns legal transitions and completion eligibility, and persists every result; React supplies explicit user intent only. No dependency, migration, model/runtime, new Agent tool, OCR, embedding, vector database, Docker, connector, evaluator, corpus, or dogfood result changed. Optional seven-day dogfood remains `NOT_RUN`, so no sustained real-use claim is made.

## P1 — Portfolio Agent Demo

P1 is the post-M7 portfolio milestone. Its purpose is to make the already implemented Agent architecture observable without entering M8 or adding external effects.

### P1-01 Case-scoped read-only Agent run

Status: implemented locally on 2026-08-09.

- compose the existing StageRunner, evidence-backed extraction, and `evidence_context.search.v1` RAG validation behind thin Case-scoped HTTP contracts;
- keep the application default provider disabled and return an explicit capability error when no provider is configured;
- use deterministic fake-provider scripts for CI and demo fixtures; do not claim that recorded fake output is live-model output;
- show persisted run/stage status, bounded checkpoints, the one allowed read-only tool call, citations, retries, resume/cancel state, budgets, and safe errors in the Web UI;
- allow Agent results to create only candidate/inferred Claims through the existing provenance service; confirmation and every external-effect boundary remain user-owned;
- add no connector, email/calendar write, browser submission, OCR, embedding, vector database, Docker, telemetry, credential discovery, or M8 capability.

P1-01 exit gate: a new synthetic Case can start a deterministic Agent run, visibly use Case-scoped evidence retrieval, persist a provenance-backed candidate result, recover the complete safe trace after restart, retry one injected failure, and leave all deterministic Case operations usable when the model capability is disabled. Optional OpenAI or local-model composition is a separate later slice.

Implemented: explicit opt-in creates one queued run per reviewed warranty Case and the Web then executes the fixed seven stages through a thin application composition. Other Case types are rejected because the existing formal extraction Schema is warranty-only. The server derives every Fake Provider script from current authoritative Case/Segment state; the browser submits no provider output or checkpoint. Exact-quote extraction commits candidate/inferred Claims through the unchanged Claim service, while the real read-only tool result drives validate to `answered` or `unknown`. Safe projections expose stage state, budget/usage, allowed tool trace, Claim results, citations, retry/recovery/cancellation, and no input body, Context text, raw provider response, SQL, credential, object path, or private log. Existing tables were sufficient; no migration or dependency was added.

### P1-R1 Release evidence closeout

Status: complete and released. The final documentation commit `b6decb4b052e7d8dfc23cafc4af60a5e529aae4f`, annotated `v0.1.0`, GitHub Release, and backend/Web CI run `31309640547` have exact hash parity.

1. Generate the current FastAPI OpenAPI read-only and compare every P1-01 route, status code, request/response model, and safe error with `docs/API_CONTRACT.md`.
2. Confirm that only explicitly validated files under `packages/contracts/schemas/` are shared-schema authorities; do not create a blanket duplicate of generated OpenAPI.
3. Record bounded aggregate timing plus fixed call/Token/cost ceilings from the existing Fake Provider and synthetic Case path. Label the result machine-specific and non-live-model.
4. Keep product code, evaluator, corpus, templates, dependencies, migrations, dogfood state, and external-effect boundaries unchanged.
5. Only after explicit publication authorization, push the final exact commit and require both backend and Web CI to pass for that same hash.

Exit gate: satisfied for `v0.1.0@b6decb4` — OpenAPI/shared-schema consistency and deterministic cost/latency evidence are documented, local checks and the sensitive-content audit are clean, and exact-commit backend/Web CI is verified. Any later release-target commit must independently pass CI for its own SHA.

### P1-02 Loopback-only Ollama Provider

Status: implemented on 2026-08-10 and published to `main@b3e37b9` on 2026-08-11; exact-commit backend/Web CI passed in run `31502496839`. No new tag or GitHub Release was created.

- add an independent `ollama` Provider identity rather than reusing or relabeling `openai`;
- require explicit model selection while preserving `disabled` as the default;
- call only native `http://127.0.0.1:11434/api/chat` with the standard library, `stream=false`, `think=false`, temperature 0, a bounded output-token ceiling, and the supplied JSON Schema;
- retain only canonical structured output plus safe provider/model/scope/latency/token metadata; report no monetary cost when Ollama supplies none;
- extend `agent_stage_run.provider` through migration 11 without changing any other persisted authority;
- add no API/Web route, Agent Demo replacement, general chat, SSE, tool, OCR, embedding, vector database, Docker, connector, credential path, remote host, or external write.

P1-02 exit gate: offline boundary/error/log-redaction tests and migration retention checks pass, and a fresh synthetic call through the production factory and an installed local model returned schema-valid JSON. This proves adapter connectivity only. The separately authorized P1-03 slice subsequently exercised full seven-stage composition and recorded a failed model-quality gate without changing the P1-02 adapter result.

### P1-03 Controlled Ollama Agent composition and evaluation

Status: implemented, evaluated, and narrowly diagnosed locally through 2026-08-12; exit gate not satisfied. This candidate is not pushed, tagged, released, or composed into product HTTP/Web.

- add only an explicit evaluation provider seam; normal API construction continues to use disabled/Fake behavior;
- create two to five fresh temporary synthetic warranty Cases and use the existing seven-stage runner, extraction, Claim provenance, read-only retrieval, RAG validation, and restart projection;
- require at least 90% exact field/disposition accuracy, 100% valid citations, zero unsupported Claims, correct answered and fail-closed unknown outcomes, all seven stages, and recovery for every run;
- report only safe aggregate call/token/time and failure metadata, with a 300-second per-run and matching aggregate ceiling by default;
- classify provider-neutral invalid candidates only through closed content-free categories without retaining values, paths, checkpoints, identifiers, raw responses, or validator details;
- preserve the fixed Fake Provider CI baseline, fixed M7 corpus/evaluator/27/30 result, default disabled Provider, and all no-external-effect boundaries.

Observed local evidence: two `qwen2.5:3b` runs made four calls and used 10,908 total tokens within the configured time bounds, but both exhausted the one allowed Schema repair and ended `agent_schema_invalid` before extraction commit. Recovery passed 2/2; seven-stage completion, field quality, citations, answered quality, and unknown quality were therefore not established. This is a useful fail-closed compatibility result, not successful local-model Agent performance. See `docs/evaluation/p1-03-ollama-agent-evidence.md`.

The narrow 2026-08-12 diagnostic added no tolerant parsing or authority change. Historical outputs were not retained and therefore received no inferred narrower category. A two-Case CPU-only low-resource rerun of the only installed model, `qwen3:4b`, reached the unchanged 120-second first-stage timeout twice before producing any provider-neutral candidate. Recovery remained 2/2, but seven-stage completion remained 0/2 and the overall result remained `FAIL`.

P1-03 exit gate remains open. Do not route Ollama into the product Agent Demo/API/UI unless a separately reviewed model/configuration passes the unchanged controlled evaluation. Do not enter M8 based on this result.

Recommended order from the current state:

1. stop the current local-model compatibility route and retain the deterministic Fake Provider baseline because the unchanged gate did not pass;
2. treat any different model, resource envelope, or compatibility strategy as a new explicitly authorized evaluation slice;
3. treat publication of the local candidate and exact-commit CI as a separate release action;
4. treat private dogfood as a separate real-use evidence lane;
5. enter M8 only after a documented real workflow proves explicit local file/EML import insufficient and identifies a minimum read-only mailbox/calendar requirement.

### P1-04 Live Agent Read-only Beta

Status: first minimum product slice implemented locally on 2026-08-12; real-model acceptance not run.

- keep `disabled` as the default and preserve every P1-01 Fake Demo route, response, label and test;
- expose a separate warranty-only `/live-agent` API/Web path whose preview is computed from server configuration and authoritative Case Segments;
- require explicit confirmation of the preview SHA-256 before creating one queued run; bind the run ID to that scope so changed evidence/model/budget fails closed before execution;
- construct the existing `OpenAIResponsesProvider` only from an injected process-local client; accept no browser host, API key, model, provider output, checkpoint, tool or budget and add no SDK dependency;
- reuse the fixed seven stages, one Schema repair, Claim provenance, Evidence Context/RAG, budget reservation, cancellation, interrupted recovery and single retry;
- permit only candidate/inferred Claims and validated answered/unknown output; create no Deadline, Action, Approval or external effect;
- persist/log no prompt, full evidence Context, raw response object, credential, machine path or private remote error.

Offline exit gate: satisfied. Default/no-client/unsupported/stale/unconfirmed paths fail closed; an injected in-memory Responses client completes the product API and Web transport without opening real network access; P1-01 remains unchanged. This proves live-ready composition, not real-model quality, real credentials, remote retention behavior, paid cost, CI for the local HEAD, or a Release.

Next gate, only under separate authorization: one fixed model, 2–5 temporary synthetic warranty Cases, a declared aggregate CNY ceiling and immediate stop on credential/configuration leakage, unexpected host/tool, unsupported authority mutation, first unrecoverable safety failure, or budget overrun. Do not use personal data or retain raw responses.

### P1-05A Multi-provider Registry

Status: minimum offline slice implemented locally on 2026-08-12; no real Groq/OpenAI request was made.

- add immutable server profiles and capability contracts above `ModelProvider`; profiles include identity, fixed endpoint preset, allowed/selected model, structured output, usage/cost availability, storage control, stream/tool policy, credential source, timeout and byte ceilings;
- preserve OpenAI Responses behavior and the default-disabled/Fake Demo boundaries;
- add a fixed Groq Chat adapter for `https://api.groq.com/openai/v1/chat/completions` and `openai/gpt-oss-20b`, strict JSON Schema, non-streaming output and no tools;
- require explicit server confirmation of organization ZDR because the official Chat API does not support a request-level `store` parameter; missing ZDR/client/capability/model fails before a call;
- reject arbitrary base URLs, browser Provider/model/profile parameters and credential configuration; add no SDK dependency;
- extend only the durable provider identity CHECK through migration 12 and reuse all existing StageRunner, repair, budget, provenance, RAG, cancellation, recovery and retry authority.

Offline exit gate: injected OpenAI and Groq clients complete their respective product paths; disabled, missing-client, missing-ZDR and unsupported-profile paths make zero calls; adapter/log/SQLite/API audits retain no raw response, prompt, evidence body, credential, path or private error. Real account controls, credentials, model quality, free-tier limits, latency, cost and retention behavior remain `NOT_RUN` and require a separately authorized acceptance.

### P1-05B Mistral Provider

Status: minimum offline slice implemented locally on 2026-08-12; no fixed-profile real Mistral request was made.

- add a distinct profile fixed to `https://api.mistral.ai/v1/chat/completions` and non-moving `mistral-small-2603`;
- require strict JSON Schema, required usage, non-streaming/no-tools output, an injected process-local client and explicit server confirmation of organization ZDR;
- reject moving/arbitrary models and nonofficial endpoints before a call; expose no browser Provider/model/host/key/parameter surface;
- map only canonical `StructuredGenerationResult`, persist only safe provider/model/usage metadata, and extend only the closed durable provider identity through migration 13;
- reuse the unchanged seven stages, repair, budgets, cancellation, recovery, retry, provenance, RAG and business authority, with no Deadline/Action/Approval or external effect.

Offline exit gate: satisfied by focused adapter/Registry tests and a full in-memory product API run. The earlier synthetic moving-alias smoke proves basic Mistral connectivity and structured output only. Fixed `mistral-small-2603` product quality, ZDR/account state, credential composition, cost, latency and retention remain `NOT_RUN` pending a separately authorized acceptance with a replacement credential.

### P1-05C Safe business diagnostics and Mistral compatibility

Status: field-level diagnostics and citation-contract correction implemented locally on 2026-08-12; Large 3 acceptance remains `FAIL / NOT ESTABLISHED`.

- add a closed extraction business code using only a fixed reviewed field identity and fixed failure class; retain no raw response, evidence body, candidate value, JSON path, identifier, hash, checkpoint, private error, or credential;
- make the extraction prompt explicit about all six dispositions, unknown/null behavior, source-reference authority, date/amount shape, and copying the supplied Segment hash;
- preserve valid exact citations, but replace invalid model-derived quote offsets/hash with a full-Segment citation only after current source/Segment/Segment-hash authority matches;
- keep wrong source/Segment/hash fail-closed, the single repair ceiling unchanged, and candidate/inferred/Claim/RAG/business authority unchanged;
- change no product API, Provider profile/default, dependency, database schema, model allowlist, external effect, or publication state.

Observed compatibility evidence: the first bounded Large 3 diagnostic isolated `extract.merchant_or_manufacturer.quote_authority`. Post-correction offline regressions pass, but subsequent real requests encountered `remote_protocol_error` and a 120-second transport timeout; the last bounded request returned no candidate. Therefore two successful post-correction Large 3 Cases are not established. The conditional Medium 3.5 run was not attempted because its prerequisite was not met; the proposed USD 0.08 ceiling and first-Case-failure stop remain future controls, not measured spend. See `docs/evaluation/p1-05c-mistral-agent-evidence.md`.

### P1-05D DeepSeek synthetic acceptance and product composition

Status: answered and unknown temporary synthetic warranty Cases passed on 2026-08-12; the fixed adapter is available through the disabled-by-default read-only Registry path.

- fix `https://api.deepseek.com/chat/completions` and `deepseek-v4-flash` in both evaluation and product Registry composition;
- use JSON Object, disabled thinking/stream/tools, exact Schema instruction, canonical local validation and one repair;
- inject the credential only into a fixed-host process client; retain no raw response, evidence body, prompt, private error, key or path;
- enforce an aggregate USD 0.02 nominal list-price cap with a conservative pre-call reservation and stop after the first Schema/authority/sensitive failure;
- require an injected process-local client and explicit server synthetic/private data-use confirmation before `/live-agent` can compose it; change no browser request shape, default or external authority.

Live result: one answered and one unknown Case passed all seven stages and recovery using 14 calls and nominal USD 0.00205321, with 12/12 correct Claims and 13/13 valid citations. This authorizes only the explicit synthetic/private read-only profile; no personal-data, ZDR, dogfood or external-effect claim follows.

### P1-06 / P1-07 Demo readiness and UX

Status: implementation complete on 2026-08-12. The fixed DeepSeek synthetic path has a hidden-key loopback
launcher, two repeatable reviewed warranty seeds, marker-protected reset and controlled screenshots.
The Live Agent result is now presented as a modern evidence dossier: conclusion and citation first,
readable candidate/inferred facts second, compact seven-stage progress third, and technical usage/citation
metadata behind disclosure. The deterministic Fake Demo remains unchanged in a secondary disclosure.

This presentation work changes no API request, Provider behavior/default, business authority, persistence,
dependency or external effect. For the personal-project/resume goal this is the practical demo-complete
point; recording, exact-head CI for each published SHA, any later tag/Release and private dogfood are separate next decisions.

### P1-08 Workflow simplification

Status: local UI-only implementation and synthetic browser verification complete on 2026-08-13.

- before Case selection, mount only Case creation and the docket;
- after selection, expose Overview, Agent, Evidence Review, Actions and Deadlines, and Records and Export as a five-step case-file rail, mounting only the selected area;
- keep status transitions, dangerous operations, Fake Demo, Evidence Pack and Backup/Restore closed until explicitly expanded;
- preserve every API request body, Provider profile/default, business authority, persistence path, Fake Demo command and external-effect boundary;
- verify with presentation/lazy-mount regressions, offline/Fake backend regressions and real Chromium at 1440×900, 1024×768 and 390×844 without a model call.

This slice adds no router, component library, chat, SSE, tool, connector, cloud deployment, dependency,
migration, tag or Release. Remote publication and exact-head CI remain separately authorized evidence lanes.

### P1-09A Formal workflow density

Status: local UI-only implementation and synthetic browser verification complete on 2026-08-13.

- retain formal Case field names and the canonical `案件概览 / Agent 分析 / 事实复核 / 行动与截止 / 记录与导出` areas;
- merge selected-Case status, identity, recommended step, primary action, return control, lifecycle disclosure and area navigation into one compact control region;
- reuse the docket behind a closed `切换案件（N）` disclosure after selection and close it again when another Case is opened;
- prioritize existing records and results while keeping manual fact/date/action forms, runtime metadata, Evidence Pack and Backup/Restore closed until requested;
- verify creation/return/switch/hash flows, single-area mounting, approximately 180 px empty states, first-viewport navigation and no document-level mobile overflow.

P1-09A changes no API request body, internal enum, Provider profile/default, business authority, persistence,
Fake Demo behavior, dependency, migration or external-effect boundary. Publication and exact-head CI remain separate.

### P1-10 AI interface catalog

Status: local minimal implementation and offline verification complete on 2026-08-13.

- add one secret-free `GET /api/v1/system/ai-capabilities` projection over existing Provider boundaries;
- list OpenAI, DeepSeek, Groq and Mistral as cloud APIs, and Ollama as an evaluation-only local model boundary whose quality gate remains failed;
- add one default-closed Agent-area entry and mount its request/content only after explicit expansion;
- show current server configuration and truthful evidence status without accepting credentials, endpoint, Provider/model selection or runtime parameters;
- preserve the existing warranty-only `/live-agent`, P1-01 Fake Demo, process-injected clients, default-disabled configuration, business authority, persistence and external-effect boundaries.

This slice does not rerun a real or paid model, download/start Ollama, add a settings center, dependency,
migration, connector, tag or Release. A new DeepSeek test requires a newly issued credential supplied only
through the existing hidden launcher; any value pasted into chat must be revoked and must not be reused.

### P1-11 Persistent AI settings

Status: local implementation, offline verification and Windows restart/clear proof complete on 2026-08-13.

- replace the Agent-area catalog entry with one global `AI 设置` drawer available before or after Case selection;
- allow OpenAI, DeepSeek, Groq, Mistral, Gemini and Anthropic only, each with a project-fixed endpoint and model;
- save one API Key to the current user's Windows Credential Manager, restore it on LifeCase restart and provide explicit clear;
- add Gemini `generateContent` and Anthropic Messages structured-output adapters without new dependencies;
- keep Ollama evaluation-only and keep every actual run inside the existing warranty-only, consent-bound, read-only seven-stage workflow;
- verify all Provider contracts with injected fakes, never with a live or paid model in the normal gate.

This slice adds migration 15 only for durable Provider identity and adds no business authority, connector, chat, SSE, arbitrary endpoint/model input, cloud deployment, tag or Release. A saved configuration's `ready` state is local composition status, not remote credential or quality acceptance.

### P1-12 Versioned DeepSeek V4 analysis

Status: implementation, offline verification and authorized synthetic V4 Pro acceptance complete on 2026-08-13.

- expose only `deepseek-v4-flash` and `deepseek-v4-pro` in AI settings;
- retain each stage's actual provider/model and project it into Run summary/history;
- split goal text into bounded safe terms, discard a closed set of task-language stopwords, place alphanumeric product/fault identifiers before broader disjunctive ASCII/CJK terms and, for Live Agent only when none match, activate the closed warranty-domain bilingual alias set from matching Chinese or English intent markers through the existing retrieval service;
- preserve terminal Runs and require fresh preview consent before creating the next version;
- keep P1-01 Fake Demo, Provider defaults, Claim authority, no-tools/no-actions boundary and release state unchanged.

### P1-13 Portfolio presentation closeout

Status: local documentation and screenshot candidate completed on 2026-08-14.

- use only the retained P1-12 fixed-synthetic V4 Pro Case and its five persisted Runs;
- lead the public README with the answered result, seven citations, six candidate Claims and version history;
- provide a three-minute walkthrough, resume-ready wording and explicit non-claims;
- verify real Chromium at 1440×900, 1024×768 and 390×844 with no document overflow or console warning/error;
- keep the historical port 8765 launcher out of the current portfolio path; viewing the persisted result requires no Key and no model call;
- change no API, authority, persistence, Provider/default, dependency, migration, product runtime, M7 result, dogfood state, tag or Release.

### P1-R2 v0.2.0 Windows daily-use Release candidate

Status: local release-only closeout complete on 2026-08-14; no push, tag, or formal Release is authorized.

- set the Python workspace, root npm workspace, and Web package version to `0.2.0`, synchronizing only the matching lockfile project-version mirrors;
- retain the source checkout plus `scripts/lifecase.ps1`, and optionally distribute the same daily-use runtime as an unsigned per-user `LifeCaseSetup.exe`; do not claim MSI, portable mode, native UI, auto-update, signing, production service or dogfood;
- audit P1-02～P1-13 against the existing API, authority, persistence, Provider/default, dependency, M7 27/30, dogfood `NOT_RUN`, Ollama `FAIL`, no-tools/no-actions, and M8 boundaries;
- run the deterministic repository gate, Markdown links, version consistency, diff, sensitive/private-path, artifact, and ignored-file audits without reading a Provider credential or calling any model;
- after an explicitly authorized push, require backend and Web success for the exact candidate SHA; require later separate authorization for annotated `v0.2.0` and the matching GitHub Release.

## M8 — Optional connectors

M8 is not started. It requires a documented real-use need, a minimum read-only scope, a threat-model update, and separate product authorization. The current P1-03 model-compatibility failure does not create or justify a connector requirement.

Order:

1. read-only EML/file drop remains the baseline;
2. read-only mailbox OAuth import;
3. calendar event creation with explicit approval;
4. draft creation without sending;
5. external sending only after a separate threat model and approval usability test.

## Cross-cutting work required in every milestone

- update `docs/STATUS.md` with verified and pending state;
- add or update threat scenarios when a new I/O boundary appears;
- add fixture and evaluation coverage before changing prompts;
- update API and data contracts in the same slice as semantic changes;
- keep lockfiles current only for intentional dependency changes;
- scan staged files for secrets and personal data before commit.

## Recommended commit sequence

Use small commits that correspond to the IDs above, for example:

```text
M1-01 add SQLite migration foundation
M1-02 add immutable source storage
M1-03 extract source text with stable segments
M1-04 expose source intake flow
M1-05 add local case-scoped lexical retrieval
M2-04 build validated evidence context
M3-04 integrate evidence-backed retrieval with the Agent
```

Do not combine milestone work, dependency upgrades, and unrelated refactors.
