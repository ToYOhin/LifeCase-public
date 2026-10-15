# API Contract

## Principles

- Prefix all application endpoints with `/api/v1`.
- Use JSON except multipart source import and artifact download.
- Use opaque IDs; clients do not derive meaning from ID format.
- Write-operation idempotency keys require a dedicated persistence contract and are not claimed by M1-04; clients must not automatically replay an ambiguous import.
- Concurrency-sensitive updates require the current case version.
- Error responses expose stable codes and safe messages, not stack traces.

## Error shape

```json
{
  "error": {
    "code": "claim_source_required",
    "message": "A confirmed claim requires evidence or explicit user confirmation.",
    "details": {},
    "retryable": false,
    "request_id": "req_example"
  }
}
```

## Foundation endpoint

### GET `/api/v1/system/health`

Response `200`:

```json
{
  "status": "ok",
  "service": "lifecase-api",
  "version": "0.2.0"
}
```

Health contains no filesystem path, credential state, or document statistics.

### GET `/api/v1/system/capabilities`

Returns explicit offline capability state and a read-only reminder for the newest verified retained Backup. Calling this endpoint never creates, deletes, uploads, schedules, or cleans a Backup.

Response `200` when no verified Backup exists:

```json
{
  "ocr": {
    "status": "unavailable",
    "code": "OCR_REQUIRED",
    "action": "Use a text PDF, TXT, or EML source; OCR is not included."
  },
  "model": {
    "status": "disabled",
    "action": "Deterministic Case reads, exports, and Backup remain available."
  },
  "backup_reminder": {
    "status": "no_verified_backup",
    "action": "Create a Backup explicitly from the Backup and Restore panel."
  }
}
```

When a retained Backup verifies, `backup_reminder.status` is `verified` and `latest` contains only `id`, `created_at`, `size_bytes`, and `case_count`. A retained artifact that cannot be verified yields `verification_failed` plus a safe explicit action; no archive path or underlying storage error is exposed. Model state is `disabled` for the package default and `unavailable` if a configured non-default provider has no runtime capability.

### GET `/api/v1/system/ai-capabilities`

Returns the server-owned presentation catalog for the existing AI boundaries. The projection remains secret-free; P1-11 configuration uses the separate contract below.

Response `200` includes:

- `catalog_version: "ai_provider_catalog.v1"`;
- `current_provider`, `current_model_id` and closed `runtime_status` (`disabled|ready|unavailable|evaluation_only`);
- `browser_configuration: "persistent_local_settings"`;
- exactly the current `openai`, `deepseek`, `groq`, `mistral`, `gemini`, `anthropic`, and `ollama` presentation records;
- per-record interface/product/evidence state, selection state, safe runtime state, optional fixed model ID and bounded action text.

The six cloud entries report only reviewed product evidence: OpenAI/Groq/Mistral/Gemini/Anthropic have offline injected-client proof and DeepSeek has fixed synthetic answered/unknown acceptance. Ollama reports `evaluation_only` plus
`quality_gate_failed`; the endpoint does not admit Ollama into `/live-agent`. No credential presence, secret,
endpoint, header, injected client, account data, prompt, evidence body, raw response or write action is returned.

### GET/PUT/DELETE `/api/v1/system/ai-settings`

`GET` returns `settings_version=ai_settings.v1`, persistence availability, configured state, selected Provider/fixed model and local runtime status. `PUT` requires `X-LifeCase-Local-Intent: ai-settings.v1` and a closed body containing `provider`, its exact allowlisted `model_id`, `api_key` and `data_use_confirmed=true`. It accepts no endpoint, host, tool, prompt, budget or Provider output. `DELETE` requires the same intent header and clears the saved credential. All responses use `Cache-Control: no-store` and contain no Key or credential value.

## M1 endpoints

### POST `/api/v1/cases`

Creates and persists an intake Case.

Request:

```json
{
  "title": "耳机保修申请",
  "goal": "整理购买凭证并准备保修材料",
  "template_id": "warranty"
}
```

Response `201` is the Case projection described below. The request field name is retained for M1 client compatibility, but its value is an explicit business Case-type selector: `warranty`, `return_refund`, `reimbursement`, or `subscription_cancellation`. The closed registry resolves those values to `warranty_claim@1`, `return_refund_claim@1`, `expense_reimbursement@1`, or `subscription_cancellation_claim@1` respectively. Unknown or mismatched bindings fail closed; titles, goals, and imported content never select a template implicitly.

### GET `/api/v1/templates`

Returns the stable reviewed catalog in product order. Each item exposes `case_type`, immutable `template_id` and integer `template_version`, user-visible title, finite goal options, ordered required Claim identifiers, bounded evidence-requirement descriptions, and the exact resource SHA-256. It returns no YAML body, filesystem path, plugin location, Claim value, or mutable editor state. A registry/resource failure returns retryable `503 template_catalog_unavailable` with a redacted envelope.

Each item also exposes `conditional_claims`, an ordered array of `{ "when": string, "field": string }` copied from the existing template (empty when none). Warranty includes `{ "when": "manufacturer_requires_serial", "field": "serial_number" }`. This makes the existing conditional field discoverable for manual registration; it neither evaluates the condition in the client nor adds the field to `required_claims`. Template resources, hashes, Claim writes, confirmation/version rules and persistence are unchanged. The current Web intake consumes conditional fields only for warranty.

### GET `/api/v1/cases`

Implemented by M4-01 as the read-only Case/Today projection documented below. M5-02 now supplies an authoritative bounded `next_action` summary (or `null`) while M5-01 Deadline records remain on their separate route. `deadline_risk` remains `not_calculated` until a reviewed risk policy exists.

### GET `/api/v1/cases/{case_id}`

Returns authoritative SQLite state, current version, and ordered Source projections. Refreshing the Web application reloads this endpoint using the Case ID in the URL fragment; browser memory is not durable state.

Case projection:

```json
{
  "id": "case_opaque",
  "type": "warranty",
  "template_id": "warranty_claim",
  "template_version": 1,
  "title": "耳机保修申请",
  "goal": "整理购买凭证并准备保修材料",
  "status": "intake",
  "priority": 0,
  "version": 1,
  "created_at": "2026-08-01T00:00:00.000000Z",
  "updated_at": "2026-08-01T00:00:00.000000Z",
  "closed_at": null,
  "sources": []
}
```

M7-01 still exposes no template-editing or arbitrary evaluation endpoint. The catalog is read-only; template gap and close-condition evaluation remain domain-service authority consumed by existing Case/Action/workbench paths.

### POST `/api/v1/cases/{case_id}/sources`

Multipart field `file` contains exactly one explicitly selected browser file. The API streams it to a bounded server-owned staging file, owns all storage destinations, and never accepts a client filesystem path.

Response `201`:

```json
{
  "id": "src_opaque",
  "case_id": "case_opaque",
  "original_name": "receipt.txt",
  "media_type": "text/plain",
  "sha256": "64-lowercase-hex-characters",
  "size_bytes": 128,
  "trust_label": "untrusted",
  "imported_at": "2026-08-01T00:00:00.000000Z",
  "extraction": {
    "status": "completed",
    "extractor_version": "text.v1",
    "segment_count": 1,
    "notices": [],
    "error_code": null
  }
}
```

Import and extraction are distinct durable outcomes. If bytes are safely imported but parsing fails, the endpoint still returns `201` with `extraction.status=failed` or `ocr_required` and a safe `error_code`. A failure before Source persistence returns the error envelope and creates no Source record.

### GET `/api/v1/sources/{source_id}`

Returns the same Source projection from SQLite. It never returns `object_path`, a server absolute path, SourceSegment text, or binary content. Binary download is not implemented in M1-04.

### GET `/api/v1/cases/{case_id}/source-segments`

Returns the current extracted text Segments for one explicit Case in deterministic Source-import/sequence/Segment order. Each item contains `id`, `source_id`, safe `source_name`, `sequence`, optional `page`, bounded `text`, extraction method/version, optional confidence, and recomputed `text_sha256`. The service enforces the Case predicate in SQL, caps the result, and rejects hash drift. It returns no object path, source bytes, SQL, credential, or machine path. A missing Case returns `case_not_found`; a safe integrity/storage failure returns retryable `source_storage_failed`.

## Implemented M1 error codes

| Code | HTTP | Meaning |
|---|---:|---|
| `validation_error` | 422 | JSON or multipart contract is invalid; details contain field locations, not submitted values |
| `case_invalid` | 422 | Case service rejected title, goal, or template |
| `case_not_found` | 404 | owning Case does not exist |
| `case_storage_failed` | 503 | Case persistence is unavailable; retryable |
| `source_path_invalid` | 400 | selected upload name or staged file is invalid |
| `source_path_outside_allowed_root` | 403 | server staging containment failed closed |
| `source_too_large` | 413 | streamed upload crossed the configured byte limit |
| `media_type_unsupported` | 415 | filename-derived type is outside the allowlist |
| `source_integrity_failed` | 409 | immutable object identity validation failed |
| `extraction_failed` | 422 | extraction failed before a durable typed state could be recorded |
| `source_storage_failed` | 503 | Source/object persistence is unavailable; retryable |
| `source_not_found` | 404 | Source does not exist |

## M2-01 Case lifecycle endpoints

### POST `/api/v1/cases/{case_id}/transitions`

Commits one state transition through the Case service. The command is bound to the current optimistic-concurrency version:

```json
{
  "expected_version": 1,
  "target_status": "ready",
  "actor": "local_user",
  "reason": "Required synthetic intake checks are complete"
}
```

Response `200`:

```json
{
  "id": "transition_opaque",
  "case_id": "case_opaque",
  "prior_status": "intake",
  "new_status": "ready",
  "prior_version": 1,
  "new_version": 2,
  "actor": "local_user",
  "reason": "Required synthetic intake checks are complete",
  "transitioned_at": "2026-08-01T00:01:00.000000Z"
}
```

### GET `/api/v1/cases/{case_id}/transitions`

Returns append-only transition records ordered by `new_version`, then opaque transition ID. A Case with no transitions returns `[]`; a missing Case returns `case_not_found`.

M2-01 lifecycle errors:

| Code | HTTP | Meaning |
|---|---:|---|
| `case_transition_invalid` | 422 | trimmed command metadata or Case scope violates service bounds |
| `case_transition_not_allowed` | 409 | requested state edge, including a repeated state, is forbidden |
| `case_version_conflict` | 409 | `expected_version` is stale; safe details contain `current_version` |
| `case_not_found` | 404 | target Case does not exist |
| `case_storage_failed` | 503 | Case/history transaction or history read failed safely |

## M2-02 Claim decision endpoints

Claim creation, source attachment, provenance reads, and history reads remain domain-service boundaries. The HTTP contract includes one narrow manual evidence-candidate command plus the three existing user decision operations; it adds no Agent run, inference, arbitrary field, or bulk Claim endpoint.

### POST `/api/v1/cases/{case_id}/claims`

Creates exactly one `candidate` Claim from an explicit user-selected quote in a current same-Case SourceSegment:

```json
{
  "field": "purchase_date",
  "value": "2026-08-01",
  "segment_id": "segment_opaque",
  "quote_start": 82,
  "quote_end": 92,
  "actor": "local_user",
  "reason": "Selected the purchase date from the imported receipt"
}
```

The service loads the Case's exact reviewed template and rejects fields outside its closed field set. It reloads the Segment through the owning Case, recomputes the complete text hash, validates the zero-based half-open quote bounds, computes the quote hash server-side, and delegates the Claim plus one `supports` citation to `ClaimService`. The client never submits source IDs, Case ownership, hashes, trust decisions, confidence, status, or `created_by`. Response `201` is the Claim projection below with `status=candidate` and `created_by=user`.

Claim projection:

```json
{
  "id": "claim_opaque",
  "case_id": "case_opaque",
  "field": "order_number",
  "value": "SYN-2026-001",
  "status": "confirmed",
  "confidence": 0.92,
  "created_by": "agent",
  "contradiction": false,
  "version": 2,
  "created_at": "2026-08-01T00:02:00.000000Z",
  "updated_at": "2026-08-01T00:03:00.000000Z"
}
```

### POST `/api/v1/claims/{claim_id}/confirm`

```json
{
  "expected_version": 1,
  "basis": "user",
  "actor": "local_user",
  "reason": "Explicitly verified against synthetic material"
}
```

`basis=evidence` requires a currently valid supporting ClaimSource. `basis=user` records an explicit value/version-bound user confirmation. Response `200` is the committed Claim projection.

### POST `/api/v1/claims/{claim_id}/correct`

```json
{
  "expected_version": 2,
  "value": "SYN-2026-002",
  "actor": "local_user",
  "reason": "Corrected after reviewing the synthetic receipt"
}
```

The prior canonical value and status remain in immutable history. Response `200` has `status=corrected` and version incremented by one.

### POST `/api/v1/claims/{claim_id}/reject`

```json
{
  "expected_version": 2,
  "actor": "local_user",
  "reason": "The synthetic candidate refers to a different order"
}
```

Response `200` has `status=rejected` and version incremented by one.

M2-02 Claim errors:

| Code | HTTP | Meaning |
|---|---:|---|
| `claim_invalid` | 422 | domain value or command metadata violates stable bounds |
| `claim_not_found` | 404 | target Claim does not exist |
| `claim_version_conflict` | 409 | expected Claim version is stale; safe details contain `current_version` |
| `claim_transition_not_allowed` | 409 | operation is forbidden for the current Claim state or repeats the value/state |
| `claim_source_required` | 409 | evidence confirmation has no current supporting source |
| `claim_source_invalid` | 409 | ClaimSource is missing, stale, cross-Case, or hash-invalid |
| `claim_storage_failed` | 503 | Claim transaction/read failed safely; retryable |

## M2-04 Evidence Context boundary

M2-04 intentionally adds no HTTP endpoint. Evidence Context construction remains a pure Python domain service. Its typed request requires a Case ID, bounded retrieval parameters, adjacent-window bounds, an explicit trust-label policy, and UTF-8 byte budgets. M3-04 consumes it through an internal controlled Agent tool and validate-stage contract only; no retrieval or question-answering HTTP/UI contract is added.

## M4-01 Case workbench

### GET `/api/v1/cases`

Returns the deterministic read-only Case list used by the Today workbench. Every item contains the authoritative Case identity, template binding, title, goal, lifecycle status, priority/version/timestamps, `is_today`, latest admitted activity timestamp/kind, and ordered missing required-Claim/evidence identifiers from the M2-03 template evaluator.

`next_action` is an authoritative M5-02 object or `null`, selected by deterministic graph/readiness order from committed same-Case Actions. `deadline_risk` remains explicitly `not_calculated` because no formal risk rule is implemented. Authoritative dates use the separate Deadline route. The endpoint does not expose Source bodies, Claim values, Agent checkpoints, object paths, SQL, or machine paths. An empty database returns `200 []`; a safe read/configuration failure returns retryable `503 case_storage_failed`.

## M4-02 Evidence review

### GET `/api/v1/cases/{case_id}/evidence-review`

Returns one deterministic Case-scoped queue of active `candidate` and `inferred` Claims. Each item contains the authoritative Claim projection, every freshly validated supporting/contradicting citation, all same-field `confirmed` and `corrected` Claim projections, and `is_next`. The top-level `next_claim_id` is the first item or `null` for an empty queue.

Citation fields include Source/Segment identity, source display name, sequence, page, current `text_sha256`, complete current Segment text, exact quote offsets/hash/text, relation, and attachment audit metadata. This intentionally narrow review route returns Segment text so a person can compare source, extracted passage, candidate, and settled values side by side. It never returns source bytes, object paths, SQL, prompts, checkpoints, or machine paths.

Template fields are ordered by reviewed workflow order, with unknown fields following lexicographically. Contradictory candidates come first within a field, then `inferred` before `candidate`, followed by creation time and Claim ID. Every persisted citation is rejoined to authoritative same-Case Source/SourceSegment rows and revalidated; one stale, missing, cross-Case, or hash-invalid citation fails the whole read closed.

Decisions reuse the M2-02 `confirm`, `correct`, and `reject` endpoints above. The browser must supply non-empty actor and reason values. A current supporting citation permits `basis=evidence`; a source-free candidate can be confirmed only through explicit `basis=user`. M4-02 adds no alternate Claim mutation path.

| Code | HTTP | Meaning |
|---|---:|---|
| `evidence_review_invalid` | 422 | Case scope violates stable bounds |
| `case_not_found` | 404 | target Case does not exist |
| `evidence_review_integrity_failed` | 409 | persisted review authority is missing, stale, cross-Case, or hash-invalid |
| `evidence_review_unavailable` | 503 | configuration or persistence prevented a safe read; persistence failures are retryable |

### GET `/api/v1/cases/{case_id}/current-facts`

Read-only preparation-sheet and fact-review projection of **all current Claims**, including settled and rejected
ones; it does not replace the active Evidence Review queue. The response has
`{case_id, case_type, items}`. Each item contains:

- `claim`: the unchanged authoritative Claim projection;
- `value_sha256`: the stored `ClaimValue.sha256` for that exact current Claim value. Clients bind this server-provided hash with the Claim ID and version when preparing ordinary local Actions; they do not rebuild authority by serializing the displayed value;
- `confirmation_basis`: `evidence`, `user`, `correction`, or `null`, and `confirmation` with
  existing `actor`, `reason`, `occurred_at` metadata (or `null`);
- `evidence_status`: `valid`, `none`, or `unavailable`, and `citations` with the same freshly
  checked source name, Segment text, quote, hashes and Unicode code-point offsets as Evidence Review;
- `conflict`: a presentation-only flag for different normalized non-rejected same-field values
  or a current contradicting citation; it never rewrites `claim.contradiction`;
- `usable_as_fact`: only a confirmed/corrected value with its matching confirmation/correction
  record, no conflict and no invalid source. Evidence-based confirmation also needs a valid
  supporting citation; explicit user confirmation/correction may have no attached source.

Correction metadata describes an explicit human decision. Its original citations remain unchanged
and must not be presented as verbatim proof of a replacement value. Candidate, inferred, rejected,
conflicting, unconfirmed-record and stale-source values are never promoted to usable facts.

The domain service uses one read transaction and `ClaimService` source validation. A missing,
cross-Case or hash-invalid citation degrades only its fact to `unavailable`, with no citations and
`usable_as_fact=false`; foreign or stale text is not returned. Structural/storage failure fails
the whole projection safely. Ordering is the existing Claim field/creation/ID order. An empty
Case returns an empty `items` array; successful responses carry `Cache-Control: no-store`.

This endpoint performs no model call, write, transition, approval, Action execution, draft or
evidence-pack rewrite. A preview or clipboard copy is not a sent communication or completed Action.
It exposes no object path, source bytes, credentials, SQL or machine path.

The four-template Web correction entry reads this projection and reuses the existing Claim `correct`
command with the selected Claim ID, `expected_version`, actor and reason. It adds no mutation to
`current-facts` and no new authority. Known string fields use plain text/date inputs with non-empty,
length and calendar-date checks in the Web form. Existing numeric amount values use safe integer inputs without changing their original unit or converting currencies; textual amount values remain text. These are not new field-type rules in the generic
Claim API. Conflict or ambiguous network outcomes require rereading before retrying the settled-fact
form. A successful correction refreshes current reads without rewriting historical Runs or exports.

| Code | HTTP | Meaning |
|---|---:|---|
| `current_facts_invalid` | 422 | Case scope violates stable bounds |
| `case_not_found` | 404 | target Case does not exist |
| `current_facts_unavailable` | 503 | current authority could not be safely read; retryable |

## Reviewed repair Action from a question analysis

`GET /api/v1/cases/{case_id}/analysis-action-draft?run_id={run_id}` prepares a bounded
Chinese communication draft from a completed, answered, current question-bound q1 Run.
It makes no model call and creates no Action. Old unbound, unfinished, unknown, superseded,
changed-basis, and unreadable runs cannot authorize a draft.

`POST /api/v1/cases/{case_id}/analysis-action-draft` explicitly saves a Risk-B
`prepare_communication` Action. The request contains `expected_run_id`, strict positive
`expected_run_version`, `basis_sha256`, `answer_sha256`, `draft_text`, `actor`, `reason`,
and paired nullable `expected_action_id` / `expected_action_version`. The body is at most
500 Unicode code points and the complete canonical Action payload remains bounded to
2048 UTF-8 bytes. Neither read nor save automatically consumes the separate preparation intent.

Both responses contain `{case_id, run_id, run_version, question, basis_sha256,
answer_sha256, draft_text, saved_action}`. `saved_action` is null before explicit save,
otherwise `{id, version, draft_text, status}`. GET then returns the saved body verbatim.
Successful responses are not cacheable. Save checks exact Run/checkpoint/basis identity and
current authority inside the Action write transaction. It preserves existing required-fact
blockers, Action events, actor/reason, and approval invalidation on revision.

An initial-save replay returns the existing Action without another event only while it is
still version 1 and its body and original actor/reason match. Different content, reviewer,
reason, or a stale Action version conflicts; the client must reread and explicitly choose
which edit to retain. Failed/ambiguous responses do not authorize reuse of a prior preview.
The Web keeps unsaved text in this tab's Case/Run-scoped session storage solely as editing
input; reload requires fresh server authority and review before saving it.
If a new analysis becomes current, this tab's edits for old Runs remain available in a
read-only disclosure, selected only from this Case's server-reported Run history. They
are not automatically copied into the new draft or treated as review permission.

Action reads add nullable `analysis_basis_status`; bound historical drafts retain their
saved text with `current`, `changed`, or `unavailable`. Changed/unavailable drafts cannot
authorize approval or progression, while cancellation and history reading remain available.
The original two-key communication payload stays compatible. Generic Action create/revise
cannot introduce or remove the bound draft variant to bypass the dedicated save checks.
Saving creates no approval, starts no action, and sends nothing externally.

## Warranty preparation intent

### GET `/api/v1/cases/{case_id}/preparation-intent`

Reads only the current explicitly saved intent for one existing `warranty` Case. An unsaved
Case returns `200` with the following value without inserting a row:

```json
{
  "case_id": "case_opaque",
  "intent": "",
  "version": 0,
  "updated_at": null
}
```

### PUT `/api/v1/cases/{case_id}/preparation-intent`

Explicitly saves the supplied plain text against its own last-read version:

```json
{
  "intent": "请说明检测流程。\n请先告知可能产生的费用。",
  "expected_version": 0
}
```

Only these two request fields are accepted. `intent` is a string containing at most 2,000
Unicode code points, not bytes or UTF-16 code units. Text, whitespace, line breaks and emoji
are retained verbatim; NUL and unpaired surrogates that cannot be encoded as UTF-8 are rejected.
`expected_version` is a strict non-negative integer; Boolean, string and floating-point values
are invalid. The Case ID comes only from the URL.

Response `200` has `{case_id, intent, version, updated_at}`. The first accepted save returns
`version=1`; every accepted save increments the preparation intent version exactly once and
records UTC microsecond `updated_at` ending in `Z`. The service checks the current version and
commits the text in one `BEGIN IMMEDIATE` transaction. PUT with `intent=""` explicitly clears
the saved text but retains the row, timestamp and increasing version. Clearing never returns
the Case to the unsaved version-0 state, so an old page cannot resurrect cleared text.

Both successful endpoints carry `Cache-Control: no-store`. A conflict requires rereading
before an explicit save against the new version; an ambiguous network result likewise
requires checking saved state instead of assuming success or retrying an overwrite.

This is a current user draft, not a confirmed fact, Action, approval or communication history.
It does not change Case `goal`, lifecycle, `version`, timestamps or transition history, and
does not change Claim/Action versions or their actor/reason records. The preparation sheet
continues reading facts and sources through current authority; no fact snapshot or rendered
sheet is persisted here. Copying a preview does not save its text, send it or complete an Action.

| Code | HTTP | Meaning |
|---|---:|---|
| `validation_error` | 422 | invalid text, strict version or extra request field; submitted values are not echoed |
| `case_not_found` | 404 | owning Case does not exist |
| `preparation_intent_unsupported` | 422 | Case exists but is not a warranty Case |
| `preparation_intent_version_conflict` | 409 | stale version; safe details contain only `current_version` |
| `preparation_intent_unavailable` | 503 | stored text/version/timestamp is invalid or storage failed; retryable safe error without stored text, SQL or paths |

Migration 17 adds only the Case-owned current-intent table. Startup retains the existing
verified backup-before-migration order; supported older databases and backups migrate forward,
with existing Cases initially having no saved intent. Current backups include intent rows in
the complete SQLite snapshot. A pre-migration build does not support the new migration catalog;
opening a new-schema database or restoring its backup in that older build is not supported.

## M4-03 Timeline and recovery

### GET `/api/v1/cases/{case_id}/timeline`

Returns one deterministic, oldest-first Case Timeline over existing authoritative rows. Admitted kinds are Source import, latest Source extraction status, Artifact creation, immutable Case transition, immutable Claim create/source-attach/confirm/correct/reject event, latest Agent-run status, and latest Agent-stage status. Mutable extraction and Agent rows are explicitly current status snapshots; the API does not invent intermediate history that was never persisted.

Every event contains a stable composite `event_id`, Case ID, kind/time, authority type/ID, subject and optional parent IDs, persisted origin/actor, bounded label/status/prior status/version/reason fields. Ordering is UTC time ascending, then a closed event-kind rank, then authority ID. The endpoint returns no Source or Claim value body, object/relative path, prompt, complete checkpoint, credential, SQL, or machine path.

`availability.approvals` is `available`; `availability.communications` is `available_as_action_projection` because M5 communication output is a deterministic Action draft rather than an external-send record. Timeline admits only committed Action and Approval authority events. An existing Case with no admitted rows returns `200` and an empty `events` list.

| Code | HTTP | Meaning |
|---|---:|---|
| `timeline_invalid` | 422 | Case scope violates stable bounds |
| `case_not_found` | 404 | target Case does not exist |
| `timeline_integrity_failed` | 409 | persisted Case/history/reference/hash authority is incomplete or inconsistent |
| `timeline_unavailable` | 503 | persistence prevented a safe read; retryable |

## M5-01 Deadline engine

### GET `/api/v1/cases/{case_id}/deadlines`

Returns all current authoritative Deadlines for exactly one Case in deterministic creation-time/ID order. Each item exposes `kind`, `date_kind`, canonical `due_value`, IANA `timezone`, `status`, `uncertainty`, optional confidence, optimistic `version`, immutable `definition_sha256`, ordered basis Claim IDs/versions/value hashes/fields/date kinds, and an optional calculated rule ID/version/canonical parameters/parameter hash.

The three kinds are never shape-equivalent: explicit has one basis and no rule; calculated has basis plus a rule; suggested has neither and carries the personal-preference uncertainty code. The endpoint revalidates current Claim authority, rule availability/result, hashes, and contiguous immutable history before returning any result. A Case with no Deadlines returns `200 []`. It does not calculate `deadline_risk`.

### POST `/api/v1/cases/{case_id}/deadlines/explicit`

Creates a proposed explicit Deadline from one supplied basis containing `claim_id`, positive `expected_claim_version`, exact `value_sha256`, and `date_kind`, plus IANA `timezone`, actor, and reason. The service derives the due value from the current same-Case, non-contradictory, confirmed/corrected Claim; clients do not submit a competing due value.

### POST `/api/v1/cases/{case_id}/deadlines/calculated`

Creates a proposed calculated Deadline from ordered basis contracts, IANA timezone, registered `rule_id`/`rule_version`, canonicalizable rule parameters, actor, and reason. The current registry exposes `calendar_days_after_basis@1`, requiring one authoritative date basis and `{"days": 1..366}`. The result is deterministic and has no model or wall-clock input.

### POST `/api/v1/cases/{case_id}/deadlines/suggested`

Creates a proposed personal follow-up preference from `date_kind`, `due_value`, IANA timezone, actor, and reason. It creates no Claim basis or calculation rule and is not an external obligation.

### Deadline status commands

```text
POST /api/v1/deadlines/{deadline_id}/confirm
POST /api/v1/deadlines/{deadline_id}/pass
POST /api/v1/deadlines/{deadline_id}/complete
POST /api/v1/deadlines/{deadline_id}/dismiss
```

Every request supplies strict positive `expected_version`, actor, and reason. Legal edges are proposed to confirmed/dismissed; confirmed to passed/completed/dismissed; and passed to completed/dismissed. Completed and dismissed are terminal. A successful command increments version exactly once and returns the complete revalidated Deadline projection.

| Code | HTTP | Meaning |
|---|---:|---|
| `deadline_invalid` | 422 | command/date/timezone/date-kind/metadata boundary is invalid |
| `deadline_rule_invalid` | 422 | rule ID/version/parameters or finite rule input is invalid |
| `case_not_found` | 404 | owning Case does not exist |
| `deadline_basis_claim_not_found` | 404 | basis Claim does not exist |
| `deadline_not_found` | 404 | target Deadline does not exist |
| `deadline_basis_invalid` | 409 | basis is cross-Case, non-authoritative, contradictory, or not a valid date |
| `deadline_basis_stale` | 409 | basis Claim version or exact value hash changed |
| `deadline_version_conflict` | 409 | expected Deadline version is stale; details contain current version only |
| `deadline_transition_not_allowed` | 409 | requested state edge is forbidden |
| `deadline_integrity_failed` | 409 | persisted definition, rule, basis, or history is inconsistent |
| `deadline_storage_failed` | 503 | atomic write or safe read failed; retryable |

## M3-01 model-provider boundary

M3-01 intentionally adds no HTTP, SSE, or UI contract. Provider selection and structured generation remain an internal async Python protocol. The default disabled provider cannot affect existing deterministic APIs. Model runs, stage progress, repair, checkpoints, budgets, and cancellation endpoints remain M3-02 or later; evidence-backed retrieval and question answering remain M3-04.

## Deferred Agent run endpoints

M3 completed its internal provider, stage, extraction, and evidence-backed validation boundaries without exposing HTTP run control. The following routes remain deferred and are not part of the current API:

```text
POST /api/v1/cases/{case_id}/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/events
```

SSE events contain stage name, status, progress, safe summary, and timestamp. They do not contain full source text, model prompts, or credentials.

## M5-02 Action planner contract

```text
GET  /api/v1/cases/{case_id}/actions
POST /api/v1/cases/{case_id}/actions
POST /api/v1/actions/{action_id}/revise
POST /api/v1/actions/{action_id}/revalidate
POST /api/v1/actions/{action_id}/start
POST /api/v1/actions/{action_id}/complete
POST /api/v1/actions/{action_id}/fail
POST /api/v1/actions/{action_id}/cancel
```

Create accepts one closed kind, risk level, bounded kind-specific payload, ordered Claim basis contracts, ordered dependency IDs with expected versions, and an optional Deadline ID/version/definition hash. The service validates Case/template/Claim/Deadline authority, forbidden actions, the complete dependency DAG, and canonical blockers before committing. Response items include optimistic version, canonical payload/blocker hashes, dependency state, original Deadline date projection, completion-evidence references, stable topological position, and optional deterministic communication draft.

Revise is allowed for proposed/blocked/ready and M5-03-approved Actions and replaces the complete payload/basis/dependency/Deadline definition using an expected Action version. Revalidate recomputes the canonical blocker snapshot against current authority. A legitimate upstream Action version advance leaves a dependent blocked Action readable with its stored binding; it cannot execute until revalidation refreshes that binding and appends the next immutable event. Start, complete, fail, and cancel are service-owned optimistic transitions; complete additionally requires ordered same-Case Claim/Source/Artifact evidence contracts. M5-02 itself cannot produce `approved`; the separate M5-03 authority below can.

The Case workbench now returns a bounded authoritative M5-02 next-action summary. Action risk does not change `deadline_risk=not_calculated`.

### Manual progress on a saved repair-preparation Action

```text
GET  /api/v1/cases/{case_id}/actions/{action_id}/progress
POST /api/v1/cases/{case_id}/actions/{action_id}/progress
```

This local entry is limited to the existing question-analysis-bound
`prepare_communication` Action. Reads return `{action, entries}` with a freshly
validated Action and its append-only manual records. Each record includes its ID,
Case/Action IDs, observed `action_version`, `kind` (`progress` or `result`), raw
`text`, `actor`, `reason`, and UTC `created_at`. These records are neither Claims
nor completion evidence. Successful reads and writes use `Cache-Control: no-store`.

POST requires `expected_version` for the Action and an explicitly present
`expected_last_progress_id` (null for the first record), plus kind, bounded text,
actor and reason. Both optimistic conditions and current analysis authority are
rechecked in the same immediate transaction. Text is bounded to 2000 Unicode code
points and 8000 UTF-8 bytes. A successful append returns 201; it
does not change the Action version, payload, status, Case, facts or Approval.
Done/cancelled Actions remain readable but cannot receive new records. A lost
response requires a fresh read; the client does not automatically replay a write.

The Web completion entry separately selects an imported same-Case Source and reads
its metadata and extracted text. Explicit review is reset when the selection,
Action authority or read availability changes. Any unsaved manual note must first
be explicitly saved or cleared, then completion reviewed again. It calls the existing
`/actions/{action_id}/complete` with the exact Action version and Source hash.
Approval and start remain separate commands; a result note never performs them.
Completing this Action means communication preparation is complete, not that a
repair was sent, accepted or successful. Importing new material changes the old
analysis basis and continues to block that old Action; fresh analysis and a newly
reviewed saved Action are required, without rewriting the original history.

Timeline exposes `action_progress_recorded` references with the real actor/reason
and a progress/result label, no implied Action status transition or note body.
The full body is available only through the Case/Action-scoped progress read.

## M5-03 Approval and ICS contract

```text
GET  /api/v1/cases/{case_id}/approvals
POST /api/v1/actions/{action_id}/approval-requests
POST /api/v1/actions/{action_id}/approve
POST /api/v1/actions/{action_id}/reject
POST /api/v1/actions/{action_id}/expire-approval
GET  /api/v1/cases/{case_id}/deadlines/{deadline_id}/calendar-export
```

An Approval request accepts the expected Action version, payload SHA-256, the exact bounded summary shown to the user, actor label, and reason. Approve/reject/expire accept expected Action and Approval versions. Responses expose status and authority hashes but never Claim bodies. Revision, revalidation, or cancellation invalidates active prior approval atomically. Risk C can reach approved but cannot start; Risk D is rejected; actor remains an unauthenticated local audit label.

Calendar export revalidates Case ownership and the complete Deadline basis/rule/history, requires `confirmed`, and returns `text/calendar; charset=utf-8` with a bounded attachment filename. Identical committed Deadline input produces identical bytes. It performs no database write, Artifact creation, system calendar call, connector access, email, or browser submission.

## M6-01 Evidence Pack contract

```text
POST /api/v1/cases/{case_id}/evidence-pack
GET  /api/v1/cases/{case_id}/evidence-pack
GET  /api/v1/artifacts/{artifact_id}
```

POST freezes one revalidated Case-scoped authority snapshot and returns `201` with retained Artifact ID, safe filename, SHA-256, size, independent format version, creation time, and download URL. Equal committed authority reuses the same byte-identical pack and Artifact. GET by Case returns the latest retained, reverified pack metadata for refresh/restart recovery; absence is `404 artifact_not_found`. GET by Artifact returns `application/zip` only after rechecking the retained file and complete archive integrity. Invalid requests, missing Case/Artifact, integrity/limit failures, and storage failures use typed redacted envelopes and never expose content, SQL, paths, or credentials.

## M6-02 Backup and restore contract

```text
GET  /api/v1/backups
POST /api/v1/backups
GET  /api/v1/backups/{backup_id}
POST /api/v1/restores/validate
POST /api/v1/restores/apply
```

Backup create returns `201` only after the SQLite online snapshot, all referenced retained files, manifest membership, sizes, and SHA-256 values pass independent verification and content-addressed no-overwrite publication. Latest and download revalidate retained bytes; absence is `404 backup_not_found`. The response exposes only an opaque content-hash ID, safe filename, format/minimum-application versions, aggregate counts, size, creation time, and local download URL.

Restore validation accepts one multipart Backup ZIP, persists an opaque content-hash validation ticket, and does not write a restore target. Apply accepts that ticket, a bounded target name, and the exact `RESTORE INTO NEW DIRECTORY` confirmation. The server derives a target below its recovery root and rejects every existing target, including an empty directory; it never accepts an arbitrary machine path, overwrites, merges, deletes, or repairs the active runtime. Success returns only the opaque runtime ID, Case count, and whether the restored staging database required a supported forward migration. Invalid input, integrity/limit failure, missing tickets, non-clean targets, storage failure, and apply failure use typed redacted envelopes.

HTTP request/response truth is the generated FastAPI OpenAPI schema backed by strict Pydantic transport models. Domain dataclasses and SQLite constraints remain business/storage authority. Files under `packages/contracts/schemas/` are authoritative only when an explicit automated validator names them; they are not a parallel blanket definition of every HTTP endpoint.

## M7-02 offline evaluation contract

M7-02 intentionally adds no HTTP or Web endpoint. The fixed corpus is evaluated from the repository with `uv run python -m lifecase.evaluation fixtures/synthetic-cases/m7-v1`. Canonical JSON and Markdown outputs expose corpus/version/hash, per-Case classifications, aggregate thresholds, safe failure codes, unsupported product capabilities, and the reproduction command. They expose no fixture body, local absolute path, SQL, credential, remote error, or live-model quality claim.

## P1-01 Portfolio Agent Demo contract

```text
GET  /api/v1/cases/{case_id}/agent-run
POST /api/v1/cases/{case_id}/agent-run
POST /api/v1/cases/{case_id}/agent-run/execute
POST /api/v1/cases/{case_id}/agent-run/retry
POST /api/v1/cases/{case_id}/agent-run/recover
POST /api/v1/cases/{case_id}/agent-run/cancel
```

GET returns `portfolio_agent_demo.v1` capability state, `supported_case_types=[warranty]`, and the Case's single run or `null`. Default configuration reports `disabled/provider=disabled`; deterministic product routes remain available. Create writes only a queued run, rejects a second run, and rejects non-warranty Cases because their extraction Schema is not reviewed. Execute advances eligible fixed stages using server-owned scope-keyed Fake Provider scripts. Retry is accepted only for one persisted retryable stage with remaining budget. Recover conservatively charges an interrupted reservation and moves it to review before retry. Cancel delegates to StageRunner's optimistic cancellation authority.

The response contains bounded run/stage status, versions, `stage_order`, budgets/usage, safe error/retry flags, fixed provider label, safe summaries, candidate/inferred Claim projections, answered/unknown statements, citation metadata, and the allowed risk-A tool name/count/match result. For this Demo contract, `stage_order` remains the seven-stage legacy order. It contains no stage input, full Evidence Context, checkpoint body, prompt, raw provider response, SQL, credential, object path, or private log. Cross-Case, stale, forged, or tampered authority fails closed with a typed redacted error.

P1-01 runtime errors use the common safe `error` envelope:

| Code | HTTP | Meaning |
|---|---:|---|
| `agent_demo_disabled` | 409 | deterministic demo capability was not explicitly enabled |
| `agent_run_exists` | 409 | the Case already owns its single run |
| `agent_run_not_found` | 404 | the Case has no run for the requested command |
| `agent_run_not_executable` | 409 | current run state does not admit the command |
| `agent_demo_case_unsupported` | 409 | Case type has no reviewed extraction Schema |
| `case_not_found` | 404 | owning Case does not exist |
| `agent_run_invalid` | 422 | bounded request or identifier validation failed |
| Agent service codes | 409 or 503 | version/sequence/integrity/budget conflict, or safely unavailable persistence/provider/tool boundary |
| `agent_run_unavailable` | 503 | an unclassified dependency failure was reduced to a retryable safe error |

P1-R1 read-only audit result: generated OpenAPI 3.1.0 exposes the six operations, their success statuses, no request bodies, and `AgentDemoResponse`. Every operation also documents 404, 409, 422, and 503 with the exact existing `ApiErrorResponse -> ApiErrorDetailResponse` envelope; a focused regression verifies those references and validates a real disabled-capability response against the same Pydantic model. The two files currently under `packages/contracts/schemas/` have no automated validator naming them; neither is a cross-boundary authority. See `docs/evaluation/p1-r1-release-evidence.md` for hashes and reproducible evidence.

## P1-04 Live Agent Read-only Beta contract

### Current case question contract (2026-09-19)

The transport is now `live_agent_read_only.v3`. The Web asks an independent
natural-language `question` without changing Case goal. POST
`/api/v1/cases/{case_id}/live-agent/preview` accepts only `{question}` (trimmed
1–2000 characters, no NUL). Create adds that same question to the existing
`preview_sha256` / `confirm_read_only` body. The consent hash covers question,
server retrieval expression, current fact/material basis, Provider/model and budget.

New question Runs use `portfolio-live-q1-<uuid>`, with no question or hash encoded
in the ID. They execute only `validate`: one adapter call without repairs/retries.
There is no extraction, candidate insertion or placeholder checkpoint. Omitting
question on create preserves the two-stage API for older clients. Old two/seven-stage
Runs remain readable and recover under their original contracts.

For question Runs, execute/retry/recover/cancel require `{expected_run_id}`. Missing
or different IDs return `409 live_agent_run_stale`; old clients operating old Runs
may omit the body. The Web always supplies its displayed Run ID and checks the
response. Creation rechecks unfinished Case runs inside its existing transaction.

Explicit preview returns the question, retrieved Segment count/UTF-8 bytes, budget,
notice and consent hash. Ordinary reads of question Runs return `preview=null`;
reading history does not require a new preview. A Run adds
`analysis={question,basis_sha256,basis_status,facts}` (null for older Runs).
`basis_status` is `current`, `changed` or `unavailable`; the latter two disable
execute/retry. History preserves original question, answer and basis status;
old unbound Runs explicitly use `legacy` rather than claiming current evidence.
Answers add `fact_references` alongside original-material citations. Fact snapshots
retain value/version, confirmation basis, actor/reason/time and original quoted
excerpt metadata. They do not replace current Claim authority.

The original question and retrieval expression are distinct stage inputs.
`validate.question.v1` uses `validate.question.input.v1` and `evidence_backed_answer.v2`.
Fact references must match Case, Claim, version, value hash and usable authority.
Original quote intervals associated with corrected/unsettled facts cannot override
that authority. This bounded check is not a general semantic-entailment guarantee.
Basis is checked before reservation, repair and checkpoint finalization; an incurred
call remains charged when changed evidence prevents checkpoint commit. Reanalysis
creates a new bound Run after a fresh explicit preview, without rewriting history.
See the [slice record](evaluation/case-question-analysis-20260919.md).

### Earlier v2 transport and compatible two/seven-stage execution

The following records the earlier transport. The v3 additions above supersede its
response version, question-mode fields and question-mode command bodies; its old
consent calculation and stage semantics remain for compatible callers.

This is a separate product contract and does not replace or relabel P1-01:

| Method | Path | Success | Request body |
|---|---|---:|---|
| GET | `/api/v1/cases/{case_id}/live-agent` | 200 | none |
| POST | `/api/v1/cases/{case_id}/live-agent` | 201 | `LiveAgentAuthorizationRequest` |
| POST | `/api/v1/cases/{case_id}/live-agent/execute` | 200 | none |
| POST | `/api/v1/cases/{case_id}/live-agent/retry` | 200 | none |
| POST | `/api/v1/cases/{case_id}/live-agent/recover` | 200 | none |
| POST | `/api/v1/cases/{case_id}/live-agent/cancel` | 200 | none |

`LiveAgentAuthorizationRequest` has `additionalProperties=false` and exactly two required fields: a lowercase 64-character `preview_sha256` and `confirm_read_only: boolean`. The server requires the boolean to be true and the hash to equal a freshly computed preview. There is no API field for host, API key, Provider, model, raw Provider output, checkpoint, tool or budget.

`LiveAgentResponse` returns `live_version=live_agent_read_only.v2`, capability status (`disabled`, `unavailable`, or `ready`), server Provider/model, `supported_case_types=[warranty]`, one preview, the latest optional Run and compact `run_history`. The preview contains only Case type, Segment count, exact aggregate UTF-8 bytes, whole-Case maximum calls/input/output/total tokens/CNY cost, the read-only notice and the consent hash. It contains no Segment body, prompt, key or path. `AgentRunResponse` and each stage expose the actual persisted Provider/model; the Run and every history entry include the required `stage_order`. History otherwise exposes only version, identity, lifecycle/answer status, calls, total tokens and timestamps.

S1 creates production Runs with `stage_order=[extract, validate]` and ID prefix `portfolio-live-v2-`. A successful run without repair or retry makes exactly two model-adapter calls: candidate extraction, then evidence retrieval/answering. It does not call `normalize`, `link_claims`, `classify_case`, `check_gaps` or `propose_actions`, and creates no placeholder checkpoint for them. The internal validate input is `validate.rag.input.v2` with exactly one prior checkpoint reference (`extract`); answer/output/checkpoint authority is unchanged.

Existing `portfolio-live-` IDs without the `v2-` marker retain the seven-stage order (`extract`, `normalize`, `link_claims`, `classify_case`, `check_gaps`, `propose_actions`, `validate`), original consent-scope calculation and `validate.rag.input.v1` with six prior references. Reads and eligible continue/retry/recover commands use that original order; no history is rewritten or silently upgraded. The response's `live_version` describes the current transport, while each Run's `stage_order` identifies its execution plan. This uses existing persisted Run IDs and adds no table, migration or dependency.

Create never calls the Provider. If no Run exists it creates version 1; after a terminal Run it may create the next version only with a fresh current preview hash and confirmation, preserving prior Runs. Execute requires the latest queued live-mode Run whose durable ID matches the current preview. Retry/recover/cancel retain their existing authority. Registry profiles include fixed OpenAI, Groq, Mistral, DeepSeek, Gemini and Anthropic adapters; DeepSeek alone allows the two fixed V4 model IDs. Live routes still accept no provider/profile/endpoint/model values.

The Web mirrors this closed contract: preview first, one confirmation checkbox, then fixed create/execute/retry/recover/cancel commands. Progress uses the Run's `stage_order`; new Runs show candidate extraction and evidence retrieval/answering, while legacy Runs and history are explicitly labeled as the old seven-stage flow. There is no general chat, SSE, arbitrary retrieval, tool invocation or external action transport. Current S1 verification is tracked in the [slice record](evaluation/production-agent-simplification-20260918.md); historical seven-stage model reports are not new two-stage quality evidence.

P1-05C adds no HTTP operation, request field, response field, or error detail. Its structural categories and fixed-field business failure codes belong only to the direct local evaluation report. Product responses continue to expose only the existing stable redacted error code; they never expose a candidate, prompt, Evidence Context body, Schema path, checkpoint, private remote error, or credential.

P1-05D adds no operation or browser-controlled field. Existing `/live-agent` capability responses may now report `provider: "deepseek"` for the fixed `deepseek-v4-flash` profile. The server still owns endpoint, model, byte/time limits, data-use confirmation, client injection, preview evidence scope and budget; authorization requests remain limited to the preview hash and read-only confirmation.

## Versioning

- Breaking HTTP changes require `/api/v2` or an explicit compatibility period.
- JSON Schema files include their own semantic version.
- Case templates use integer versions and remain readable after upgrade.
- Evidence Pack uses `format_version` independent from application version.
- Whole-authority Backup uses its own integer `format_version`; it is not an Evidence Pack or Artifact row.
