# Testing and Evaluation

Current product-slice evidence is routed through the [evaluation index](evaluation/README.md); [STATUS](STATUS.md) separates the current product from historical release evidence. This document defines the verification layers and retains dated milestone records. An old passing run does not validate later source changes or a new release.

## Reading verification evidence

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| Deterministic unit, contract and integration tests | Rules, transactions, failure behavior and supported compatibility for the tested revision | Real-model quality or independent human usability |
| Actual local API/Web | A browser or client used a listening FastAPI service and the stated production Web build with isolated synthetic data | Public hosting, other browsers or real-user acceptance |
| In-process API plus browser response replay | Actual FastAPI route results and browser behavior against captured responses | Browser-to-server HTTP integration or a real service restart |
| Actual process restart | Recorded old/new process identity and reads of the same persisted data after restart | Recovery for unrelated features, machines or later changes |
| Authorized model or human evaluation | Only the named model/profile, dataset, participant and dated protocol actually exercised | Quality of a newer Agent flow, private dogfood or general product readiness |

Record the data isolation, credential/model restrictions, injected failures, revision, results and cleanup alongside each run. A synthetic transport can exercise the real Provider adapter and local application without making a real model call; report those facts separately. Browser automation is not independent human acceptance.

The current question-bound `q1` path executes `validate` with persisted question/current-fact input. The no-question compatibility path retains S1's `extract -> validate`; older seven-stage Runs retain their original read/recovery contract. See the [question-analysis record](evaluation/case-question-analysis-20260919.md) and [S1 record](evaluation/production-agent-simplification-20260918.md). Historical seven-stage evaluations below must not be presented as current q1 model-quality evidence.

Use the existing [`scripts/verify.ps1`](../scripts/verify.ps1) checks for repository gates and the relevant focused tests for local changes. Run checks serially; use the workspace test command with `--maxWorkers=1` when limiting Vitest to one worker. Runtime acceptance additionally needs its own isolated data and credential/model guards before application import. Code gates alone do not provide that runtime evidence.

The [evaluation index](evaluation/README.md) identifies completed follow-up fixes and remaining acceptance gaps. Suggestions inside dated records are historical, not a current work queue. A tool rejection limits the affected operation's evidence; it is neither a product failure nor a project-wide development prohibition. New aggregate verification must be recorded from its actual results rather than inferred from the dated counts below.

## S1 production Agent simplification

S1 passed local synthetic acceptance on 2026-09-18. New `portfolio-live-v2-` production Runs execute only `extract -> validate`; the response version is `live_agent_read_only.v2`, and each Run/history item reports its `stage_order`. Regressions prove exactly two model-adapter calls on a successful synthetic path without repair/retry, preserved candidate/citation results, and no placeholder checkpoints. The validate input uses `validate.rag.input.v2` and one prior checkpoint; legacy IDs retain the seven-stage sequence and `validate.rag.input.v1` with six prior references.

Compatibility coverage reads completed old Runs and continues/retries/recovers eligible unfinished old Runs using their original order and authorization scope. Checkpoint/Claim atomicity, budget, timeout, cancellation, restart and U2 correction-to-preparation regressions pass. Web coverage shows only two new production stages and explicitly identifies legacy seven-stage progress/history.

Acceptance used isolated synthetic data, deterministic injected clients through the real local API/Web, and serial low-resource checks. The comprehensive gate passed; browser checks covered failure, cancellation, refresh, an actual interrupted process/restart, old records, and U2 correction/preparation/clipboard behavior. All task services stopped, and independent review found no actionable defect. No real/paid model, private material or Provider Key was used. There is no new table, migration or dependency. The explicit Fake Demo and historical evaluation paths remain seven-stage; retained M7, DeepSeek and Ollama results are unchanged and do not establish new two-stage model quality. Detailed results and limits are in the S1 record linked above.

## Quality model

Traditional tests prove deterministic correctness. Agent evaluations prove that probabilistic outputs remain useful, grounded, and safe. Both are required.

## Test layers

### Unit

Targets pure rules:

- state transitions;
- Claim status and provenance;
- deadline kinds and calculations;
- money normalization;
- template validation;
- risk classification;
- payload canonicalization and approval hashes;
- path containment and redaction.

No network, real clock, or production data.

### Contract

Targets JSON Schemas and provider/tool boundaries:

- valid and invalid model results;
- nonexistent source references;
- tool error shape and retryability;
- OpenAPI response compatibility;
- template schema compatibility.

### Integration

Targets SQLite, object storage, parsers, exports, and API:

- migration from empty database;
- atomic source import and dedupe;
- text segment persistence;
- case creation and evidence association;
- complete allowed/forbidden Case transition graph, optimistic concurrency, append-only history, restart recovery, and transaction rollback;
- Claim value/command bounds, complete operation-state rules, optimistic versioning, authoritative Case-scoped citations, append-only correction/confirmation history, deterministic contradictions, restart recovery, and transaction rollback;
- FTS5 capability, Case isolation, BM25 ordering, citation integrity, rebuild, and extraction/index rollback;
- Evidence Pack integrity;
- backup and restore.

Use temporary directories and databases.

### M1-05 Chinese lexical retrieval

The committed evaluation is a seven-segment synthetic Chinese dataset covering purchase date, fault photo, delivery damage, order number, billing cycle, electronic invoice, and one distractor. It contains no personal material. Six four-character substring queries each have exactly one relevant Segment.

For each query, the evaluator ranks one Case's results with the production SQL and records:

- Recall@3: fraction of queries whose relevant Segment appears in the first three results;
- MRR: mean reciprocal rank of the first relevant Segment.

Minimum acceptance is Recall@3 = 1.00 and MRR >= 0.90. On Python 3.12.13 with SQLite 3.53.1, the production trigram tokenizer measured Recall@3 = 1.00 and MRR = 1.00. A same-data `unicode61` baseline measured 0.00 and 0.00 because each uninterrupted Chinese sentence became a larger token and the four-character substring queries did not match. The product therefore requires the officially documented FTS5 trigram tokenizer instead of assuming the default tokenizer provides suitable Chinese substring recall.

Trigram indexes contiguous three-character sequences, so the public retrieval contract rejects normalized queries shorter than three characters. Queries longer than 256 characters, invalid FTS5 expressions, and limits outside 1–100 fail with typed safe errors. Sources: [SQLite FTS5 trigram tokenizer](https://sqlite.org/fts5.html#the_trigram_tokenizer), [SQLite FTS5 BM25](https://sqlite.org/fts5.html#the_bm25_function), and [SQLite FTS5 integrity/rebuild commands](https://sqlite.org/fts5.html#special_commands_to_fts5).

### M2-02 Claim provenance

Committed tests use only synthetic values and source text. They enumerate the legal confirmation/correction/rejection/source-attachment state sets, prove forbidden and stale operations leave Claim/history unchanged, and inject failures after Claim/ClaimSource writes to prove whole-transaction rollback.

Citation tests create two Cases and require the production service to reject cross-Case IDs, missing/stale hashes, invalid excerpt bounds, and authoritative text tampering. Read tests rejoin Source/SourceSegment and recompute both segment and excerpt hashes. A stable delete/reinsert of the same Segment identity preserves ClaimSource metadata; changed or missing authority fails closed.

Contradiction tests define active Claims as `candidate` and `inferred`, compare normalized value hashes within one Case/field, and prove equivalent values do not create a distinct candidate value. Confirm, correct, or reject removes that Claim from the active set and recalculates all flags deterministically. Restart tests restore Claim, citations, events, confirmations, versions, and flags from SQLite.

### M2-03 Warranty template engine

Committed tests parse the reviewed `warranty_claim@1` resource twice and require equal frozen typed projections and an equal exact-byte SHA-256 identity. Negative fixtures cover top-level and nested unknown keys, duplicate keys and list entries, explicit YAML tags, anchors/aliases, wrong scalar types, empty text, invalid field names, excessive text, depth, and total bytes. Failures expose neither template content nor machine paths.

The deterministic gap fixture treats only non-contradictory `confirmed` and `corrected` Claims as satisfying. `candidate`, `inferred`, `rejected`, contradictory, missing, and confidence-only data remain gaps. Ordered required Claims, named conditional Claims, and evidence requirements are asserted exactly. Conditional input order cannot affect output because inputs are finite sets and output follows template order.

Close conditions are alternative named outcomes: any recognized completed outcome sets `can_close`. Tests assert the Case status and version are unchanged. Migration coverage upgrades an M2-02 database to `warranty -> warranty_claim@1`, preserves Claim and FTS data, and proves restart recovery, mismatch failure, idempotency, and transaction rollback.

### M2-04 Evidence Context Builder

Committed integration tests use synthetic Chinese/English Segment text and the production M1-05 retrieval service. Repeating the same Case/query/policy/budget must produce an exactly equal frozen result. Two-Case fixtures prove both initial MATCH and adjacent expansion stay inside the SQL Case scope, while ranked-match projections preserve the production BM25/tie order.

Same-Source fixtures create overlapping windows and assert one stable sequence-ordered copy of every Segment, exact match/adjacent origins, and ascending selecting match ranks. Every citation is compared with authoritative Case, Source, Segment, sequence, page, text, and recomputed SHA-256 values. Missing/cross-Case forged hits, changed text, stale IDs, and non-continuous authoritative sequences fail with safe typed errors.

Budget tests count `len(text.encode("utf-8"))`, cover invalid total/per-item ceilings, and assert every admitted Segment is complete. A Segment that exceeds either the per-item ceiling or remaining total ceiling is omitted, `truncated` is set, and the result never exceeds its total byte budget. Policy tests explicitly allow the only current `untrusted` Source label or use an empty allowlist to return no evidence.

No-match returns a typed empty Context and does not generate an answer. Snapshot tests prove construction leaves Case, Claim, ClaimSource, Source, SourceSegment, FTS, and template binding state unchanged. Missing Case, invalid FTS syntax, missing FTS projection, closed database, and unsafe input boundaries have typed redacted coverage.

### M3-01 Provider boundary

Committed provider tests use only synthetic JSON and injected in-memory clients. They assert frozen versioned request/result/usage/metadata values, runtime protocol compatibility, explicit secret-free configuration, and a disabled default. Calling the disabled provider returns a stable non-retryable error while an independent SQLite deterministic-state fixture remains unchanged.

The fake provider accepts only an explicit scope-keyed script and must return exactly equal results for repeated requests. Missing scripts and request/response byte-limit violations fail safely. No test discovers credentials, opens a socket, or performs a live/paid model call.

The OpenAI adapter contract test captures the request passed to a fake async `responses.create` resource and asserts the configured model, separate instruction/input roles, strict named `text.format` JSON Schema, `max_output_tokens`, and `store=false`. Synthetic response objects are reduced to provider-neutral JSON and metadata. Timeout, cancellation, rate limit, network failure, retryable/non-retryable remote status, refusal, incomplete response, missing fields, invalid JSON, abnormal usage, and oversized output each have stable typed coverage. Log assertions prove prompt/input/output text, remote private errors, credentials, and path-like strings are absent.

P1-02 adds a separate offline Ollama boundary suite. An injected client captures the native payload and asserts the configured model, fixed system/stage/input messages, `stream=false`, `think=false`, temperature 0, bounded `num_predict`, and the exact JSON Schema. It covers timeout, cancellation, loopback network failure, 429, retryable/non-retryable remote status, incomplete/missing/malformed JSON, abnormal usage/model ID, request/output/raw-envelope bounds, and safe metadata-only logs without opening a socket. A standard-library transport test replaces `HTTPConnection` in memory and proves the only destination/path are `127.0.0.1:11434/api/chat`.

The separately recorded 2026-08-10 live-local smoke uses only a new synthetic one-field schema and the already-installed `qwen3:4b`: one factory-built call returned schema-valid JSON with 40 input and 10 output tokens in 5.687 seconds cold-local time. It pulled no model, read no Case database, and was followed by service shutdown. This is adapter connectivity evidence only; it is not part of CI, the fixed corpus, P1-01 deterministic cost evidence, dogfood, or seven-stage grounded-output quality.

### M3-02 Stage runner

Committed tests use only synthetic warranty markers and injected local providers. They assert the exact seven-stage order, immutable versioned registry, strict generic output envelope, empty tool allowlists, timeout/stop definitions, input-scope recomputation, checkpoint bounds/hash validation, and default 14-call/CNY 1 Case ceiling. No credential lookup, network socket, paid call, HTTP route, or UI is involved.

Happy-path coverage checkpoints all seven stages in order and compares exact per-stage/per-Case call, input/output/total token, CNY cost, repair, latency, version, and reserved counters. Missing provider cost charges the reservation; usage/currency above reservation stops for review. Invalid schema receives exactly one repair, while a second invalid result stores no candidate and makes no third call. Budget exhaustion is persisted before any provider call.

Restart tests preserve prior checkpoints, explicitly resume a failed stage with the same version/input scope, and conservatively recover a committed in-flight reservation without recalling the provider. Two independent SQLite connections using the same expected version prove at most one provider call succeeds. Cancellation, short timeout, retryable provider failure, checkpoint tampering, finalization failure rollback, and Case-scope/order/version mismatches have typed fail-closed coverage. Snapshots prove the runner does not modify Case lifecycle or any Claim, Source, SourceSegment, FTS, template, or Evidence Context authority.

### M3-03 Evidence-backed extraction

The committed M3-03 suite uses only five fixed Chinese/English synthetic warranty cases and injected fake provider outputs. It covers merchant/manufacturer, product, purchase date, order number, amount, and `serial_number`; each field is exactly one of direct `extracted`, reasoned `inferred`, or `unknown`. Direct facts must become candidate Agent Claims, inferred facts remain inferred with a cited basis, unknown creates no Claim, and no model output can manufacture confirmation or a user actor.

Authority tests compare Case, Source, Segment, sequence, page, text, current `text_sha256`, excerpt offsets, and excerpt hash. Forged hash, stale text, cross-Case authority, missing direct citation, duplicate/invalid field dispositions, and a second invalid repaired output fail closed. A trigger failure after Claim insertion proves Claim, ClaimSource, history, contradiction, Agent accounting, and checkpoint rollback together. Restart reproduces the same checkpoint and Claim provenance.

The fixed-dataset metrics and minimum thresholds are:

| Metric | Algorithm | Minimum/maximum | Current synthetic result |
|---|---|---:|---:|
| Field disposition accuracy | exact normalized value plus exact extracted/inferred/unknown disposition over 30 fields | >= 0.90 | 1.00 |
| Citation validity | non-unknown fields whose authoritative ClaimSource rejoin and hashes validate / all non-unknown fields | 1.00 | 1.00 |
| Unsupported-fact rate | non-unknown Claims without a valid source / all non-unknown Claims | 0.00 maximum | 0.00 |
| Unknown handling | expected-unknown fields stored as unknown with no Claim or source / all expected-unknown fields | 1.00 | 1.00 |

These results validate the deterministic schema/provenance pipeline with fake outputs; they do not claim accuracy for a live model, because no credentialed or paid model evaluation is authorized.

### M3-04 Evidence-backed RAG

The M3-04 suite uses only local SQLite and synthetic Chinese/English evidence. Tool tests prove the static validate-only allowlist, risk-A classification, exactly-one-call and 1-5000 ms deadlines, unchanged M2-04 Case/BM25/authority behavior, deterministic Context hashing, typed safe failures, and no mutation of Case, Claim, Source, SourceSegment, FTS, template, or Agent state.

Pipeline tests execute the complete seven-stage order. They compare every persisted reference with authoritative Case, Source, Segment, sequence, page, text SHA-256, quote offsets, and quote SHA-256; fabricated and cross-Case references receive at most one repair and then fail closed. A self-hashed forged tool projection is rejected by formal-service replay. Empty Context rejects an answered result and accepts only explicit `unknown`. A finalization-time authority change proves the Segment mutation and checkpoint both roll back, and restart returns the identical bounded checkpoint without storing Context text.

The fixed five-case evaluation algorithm builds a production Evidence Context, validates one deterministic structured candidate with the production handler, and scores exact expected status/text plus accepted authoritative references. Thresholds and current results are:

| Metric | Algorithm | Minimum/maximum | Current synthetic result |
|---|---|---:|---:|
| Answer correctness | exact answered/unknown status and expected statement text / five cases | >= 0.90 | 1.00 |
| Citation precision | accepted authoritative quote references / all emitted references | 1.00 | 1.00 |
| Unsupported-answer rate | answered cases without an accepted reference / all answered cases | 0.00 maximum | 0.00 |
| No-answer correctness | expected-unknown cases returned as unknown with no statements / all expected-unknown cases | 1.00 | 1.00 |

These are deterministic boundary/provenance results, not evidence of live-model semantic quality. No credentialed, network, or paid evaluation was performed.

### M4-01 Case list and Today view

Backend integration coverage proves empty-list behavior, Today versus terminal classification, authoritative M2-03 missing-evidence semantics, latest-activity selection, deterministic priority/activity/ID order, restart equality, read-only snapshots, thin API shape, and redacted retryable persistence failures. Fixtures contain synthetic titles, goals, Claims, and Sources only.

Vitest covers the typed list client, offline versus safe server failure, all presentation states, and human labels. TypeScript checking and the production Vite build verify the responsive workbench composition. M5-02 replaces the historic `not_available` next-action placeholder with a bounded authority projection; `deadline_risk` remains `not_calculated` because no risk policy has been reviewed.

### M4-02 Evidence review

Backend integration coverage proves deterministic template/contradiction/status ordering, strict Case isolation, exact Source/Segment/quote authority, all settled same-field values, source-free and empty queues, restart equality, read-only construction, stale/cross-Case fail-closed behavior, safe database failure, and the thin read contract. One end-to-end API test reads the queue and confirms through the existing M2-02 evidence decision endpoint, proving there is no second mutation path. Fixtures contain synthetic Claims and evidence only.

Vitest covers the typed review client, decision routes, offline failure, loading/empty/error/populated states, shortcut mapping, bounded next navigation, and JSON correction parsing. Real Chromium acceptance on synthetic data verifies the desktop side-by-side source/candidate/settled layout, quote highlighting, contradiction display, evidence-backed `Alt+1` confirmation, source-free explicit-user confirmation, persistent success feedback, and a 390 px single-column layout with no horizontal overflow. The only browser-console error observed was the existing missing favicon response; no feature error occurred.

### M4-03 Timeline and recovery

Backend integration coverage proves empty/missing Case behavior, strict Case scope, every admitted authority family, UTC/kind/ID ordering, exact status/version/actor/reason references, current extraction and Agent snapshots, read-only repeatability, restart equality, thin API recovery, and redacted errors. Cross-Case Artifact parents, Segment-count corruption, missing Claim events, missing Case transitions, non-contiguous versions, stale final state, and closed-database reads fail closed. Fixtures are synthetic only.

Vitest covers the typed Timeline client, encoded Case route, offline/safe-server errors, all presentation states, human labels, status transitions, and shortened authority display. Real Chromium acceptance verifies the six-event desktop spine, Source/Artifact/extraction/transition/Claim ordering, actor/reason/source visibility, identical restoration after page refresh, explicit M5 approval/communication unavailability, and a 390 px single-column layout with no horizontal overflow. The browser console reported zero errors and zero warnings.

### M5-01 Deadline engine

Backend integration coverage enumerates all three immutable kinds and five statuses; canonical UTC-instant/local-date/IANA-timezone values; rejection of naive instants, invalid zones, daylight-saving gaps, and unresolved overlaps; and deterministic fold handling. Fixtures contain synthetic date Claims only.

Explicit coverage accepts only current same-Case, non-contradictory confirmed/corrected Claims and asserts the exact Claim ID/version/value hash/date kind, due value, confidence, definition hash, and write/read revalidation. Missing, candidate, contradictory, cross-Case, stale-version/hash, changed authority, and non-date Claims fail closed.

Calculated coverage repeats identical basis/rule/timezone/parameter inputs and compares exact results across DST. Unknown/old rules and invalid parameters fail safely. The persisted rule ID/version/parameter hash and ordered basis remain auditable. Suggested coverage requires no basis or rule and asserts the personal-preference warning.

Independent SQLite connections prove competing status commands allow at most one success and increment by exactly one. Trigger injection after Deadline/basis writes proves Deadline, basis, and history roll back together; restart reproduces equal projections. Thin API tests cover all three response shapes, optimistic errors, invalid zones/basis, and redacted persistence failures. Timeline tests admit only target-Case committed Deadline events in deterministic UTC/kind/ID order.

Vitest covers the encoded Case client, offline/safe-server failures, unselected/loading/empty/offline/failure/populated states, visible kind/date-kind/status/uncertainty distinctions, local-date display, and the explicit personal-preference warning. TypeScript and production build validate the responsive one-column mobile layout. M5-01 performs no live model, credential, connector, ICS, calendar, or external-side-effect test.

### M5-02 Action planner

Backend integration coverage must enumerate the closed kinds/statuses/risk levels, kind-specific payload bounds, template forbidden actions, and every legal/illegal transition. It must prove that only current same-Case non-contradictory confirmed/corrected Claims satisfy non-collection requirements, while collect-evidence targets a current missing Claim/evidence key. Candidate, inferred, rejected, contradictory, stale-version/hash, missing, and cross-Case authority fail closed.

Dependency tests cover missing/cross-Case/self/duplicate/cyclic edges, stable topological order, readable-but-non-executable blocked bindings after a legitimate upstream version advance, explicit version-refresh revalidation, and completion propagation. Deadline tests bind ID/version/definition hash and preserve UTC-instant/local-date/timezone projection. Completion tests require and revalidate same-Case Claim/Source/Artifact hashes. Independent connections prove one optimistic winner; trigger injection proves Action, requirements, dependencies, Deadline link, blockers, evidence, and history roll back together; restart returns equal projections.

Thin API and Web tests cover create/list/revise/revalidate/start/complete/fail/cancel; authoritative `next_action`; deterministic communication drafts; loading/empty/offline/failure/blocked/ready/running/approved/terminal states; non-JSON local gateway failures mapped to offline without reclassifying typed API failures; and safe redacted errors. Timeline admits only committed target-Case Action events. Approval and ICS remain separately owned by M5-03 and no Action command creates an external effect.

### M5-03 Approval gate and ICS export

Approval coverage binds exact Action/Approval versions, payload hash, visible summary/hash, local actor label, risk, and contiguous event-type/status history. It tests pending/approved/rejected/expired/invalidated states; stale, duplicate, cross-Case, illegal-state and independent-connection races; Risk-A bypass, Risk-B/C gate enforcement, Risk-C non-execution, Risk-D rejection; payload-revision invalidation; approval/Action atomic rollback; restart recovery; and committed Timeline projection. The API regression also proves that approving an upstream Action returns committed success while a downstream exact-version dependency remains readable and blocked, can be explicitly rebound, and recovers identically after restart. Timeline rejects legal enum values that do not match the persisted Action or Approval status transition. No test treats actor as authenticated identity.

ICS coverage exports only a same-Case confirmed Deadline and rejects non-confirmed, cross-Case, missing, stale, or corrupt authority. Golden bytes cover UTC instant, local all-day date, retained IANA timezone metadata, deterministic UID/DTSTAMP/order, bounded ASCII 75-octet folding, CRLF, safe filename, restart identity, and identical repeated output. API/Web tests prove local download only, safe failure, and absence of database, Artifact, email, connector, or system-calendar writes.

Real Chromium M5 acceptance uses a synthetic local Case and verifies all three Deadline kinds, four Action kinds, pending and approved gates, Risk-C non-execution, confirmed-only ICS download, committed Action/Approval Timeline events, refresh recovery, and 1440 px / 390 px responsive rendering. At 390 px, document width remains within the viewport; console errors and warnings are zero.

### M6-01 Evidence Pack

Backend integration coverage builds a synthetic Case containing a retained Source, confirmed Claims, an explicit confirmed Deadline, a communication Action, and Approval authority. It proves four-layer membership, deterministic/idempotent bytes, retained Artifact metadata, restart/latest-pack recovery, API download, and independent manifest/checksum verification. Mutation tests cover one-byte archive changes, traversal paths, stale source bytes, and injected Artifact-write failure with complete metadata/file rollback. Safe API failures are checked for content, SQL, credential, and machine-path redaction.

Vitest covers encoded Case-scoped latest/create routes, absence, offline and typed failure behavior, every Evidence Pack panel state, format/hash/size presentation, and retained download URLs. TypeScript checking and the production build cover the responsive four-layer panel. Real Chromium acceptance uses a fictional appliance-warranty Case, generates/downloads a pack, refreshes to recover the retained Artifact, independently verifies the ZIP, and checks desktop/mobile overflow and console output. M6-01 uses synthetic local data only and performs no backup/restore, live model, credential, connector, or network call.

### M6-02 Backup and restore

Integration coverage creates a fresh synthetic headphones-warranty Case with imported Source, five confirmed Claims, confirmed explicit Deadline, Risk-B Action, approved Approval, Timeline, and retained Evidence Pack. It proves the online snapshot includes committed concurrent state but excludes an open transaction; manifest/checksum and complete data-root reconciliation; restart-persisted validation; no target write during validate; clean-target atomic restore; and post-restore reads through Case, Claim, Deadline, Action, Approval, Timeline, and Evidence Pack services.

Hostile coverage rejects missing/extra/changed objects; traversal, absolute, drive, UNC, backslash, control-character, duplicate/case-collision and symbolic-link members; byte and size tampering; unknown format; future or checksum-drifted migrations; SQLite corruption and foreign-key violations; stale validation; invalid confirmation; and existing targets without modifying their marker. Fault injection proves backup publication and restore staging leave no partial output. Pre-migration tests prove no pending migration means no backup, backup failure prevents migration, and a verified backup precedes a synthetic test-only migration without adding a business migration. Web tests cover latest/create/upload/apply transport, explicit confirmation, and loading, empty, selected, validating, confirmation, restoring, success, offline, and failure states. All fixtures are synthetic and local.

### M7-01 Additional templates

Focused integration coverage loads the four reviewed resources twice, compares immutable identity/content hashes, and asserts distinct ordered required Claims, finite conditional fields, evidence requirements, close conditions, and forbidden-action identifiers. Each post-warranty Case type must persist its exact registry binding through API restart; unknown type/version/mismatched resource input fails closed.

Gap tests prove candidate, inferred, rejected, contradictory, cross-Case, and canonical-value-hash-invalid Claims cannot satisfy either new template. Confirmed/corrected same-Case Claims use the unchanged Claim reader, and close-condition evaluation remains pure. The Action planner consumes each new template's existing required-Claim contract without a per-type fork.

Fresh synthetic return/refund, reimbursement, and subscription-cancellation Cases confirm all requirements, produce local communication Actions and deterministic Evidence Packs, enter one whole-authority Backup, restore to a clean runtime, and recover equal Case/template gaps, Action IDs, and retained pack downloads. The four fixed subscription-cancellation fixtures additionally cover source-backed Claim provenance, missing-reference gaps, all three Deadline semantics, Risk C/Risk A Approval boundaries, Timeline, restart, and Backup/Restore. No project-development workflow is used as a Case fixture. Wheel inspection requires all four YAML resources. Web tests cover catalog loading, empty, offline, safe failure, populated identity presentation, and explicit selector transport.

### M7-02 Thirty-case evaluation

The committed `m7-v1` corpus contains exactly 30 generated Cases with the required 8 return/refund, 8 warranty, 7 reimbursement, 4 subscription-cancellation, and 3 travel/logistics distribution. Every directory has the documented input/expected structure plus strict metadata and a separate recorded observation. The generator and report checker prove byte stability; the evaluator rejects membership/schema/scope collisions, unsafe paths, stale/cross-Case/hash-invalid citations, invalid Deadline rules/date semantics, unsafe Action boundaries, and zero denominators.

The scoring contract uses Unicode NFKC, collapsed whitespace, and case-folding for strings; structured values are recursively canonicalized. Each critical field is exact, acceptable, appropriately uncertain, or unsupported/wrong. Evidence validity recomputes the complete Segment and quoted slice SHA-256. Deadline safety validates explicit basis, calculated `calendar_days_after_basis@1` basis/rule/result, suggested non-obligation semantics, local dates, UTC instants, and IANA timezones. Action quality records the documented six 0–2 rubric values and separately counts safety failures.

Current deterministic recorded-output results are 148/150 passing critical fields (including acceptable and appropriately uncertain cases plus two explicit unsupported outputs), 144/144 valid citations, 30/30 safe Deadline records, 11.60/12 average Action quality, and zero Action safety failures. These are contract/provenance results over recorded synthetic outputs, not live-model quality evidence. The four subscription-cancellation Cases now use a reviewed product template, so 27 Cases are product-processed and only 3 travel/logistics Cases remain evaluation-only. Product-processing coverage is 27/30 at the unchanged threshold, and the aggregate evaluation report is `PASS`. Canonical reports are `docs/evaluation/m7-v1-results.json` and `.md`.

### Browser acceptance

Targets user-observable workflows:

- create case;
- import synthetic evidence;
- review and confirm claims;
- understand a missing-evidence blocker;
- approve a local reversible action;
- export and download an Evidence Pack;
- recover after page refresh.

### Security

- prompt-injection documents;
- path traversal filenames;
- oversized/malformed inputs;
- approval payload mutation;
- duplicate execution;
- secret/log redaction;
- unexpected host/origin;
- stale version, forbidden lifecycle edge, and history-write rollback;
- forged/cross-Case/stale Claim sources, source-free evidence confirmation, stale Claim versions, forbidden Claim operations, and Claim/event/confirmation rollback;
- forged/cross-Case/stale extraction authority, unsupported facts, fabricated confirmation, repair exhaustion, and Claim/checkpoint atomic rollback;
- one-byte export tampering.

## Evaluation dataset

All committed cases are synthetic or irreversibly redacted. Each contains:

```text
case-id/
├─ input/
├─ expected/claims.json
├─ expected/gaps.json
├─ expected/deadlines.json
├─ expected/actions.json
└─ metadata.yaml
```

Final MVP distribution:

| Type | Count |
|---|---:|
| Return/refund | 8 |
| Warranty | 8 |
| Reimbursement | 7 |
| Subscription cancellation | 4 |
| Travel/logistics | 3 |

## Evaluation metrics

### Critical field score

For merchant, amount, purchase/event date, order/reference number, and desired outcome:

- exact normalized match;
- acceptable alternate match;
- explicitly uncertain/unknown;
- unsupported or wrong.

The target is at least 90% exact/acceptable/appropriately uncertain, with no unsupported critical fact accepted as confirmed.

### Evidence validity

- 100% of factual Agent claims reference existing segments;
- quote hash matches current segment content;
- cited segment actually supports or contradicts the claim as labeled.

### Deadline safety

- 100% kind classification for explicit/calculated/suggested fixtures;
- zero suggested dates presented as external obligations;
- calculation rule and basis claims always present for calculated dates.

### Action quality

Human rubric, 0–2 each:

- addresses the stated goal;
- uses confirmed facts;
- orders prerequisites correctly;
- identifies blockers;
- respects risk/approval boundaries;
- communication draft is concise and truthful.

Target: average at least 10/12 and no safety-boundary failure.

### Operational metrics

- cost per case;
- total tokens;
- time to first reviewable result;
- number of model calls and repairs;
- percent resumed from checkpoint after failure;
- percent requiring manual correction.

## Regression policy

Any prompt, model, schema, template, or extraction change must run the same relevant evaluation subset. A change cannot be accepted solely because it reduces calls, tokens, or latency. It must maintain or improve outcome quality and safety.

## CI gates

Every push and pull request runs:

```text
Python: lock sync -> Ruff -> format check -> Mypy -> pytest
Web: lock install -> type check -> Vitest -> Vite production build
```

Live model, email, calendar, and browser connector tests are excluded from normal CI. They run manually with test accounts and explicit credentials.

## M7 release-evidence separation

The M7 Portfolio MVP uses three required evidence lanes and one optional operational lane:

1. deterministic repository verification: lint, format, types, backend/Web tests, build, and corpus/report checks; a published release additionally requires exact-commit CI;
2. Windows package acceptance: clean start, existing-data restart, one loopback origin, invalid path, port conflict, offline operation, migration/pre-migration Backup failure, safe exit, and uninstall-without-data-deletion;
3. one short end-to-end synthetic Case through Claim, Deadline, Action, Approval, ICS, Timeline, Evidence Pack, Backup, exit, and restart;
4. optional operational validation: seven elapsed days of private dogfood with at least three completed real Cases.

The required product-processing threshold remains at least 27/30 formally supported fixed evaluation Cases and is currently 27/30. These lanes are not interchangeable. A synthetic Case cannot count as dogfood, elapsed time cannot be simulated, packaging cannot increase product support, evaluation-only types cannot satisfy the product-processing threshold, and recorded fake-provider output cannot establish live-model quality. Private dogfood evidence may retain only non-sensitive aggregate counts and outcomes; all source material, runtime data, exports, screenshots, detailed logs, and local paths stay outside Git.

The optional procedure and permitted aggregate schema are in `docs/evaluation/m7-dogfood-protocol.md`; the current `NOT_RUN` record is in `docs/evaluation/m7-dogfood-results.md`. Do not populate it from automated tests or the short synthetic acceptance, and do not infer real-use readiness from its exclusion from the Portfolio MVP gate.

The deterministic browser-loop regression adds a fresh synthetic daily-use Case that selects an exact SourceSegment quote, creates and evidence-confirms a template-bound candidate Claim, creates/confirms a personal follow-up date, downloads ICS, creates a Risk-B collect-evidence Action, approves the exact Action version, starts/completes it with same-Case Source authority, generates/downloads an Evidence Pack, creates a Backup, and transitions the Case to archived. A process restart must recover every authority and the complete Timeline; a 390 px Chromium check must have no horizontal overflow. This remains lane 3 evidence and never counts as optional dogfood.

## P1-01 deterministic Agent Demo verification

P1-01 adds no live-model quality claim. CI and browser acceptance use only server-owned `portfolio_agent_demo.v1` Fake Provider scripts and synthetic warranty material; other Case types must report unsupported until they have a reviewed extraction Schema. Focused coverage requires: default disabled capability without product regression; unsupported type rejection; queued create and seven-stage success; candidate and inferred Claim provenance; real tool match to `answered`; real no-match to `unknown`; duplicate/cancel rejection; process-interrupted recovery followed by exactly one retry; non-retryable failure; restart recovery; budget/timeout/concurrency regression from the existing StageRunner suite; cross-Case, stale Segment/citation, and checkpoint-hash tamper rejection; and Web loading/offline/failure/running/retry/recovery/completed states.

The acceptance trace may retain only bounded status, version, usage, safe error code, fixed tool name/count/result, Claim projection, answer statement, and citation metadata. It must not capture a real source body, runtime database, full Context/checkpoint, raw provider response, credential, SQL, object path, private error, screenshot, Evidence Pack, or Backup. P1-01 leaves the fixed M7 corpus/report, 27/30 coverage, and dogfood `NOT_RUN` unchanged.

## P1-R1 deterministic Fake Provider release evidence

`test_deterministic_agent_demo_usage_and_local_latency_are_bounded` creates five fresh synthetic warranty Cases and blocks non-loopback parent-process socket connections. Each measured `execute` command completes the existing seven-stage path with exactly 7 calls, 70 input tokens, 35 output tokens, 105 total tokens, and CNY 0. The existing Case budget remains 14 calls, 28,000 input tokens, 14,000 output tokens, 42,000 total tokens, and CNY 1.

On the 2026-08-09 local run, five executions took 0.129110 seconds in aggregate, with a 0.025807-second median and 0.029702-second maximum. The regression bound is 5 seconds per run and 25 seconds aggregate. These are deliberately generous fail-closed test ceilings, not service-level objectives. The measurement covers deterministic local Python, SQLite, retrieval, and Fake Provider work only; it says nothing about a live/local model, network latency, paid inference, or production load. The complete audit record is `docs/evaluation/p1-r1-release-evidence.md`.

`test_agent_demo_openapi_documents_the_exact_safe_transport_contract` generates OpenAPI read-only and asserts all five P1 path items / six operations, exact success statuses, absence of request bodies, `AgentDemoResponse`, and 404/409/422/503 references to `ApiErrorResponse`. It also asserts the closed envelope/detail fields; the disabled-capability integration path validates a real runtime error against the same model. This is transport metadata only and does not change exception handling or Agent behavior.

## P1-03 controlled Ollama Agent evaluation

P1-03 is a manual, local-only evidence lane. The command below creates two fresh temporary synthetic warranty Cases, invokes the existing fixed-loopback native Ollama adapter directly through an evaluation-only injection seam, and deletes the temporary authority when complete. It adds no HTTP/Web route and is excluded from CI:

```powershell
Set-Location .\apps\api
uv run python -m lifecase.evaluation.ollama_agent --model <installed-model-id> --runs 2 --per-run-seconds 300
```

The default gate requires every run to finish the seven registered stages and survive SQLite restart; exact Claim value/disposition accuracy must be at least 0.90; every checked Claim/answer citation must be valid; unsupported Claims must be zero; the matched Case must answer with authority and the no-match Case must return `unknown` without citations; and every run plus the aggregate must stay within the declared time bounds. The safe report retains only aggregate status, stable failure codes, calls, tokens, metric counts, and bounded timings. It excludes bodies, identifiers, database/object paths, checkpoints, raw provider output, and detailed logs.

Offline regression uses injected deterministic providers and no live socket. One test proves the full two-Case gate, including 14 fixed calls, answered/unknown, Claims, citations, recovery, report redaction, and an empty diagnostic category set. Strict failure tests prove checkpoint-shape and schema-valid business-authority classification, failure continuation, one-repair enforcement, stable `agent_schema_invalid`, no seven-stage claim, and recovery. Unit coverage exercises the closed non-JSON, envelope, stage const, checkpoint shape, type, enum, additional-field, length, pattern, numeric-boundary, and business-validation taxonomy without retaining candidate values or paths. The classifier observes provider-neutral candidates only and does not replace the canonical validator.

The 2026-08-11 live-local `qwen2.5:3b` run failed the overall gate. Both fresh Cases consumed the initial call plus one repair and then failed strict local validation with `agent_schema_invalid`; zero runs completed seven stages, so no Claim/citation or answered/unknown quality result is claimed. Recovery passed 2/2. Four total calls used 1,826 input and 9,082 output tokens; elapsed times were 175.207385 and 150.092068 seconds, with 325.299453 seconds aggregate, 162.649727 seconds median, and 175.207385 seconds maximum. The time bounds passed, but timing alone cannot waive the quality gates. This is one machine/model/configuration result and not a realtime-model performance claim. The safe evidence record is `docs/evaluation/p1-03-ollama-agent-evidence.md`.

The 2026-08-12 low-resource diagnostic rerun used only the already-installed `qwen3:4b`, CPU-only inference, one parallel request, `Idle` process priority, and 2/20 logical-processor affinity. Both fresh Cases reached the unchanged 120-second first-stage timeout before a provider-neutral candidate returned. The safe category set is empty because nothing reached the classifier, not because Schema validity was established. Recovery passed 2/2, but seven-stage completion was 0/2, Claims were 0/12, citations were 0/0, answered/unknown quality remained unverified, and the overall gate remained `FAIL`. Two calls were charged 16,000 conservative reservation tokens rather than provider-reported usage. Ollama was stopped after the run.

## P1-04 live-ready product verification

P1-04 CI/offline verification must never construct a real SDK client, read a credential, open a model socket or incur cost. Focused backend coverage requires:

- generated OpenAPI exposes only the six fixed `/live-agent` operations; create accepts exactly preview hash plus read-only confirmation and all commands accept no body;
- default disabled makes zero calls even when a client object is injected; missing client/model, unsupported Case, false confirmation, stale preview and Fake/live mode conflicts fail closed;
- the preview's Case type, exact extraction/validate-RAG Segment union, UTF-8 byte count, model and hard Case budget come only from server authority, including a regression where validate retrieves a Segment beyond extraction's first-32 limit;
- forbidden browser fields for key, host, model, Provider output, checkpoint, tool and budget are rejected;
- an injected in-memory Responses client passes through the actual OpenAI adapter payload (`store=false`, strict JSON Schema, no tools), completes all seven product stages and creates only candidate/inferred Claims plus answered/unknown output;
- existing citation, authority, one-repair, cancellation, interruption recovery, single retry and pre-call budget tests remain applicable;
- Deadline and Action reads remain unchanged, and SQLite/log/API audits exclude synthetic key, private host, raw-response-only and machine-path markers.

Web TypeScript/Vitest coverage proves the browser sends exactly the two authorization fields once and bodyless fixed commands thereafter. The UI must display Provider/model, Case type, Segment count/UTF-8 bytes, maximum calls/input/output tokens/CNY cost and the no-external-action notice before enabling authorization. P1-01 API, tests, labels and UI remain separate regression coverage.

This offline lane establishes **live-ready**, not live-model acceptance. A later manual acceptance must use one fixed server model, 2–5 temporary synthetic reviewed warranty Cases, an explicit aggregate cost cap and immediate stop conditions. It must separately record credentials/data-control review, model/version, calls/tokens/cost, seven-stage completion, field/disposition accuracy, citation validity, answered/unknown, recovery and safe failure codes. It must retain no personal evidence, prompts, raw responses, credential material, paths or private errors and cannot count as dogfood or exact-commit CI.

## P1-05A Multi-provider Registry verification

P1-05A remains an offline lane. Focused coverage requires immutable secret-free profiles with fixed endpoints/models and complete capability fields; rejection of arbitrary Groq models and configuration fields for endpoint/host/key; missing Groq ZDR confirmation or injected client failing before any call; exact strict Chat payload mapping without streaming, tools, citations, reasoning or unsupported `store`; canonical result/usage and metadata-only logging; malformed/incomplete/oversized/timeout/network/rate-limit/remote failures reduced to safe provider-neutral errors; migration 12 preserving old stage rows while admitting only `groq`; and an injected in-memory Groq client completing the same seven-stage API path with candidate/inferred Claims, answered/unknown output and no Deadline/Action mutation. OpenAI, Fake and Ollama regressions remain independent and unchanged.

This proves adapter mapping and Registry composition only. It does not prove a real Groq credential, organization ZDR setting, free-tier availability, model quality, latency, cost, remote retention behavior or production readiness. No live provider call belongs in CI.

## P1-05B Mistral Provider verification

P1-05B remains an offline product lane. Focused coverage requires the fixed `https://api.mistral.ai/v1/chat/completions` endpoint and non-moving `mistral-small-2603`; rejection of a moving or browser-selected model, arbitrary endpoint, missing injected client and missing organization ZDR confirmation before any call; exact strict `json_schema`, `max_tokens`, temperature 0, `stream=false`, `tool_choice=none`, no-tools/no-store payload mapping; canonical JSON, exact response model and required usage validation; safe metadata-only logs; migration 13 row preservation and closed identity; and an injected in-memory Mistral client completing the unchanged seven-stage API path with candidate/inferred Claims, answered/unknown output and no Deadline/Action mutation.

The separate 2026-08-12 live smoke used only a synthetic one-field schema with the moving `mistral-small-latest` alias and observed HTTP 200, 17 input / 7 output / 24 total tokens and 803 ms. It is not part of deterministic CI and proves neither the fixed product model nor ZDR, StageRunner, quality, cost, persistence, recovery or retention. The exposed test credential must be revoked before any later acceptance.

The P1-05B final local deterministic gate passes 478 backend tests and 48 Web tests, plus Ruff over 166 API files, format, strict Mypy over 122 source files, Web TypeScript, production build, unchanged M7 corpus/report bytes, Markdown links, diff checks and the sensitive/dependency/artifact/ignored-file audit. The one existing Starlette TestClient transition warning remains.

## P1-05C safe field diagnostics and contract verification

P1-05C keeps structural Schema categories separate from canonical business validation. The extraction diagnostic must emit only `extract.<fixed-reviewed-field-or-checkpoint>.<closed-failure-class>`, never a candidate value, JSON path, source/Case identifier, hash, checkpoint, evidence body, raw response, credential, private remote error, or machine path. Tests cover origin contract, wrong Segment-hash authority, one-repair reporting, run counts, report serialization/redaction, and the deterministic success path with no diagnostic.

Extraction regression additionally proves the complete six-field prompt contract, valid Fake exact-quote preservation, authoritative full-Segment fallback for invalid model quote offsets/hash, normalized checkpoint persistence, and fail-closed wrong source/Segment/hash behavior. Focused StageRunner/RAG/API/Registry tests must still prove candidate/inferred provenance, citations, answered/unknown, cancellation, interrupted recovery, one retry, and unchanged budgets without a live socket.

The authorized Large 3 lane used two temporary synthetic warranty Cases and retained no raw provider response or evidence body. It first isolated `extract.merchant_or_manufacturer.quote_authority`, which motivated the prompt/normalization correction. Final post-correction acceptance remains `FAIL / NOT ESTABLISHED`: later requests saw remote protocol failure and one 120-second transport timeout, and the last bounded diagnostic returned no candidate. Medium 3.5 was not called because the required stable Large 3 evidence did not exist. Reservation tokens from failed calls are not reported as provider usage or cost. Full safe evidence is in `docs/evaluation/p1-05c-mistral-agent-evidence.md`.

The final local deterministic gate passes Ruff lint and format over 166 API files, strict Mypy over 122 source files, 482 Pytest tests, Web TypeScript, 13 Vitest files / 48 tests, the Vite production build, unchanged M7 corpus/report bytes, Markdown local links, diff checks, and sensitive/dependency/artifact/ignored-file audits. The existing Starlette TestClient transition warning remains. These offline results do not turn the blocked Large 3 lane into live-model acceptance.

## P1-05D DeepSeek synthetic acceptance and product verification

Offline tests require the exact official endpoint and `deepseek-v4-flash`; `json_object`,
`thinking=disabled`, `stream=false`, `tool_choice=none`, no tools, bounded input/output, exact model
identity and required usage; local canonical Schema/business validation and the existing one repair;
detached raw parse/remote failures; no prompt/output/private body in logs; pre-call USD 0.02 cost
reservation; a complete two-Case deterministic pass; and immediate stop after a first-Case Schema
failure. Registry/environment/API tests require the fixed endpoint/model, injected client and
server data-use confirmation, and must prove that the unchanged browser authorization body cannot
submit Provider settings, credentials, tools, checkpoints or budgets.

The corrected live lane passed one answered and one unknown Case: 14 calls, 8,223 input and 3,535
output tokens, USD 0.00205321 nominal cost, 12/12 correct Claims, 13/13 valid citations, two
seven-stage answers and two recovery checks. Validate now normalizes derived quote metadata only
after the authoritative same-Case Segment tuple matches; wrong authority remains fail-closed. See
`docs/evaluation/p1-05d-deepseek-agent-evidence.md`.

Launcher tests additionally prove that synthetic-data confirmation is required before the hidden
credential prompt, the key is prompted exactly once, Uvicorn binds only loopback with one worker
and no access log, configuration/repr excludes the key, and the fixed client enters `create_app`
only through process injection. A real synthetic product run first isolated
`finish_reason_length` at the old 1,000-output-Token limit, then passed all seven stages after a
DeepSeek-only 2,000-per-call / 28,000-per-Case adjustment; other profiles retain their budgets.

The final deterministic gate passes Ruff lint and format over 171 API files, strict Mypy over 125
source files, 498 Pytest tests, Web TypeScript, 13 Vitest files / 48 tests, and the Vite production
build. The existing Starlette TestClient transition warning remains. The gate itself opens no model
socket and incurs no provider cost.

## P1-06 repeatable demo verification

Tests first cover the dedicated runtime marker, dangerous/unmarked/tampered reset refusal, Web-build
and port checks before credential input, loopback launch, health-gated browser opening, two fixed
reviewed warranty seeds, ordered answered/unknown execution, safe aggregation and immediate stop on
external-authority failure. Web presenter tests pin fixed Provider names, seven-stage completion and
clear answered/unknown labels; existing Fake Demo/API request tests remain unchanged.

The real product acceptance on 2026-08-12 used the hidden-input launcher and product HTTP routes.
After correcting one safe seed-goal mismatch, the bounded rerun passed answered then unknown: both
completed 7/7 stages with six candidate/inferred Claims and seven calls; answered used 4,306 input /
1,963 output Tokens in 17.100497 seconds with one citation, while unknown used 3,956 input / 1,656
output Tokens in 16.532724 seconds with zero citations. Both had no safety code, Deadline, Action or
external effect. A separate marked runtime containing SQLite/data/uploads was successfully removed
by the reset script without touching the active demo runtime.

Real Chromium inspection at 1440 × 1000 verified the fixed DeepSeek/model badges, exact 1 Segment /
171 UTF-8-byte preview, budget, 7/7 rail, candidate/inferred values, answered citation and unknown
zero-citation state. Six element screenshots under `docs/assets/p1-06-demo/` contain no credential,
machine path or personal data. Expected 404s for absent Backup/Evidence Pack and the intentionally
separate Fake-run conflict remain visible as handled HTTP failures rather than Live Agent failure.

## P1-07 evidence-dossier UI verification

Presentation tests use only typed in-memory synthetic `LiveAgentResult` values. They pin the result-first
answered/unknown order, readable Chinese Claim fields, formatted money without raw JSON, zero-or-more
citation state, compact 7/7 progress, closed runtime details, and the real-before-Fake workspace order.
Existing API transport tests continue to pin the authorization body and bodyless commands; the Fake Demo
component, labels, request behavior and regression suite remain unchanged.

Real Chromium inspection reused the already persisted P1-06 synthetic results and made no Provider call.
At 1440×900 and 1024×768, the Agent view showed Case identity, real/synthetic/read-only boundary,
answered or explicit evidence-insufficient conclusion, citation state and 7/7 completion without the former
budget-card wall. At 390×844 the same hierarchy remained single-column without horizontal overflow. Five
reviewed screenshots under `docs/assets/p1-07-demo/` contain no credential, machine path or personal data.
The existing handled 404s for absent Backup/Evidence Pack and separate Fake-run conflict remain outside the
Live Agent result and are not reclassified as P1-07 failures.

The 2026-08-13 publication closeout reran the complete deterministic repository gate without a live model:
Ruff lint and format covered 173 API files, strict Mypy covered 126 source files, Pytest passed 505 tests,
Web TypeScript passed, Vitest passed 16 files / 55 tests, and the Vite production build completed. The fixed
M7 corpus and canonical reports remained byte-identical; 34 tracked Markdown files had no broken local link;
diff, exposed-credential-pattern and ignored AI-coordination-file audits were clean. The existing Starlette
TestClient/httpx2 transition warning remains the only warning.

## P1-08 workflow-simplification verification

Web tests first pin three UI-only contracts: no selected Case means the Agent/review/deadline/action/export/
backup/timeline panels are absent; the five-step Case workflow mounts only its active area; and the Fake Demo,
Evidence Pack and Backup/Restore children remain unmounted while their disclosures are closed. Existing next-step
tests pin Overview for an empty Case and Evidence Review after the first Source. No API request-body assertion,
Provider fixture or Fake command behavior was changed.

Real Chromium used one fresh synthetic warranty Case with the Provider disabled. At 1440×900, 1024×768 and
390×844, the selected workflow had no document-level horizontal overflow; mobile keeps the five-step rail inside
its own scroll container. Switching through all five areas restored only that area's local records. A clean final
session reported zero console errors/warnings; opening Records requested Timeline only, while closed Evidence Pack
and Backup/Restore emitted no handled 404. The browser did not expand Fake Demo or authorize/execute Live Agent,
so no real, paid or Fake model run occurred.

The final local UI-only gate passed Web TypeScript, 18 Vitest files / 59 tests, the Vite production build,
29 focused Live/Fake/demo-readiness Pytest tests and `git diff --check`. The existing Starlette TestClient/httpx2
transition warning remains; no backend source, API contract, dependency or lockfile changed.

## P1-09A formal workflow-density verification

Web regressions pin the repaired `.case-start-panel` creation target, formal next-step action labels, one compact
selected-Case control region, the canonical five Chinese area names, single-area mounting and the closed selected-Case
docket. Density tests prove that manual fact, date and action forms remain unmounted until their disclosures open;
existing Evidence Pack, Backup/Restore and Fake Demo lazy-mount coverage remains unchanged.

Real Chromium used a separate synthetic local data root with the application Provider, Live Agent and Fake Demo
disabled. It verified empty-docket creation targeting, Case creation, return, Case switching with the shared docket,
hash recovery, all five areas, closed runtime metadata, disabled-capability result-first Agent presentation and no
hidden export/backup requests. At 1440×900 and 1024×768 the control and five-area navigation remain in the first
viewport. At 390×844 the compact area selector replaces the rail and document width remains within the viewport.
The selected Case title appears once, one area mounts, the docket stays closed by default and empty states measure
approximately 180 px. All observed feature requests returned success; console errors and warnings were zero.

The final local gate passed Web TypeScript, 20 Vitest files / 65 tests, the Vite production build and
`git diff --check`. No real, paid or Fake model ran, and no backend source, API contract, Provider, authority,
persistence, dependency, migration, tag or Release changed.

## P1-10 AI interface catalog verification

Backend integration coverage pins the five-item Provider order, current selection/runtime state, distinct
offline-injection, synthetic-acceptance and failed-quality-gate labels, and absence of credential, endpoint or
key-like content. A configured DeepSeek profile without an injected client must report `unavailable` and make
no remote call. The pre-existing system capability route remains in the regression set.

Web tests pin the one-request read client, all five formal Provider labels, safe evidence wording and absence
of input/select configuration controls. The closed `AI 接口与本地模型` disclosure does not mount the client;
the opened disclosure does. TypeScript, production build and browser viewport checks remain mandatory. All
P1-10 verification is offline unless a separately authorized, newly issued credential is entered through the
existing hidden launcher; chat-exposed credentials are never test inputs.

The 2026-08-13 local gate passed Ruff lint/format, strict Mypy over 126 source files, all 508 Pytest tests,
Web TypeScript, 22 Vitest files / 68 tests, the Vite production build and `git diff --check`. Real browser
inspection used a fresh synthetic washing-machine warranty Case with one TXT Segment and the Provider/Live/Fake
capabilities disabled. At 1440×900, 1024×768 and 390×844 the catalog remained readable; 1024 and 390 document
widths matched their client widths, the application console had zero warnings/errors, port 8765 was not listening,
and no real, paid, local or Fake model ran.

## P1-11 persistent AI settings verification

Backend coverage uses an injected memory credential store and fake client factory. It proves that save responses never contain the Key, an application restart restores the selected fixed profile, the capability catalog reports the effective runtime, a missing local-intent header is rejected, unconfirmed external processing is rejected without echoing input, and clear removes the stored credential. Migration coverage preserves existing stage rows while admitting only `gemini` and `anthropic` in addition to the prior closed Provider set.

Gemini and Anthropic contract tests are offline: captured calls assert official fixed endpoints, structured JSON request shapes, no streaming/tools, bounded output and provider-neutral usage mapping. Standard-library HTTP client construction is exercised without opening a model socket. Existing OpenAI/Groq/Mistral/DeepSeek Registry and seven-stage tests remain in the regression lane.

Web tests cover the separate global drawer, all six cloud labels plus evaluation-only Ollama, password input, fixed-model selection, explicit confirmation, save/clear request headers and absence of a Key in response types. The 2026-08-13 browser check used the Provider-disabled root page at 1440×900, 1024×768 and 390×844. Document width did not exceed the viewport, the mobile form used one column, and console warnings/errors were zero.

One deliberately invalid synthetic string verified the real Windows persistence boundary without contacting a Provider: save succeeded, the API process was fully restarted, the fixed DeepSeek profile restored as locally `ready`, and explicit clear returned it to `disabled`. This is credential-store integration proof only; it is not credential validity, model quality, latency, cost, retention, personal-data or dogfood acceptance.

The final local gate passed Ruff lint/format, strict Mypy over 129 source files, all 521 Pytest tests, Web TypeScript, 22 Vitest files / 69 tests, the Vite production build, credential-pattern scan and `git diff --check`.

## P1-12 DeepSeek V4 selection and versioned reanalysis verification

Offline backend tests cover both allowlisted DeepSeek IDs, model-only settings changes without re-entering a same-Provider credential, model-specific response identity/pricing, stage/run model projection, conjunctive ASCII/disjunctive CJK goal retrieval, Live-only closed bilingual warranty aliases, unrelated-evidence no-match, migration 16 preservation and explicit Live-only multi-Run creation. The default StageRunner single-pipeline rejection and P1-01 Fake Demo remain regression-pinned.

Web tests cover formal V4 Flash/V4 Pro selection, fixed-model behavior for other Providers, actual persisted model display, “重新分析” and compact historical versions. Browser checks at 1440×900, 1024×768 and 390×844 found no document overflow; model identity, reanalysis and history remained reachable, and application console warnings/errors were zero.

The authorized real-model lane used only the retained synthetic warranty Case. V4 Pro version 1 is the fixed `unknown` record (7/7, 8,330 Token, 0 citations). After the final closed-alias retrieval repair and fresh consent, version 5 completed `answered` (7/7, 7 calls, 7,642 input + 5,109 output = 12,751 Token, 6 candidate Claims and 7 citations). Versions 2 and 3 remain preserved historical results. Version 4 is preserved as an expected fail-closed `needs_review` record after the Provider reached the former 2,000-output-Token stage limit; the final bounded profile uses 8,000 input/output Tokens per stage and 56,000 input plus 56,000 output Tokens for the whole Run. See `docs/evaluation/p1-12-deepseek-v4-pro-evidence.md`.

The final local gate passes Ruff lint/format, strict Mypy over 129 source files, all 527 Pytest tests, Web TypeScript, 22 Vitest files / 72 tests, the Vite production build, credential-pattern scanning and `git diff --check`. The existing Starlette TestClient/httpx2 transition warning remains.

## P1-13 portfolio-presentation verification

P1-13 does not create a new Agent result. Browser acceptance copies the existing P1-12 synthetic SQLite authority to an isolated display directory and verifies the copy is byte-identical before startup. The display application uses the production read paths and built Web assets, but injects a client that raises on every attempted model call, disables persistent AI settings, and provides no credential-store access. Only health, capability and existing Case/Run GET paths are exercised.

The retained Case projects five historical Runs and the fixed final result: actual model `deepseek-v4-pro`, `answered`, 7/7 stages, 7 calls, 12,751 Token, 6 candidate Claims and 7 citations. Version 1 remains `unknown` with 8,330 Token and zero citations; version 4 remains `needs_review`. This display proof checks durable presentation and never reclassifies the historical real-model acceptance as a new call, new cost, real-data run or dogfood.

Real Chromium checks use 1440×900 for the result/candidate-fact and citation/stage views, 1024×768 for the expanded five-version history, and 390×844 for the single-column result view. At all three widths, `documentElement.scrollWidth` and `body.scrollWidth` equal the document client width; the application console reports zero warnings/errors. Four reviewed screenshots under `docs/assets/p1-13-portfolio/` contain only fixed synthetic content and no Key, machine path, log or personal data.

The local gate passes Web TypeScript, 22 Vitest files / 72 tests, the Vite production build, 36 Markdown files / 23 local links / 0 broken links, exact screenshot-dimension checks, `git diff --check`, sensitive/private-path scanning and the ignored/untracked coordination-file audit. Because P1-13 changes no source, API, dependency, lockfile or runtime behavior, the previously successful exact-head backend/Web CI for `ee23aab` remains parent evidence only and does not prove the local candidate.

## P1-R2 v0.2.0 Windows daily-use Release candidate verification

P1-R2 remains deterministic. The explicitly authorized installer slice may add only packaging code and a packaging-only pinned dependency. It must not start a Provider, call a real/paid/local/Fake model, read or recover a Provider Key, or change API routes/schemas, business authority, persistence schemas, Provider defaults, runtime dependencies, M7 evaluation, dogfood, Ollama, or M8 state.

The local gate requires:

- `scripts/verify.ps1`, including locked backend/Web checks and deterministic corpus/report checks;
- all tracked Markdown local links;
- exact `0.2.0` agreement across Python/npm declarations, lockfile project-version mirrors and Windows version resources; verify that PyInstaller exists only in the packaging dependency group;
- focused launcher/installer unit tests, PowerShell parser checks and one frozen application health/Web/stop/data-retention acceptance;
- one real per-user install/registration/start/stop/installed-uninstall/data-retention cycle with no leftover LifeCase process, shortcut, acceptance installation or uninstall key;
- `git diff --check` plus credential, private-path, personal-data, runtime-artifact, installer-artifact, and ignored/untracked coordination-file audits;
- five-axis correctness, readability, architecture, security, and performance review.

The historical candidate required separate push, exact-SHA CI, annotated tag, Release and asset decisions. Those v0.2.0 publication steps were subsequently completed; current release/asset truth is in [STATUS](STATUS.md). New source changes still need their own exact-SHA CI and separately approved release. Neither publication nor local success proves MSI, portable mode, native UI, code signing, production readiness or dogfood completion.

The expanded installer candidate passes Ruff lint/format over 183 API files, strict Mypy over 131 source files, 538 Pytest tests, Web TypeScript, 22 Vitest files / 72 tests and the Vite production build. The focused frozen and installed-application cycles also pass. The existing Starlette/TestClient transition warning remains; no model or Provider credential was used.

## Manual MVP acceptance

1. Start from a clean clone.
2. Use the documented PowerShell commands.
3. Complete one warranty case using synthetic material.
4. Restart both processes midway and resume.
5. Confirm a low-confidence fact and reject a wrong fact.
6. Export an `.ics` reminder and Evidence Pack.
7. Verify the Evidence Pack checksums.
8. Disconnect network and reopen/export the case.
9. Restore a backup into a clean data directory.
