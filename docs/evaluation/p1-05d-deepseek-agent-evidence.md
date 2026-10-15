# P1-05D DeepSeek Agent Compatibility Evidence

Updated: 2026-08-12

## Scope

This direct-only synthetic evaluation fixes the official endpoint to
`https://api.deepseek.com/chat/completions` and the model to `deepseek-v4-flash`. It uses
non-thinking, non-streaming JSON Object output with no tools, then applies the unchanged local
canonical JSON Schema, business authority, and one-repair ceiling. After the live gate passed, the
same adapter was moved behind a fixed product `ProviderRegistry` profile and the existing
`/live-agent` boundary. Product composition still requires an injected process-local client and an
explicit server assertion that only approved synthetic/private data will be used; the default is
still disabled.

DeepSeek documents `response_format={"type":"json_object"}` rather than direct response JSON
Schema enforcement. It requires the prompt to request JSON and recommends an example/clear shape;
LifeCase therefore sends the exact canonical Schema as an instruction but treats the result as an
untrusted JSON candidate. The current fixed model and prices were taken from the same official
model/pricing contract.

- <https://api-docs.deepseek.com/guides/json_mode/>
- <https://api-docs.deepseek.com/api/create-chat-completion>
- <https://api-docs.deepseek.com/quick_start/pricing/>

The current privacy policy says user inputs may be collected and applies to the API. No reviewed
ZDR-equivalent product contract was established, so only temporary synthetic material was sent and
personal evidence remains forbidden. The product profile is intentionally labelled synthetic-only
and fails closed until the server-side data-use assertion is present.

- <https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html>

## Safety and cost controls

- The credential is accepted only by a hidden process prompt and a fixed-host standard-library
  HTTPS client. It is absent from source, configuration, SQLite, logs, reports, screenshots, and
  retained scripts.
- The adapter sends `thinking=disabled`, `stream=false`, `tool_choice=none`, no tools, and no
  arbitrary endpoint/model parameter.
- Raw response objects, prompt/evidence bodies, private remote bodies, reasoning, identifiers, and
  machine paths are not reported or persisted. Provider parse failures are detached from their
  original exception context.
- Before every call, the evaluator reserves a conservative UTF-8-byte input-token upper bound plus
  the requested maximum output tokens at current list prices. A call is refused if that reservation
  could cross the aggregate USD 0.02 cap.
- Case 2 is allowed only if Case 1 passes all seven stages, Claim/citation authority, answered
  quality, restart recovery, sensitive-marker scan, and cost controls.

## Live result

`PASS / 2 OF 2`.

The validate contract failure was traced to derived quote metadata: the model was being asked to
calculate an exact excerpt SHA-256 even though the server already owned the authoritative Segment.
Validate now follows the existing extraction rule. It accepts only a current same-Case
source/segment/sequence/page/Segment-hash match, then replaces invalid derived offsets/hash with a
full-Segment citation; wrong or cross-Case authority still fails closed. The prompt asks the model
to copy server authority rather than calculate a new hash.

The corrected run completed one answered and one unknown temporary warranty Case. Both finished all
seven stages and restart recovery. Across 14 provider calls, the evaluator recorded 8,223 input,
3,535 output and 11,758 total tokens; elapsed time was 17.432119 and 15.480177 seconds (32.912296
aggregate, 16.456148 median, 17.432119 maximum). Nominal list-price cost was USD 0.00205321 under the
USD 0.02 cap. Claims were 12/12 correct, citations 13/13 valid, answer quality 2/2, and no run,
Schema, authority, sensitive-data, failure-code, failure-stage or business-code failure remained.

This establishes synthetic fixed-model Agent acceptance and supports explicit read-only product
composition. It does not establish suitability for real personal material, ZDR, private dogfood,
external effects, exact-commit CI, publication, or production readiness.

## Deterministic verification

The later product launcher retained the same adapter and hidden credential boundary. Its first
synthetic product Case failed safely at the former 1,000-output-Token ceiling with closed class
`finish_reason_length`. A DeepSeek-only change raised the request reservation to 2,000 and the
Case output ceiling to 28,000 while preserving 14 calls, one repair, canonical validation and all
authority controls. A fresh `/live-agent` Case then completed seven stages in 16.563378 seconds,
created five candidate Claims and returned `unknown`; this is product-path runtime evidence, not
personal-data or external-effect evidence.

The final offline gate passes Ruff lint and format over 171 API files, strict Mypy over 125 source
files, 498 Pytest tests, Web TypeScript, 13 Vitest files / 48 tests, and the Vite production build.
Unchanged M7 corpus/report byte checks, 25 reviewed Markdown files / 6 local links / 0 broken, diff
checks and sensitive/dependency/artifact/ignored-file audits also pass. The existing
Starlette TestClient transition warning remains. No model request belongs to or was made by this
deterministic gate.
