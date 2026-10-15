# P1-05C Mistral Agent Compatibility Evidence

Updated: 2026-08-12

## Scope

This is a manual, synthetic-only compatibility lane for the reviewed warranty Agent. It used
the fixed `mistral-large-2512` model through an injected process-local client and the existing
seven-stage evaluator. It did not change the product Registry profile, which remains fixed to
`mistral-small-2603`; it did not exercise `/live-agent`, organization ZDR, Credential Locker,
personal data, an external tool, or an external effect.

The credential was entered through a hidden process prompt. It was not written to source,
SQLite, an environment file, a report, a screenshot, or a retained script. Because the value
was exposed in the conversation, it must be revoked.

## Source contract

Mistral recommends custom structured outputs for pipeline stages while still iterating on clear
prompts, and recommends complete, hierarchically structured instructions. Mistral Large 3 is a
fixed general-purpose model with Chat Completions and Structured Outputs support.

- <https://docs.mistral.ai/studio/conversations/structured-output>
- <https://docs.mistral.ai/inference/prompting>
- <https://docs.mistral.ai/models/mistral-large-3-25-12>

## Safe diagnostic result

The first two-Case Large 3 compatibility run reached strict JSON Schema output but failed both
Cases at `business_validation`: 0/2 seven-stage completion, 0/12 expected Claims, 0 citations,
and 2/2 restart recovery. Four provider attempts reported 1,888 input and 8,138 output tokens;
the list-price equivalent was approximately USD 0.013151. Raw responses were not retained.

After the evaluator gained field-level closed codes, a bounded diagnostic run confirmed the
specific failing constraint as
`extract.merchant_or_manufacturer.quote_authority`: one Case reached the candidate plus one
repair and still failed; the other stopped at a separate `remote_protocol_error`. This code
contains only the fixed reviewed field name and failure class. It contains no candidate value,
Schema path, source/Case identifier, evidence text, hash, remote body, or machine path.

## Contract correction

The extraction prompt now states the complete six-field disposition and citation contract. The
checkpoint handler treats model-proposed quote offsets and quote hashes as untrusted derived
metadata. A valid existing excerpt remains unchanged, preserving the deterministic Fake Demo.
When the model selects an authoritative source/segment/hash but proposes invalid quote metadata,
the server replaces only that metadata with a full-Segment citation before canonical validation
and persistence. A wrong source, segment, or Segment hash still fails closed and can receive only
the existing single repair. No evidence body or raw candidate is persisted by this correction.

Offline regression proves exact Fake citations remain exact, invalid quote metadata is normalized
to the authoritative Segment, wrong Segment authority still fails with a field-level code, and
candidate/inferred Claim, recovery, budget, RAG, and read-only boundaries remain in force.

## Final acceptance status

`FAIL / NOT ESTABLISHED`.

The post-correction Large 3 acceptance could not establish two successful model runs. One
two-Case attempt contained a 120-second transport timeout and one further schema failure while the
observer still reported the pre-normalization candidate; the observer was then corrected to apply
the same normalization before reporting a final business code. The subsequent single bounded
diagnostic stopped before any HTTP response with `remote_protocol_error`, so no post-correction
candidate reached the final classifier. Reservation tokens from provider failures are not treated
as provider usage or cost.

Mistral Medium 3.5 was not called. The declared condition for trying it was not met: the corrected
contract has offline proof, but Large 3 has not yet produced stable post-correction evidence that
separates remaining model compatibility from the observed transport failures. A later attempt
must use a replacement credential, a stable network path, an explicit cost ceiling, and the same
no-raw-output rule.

No result in this record is dogfood, exact-commit CI, fixed product-profile acceptance, ZDR proof,
or production readiness.

## Deterministic verification

The final offline gate passes Ruff lint and format over 166 API files, strict Mypy over 122 source
files, 482 Pytest tests, Web TypeScript, 13 Vitest files / 48 tests, the Vite production build,
unchanged M7 corpus/report byte checks, Markdown local-link validation, `git diff --check`, and
sensitive/dependency/artifact/ignored-file audits. The existing Starlette TestClient transition
warning remains. No model network request was made during this final verification.
