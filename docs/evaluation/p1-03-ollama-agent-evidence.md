# P1-03 Controlled Ollama Agent Evidence

Updated: 2026-08-12

## Scope

This record covers local-only evaluation of the existing P1-02 Ollama adapter through the existing seven-stage warranty Agent services, followed by one narrow strict JSON Schema compatibility diagnosis. It does not add or prove an Ollama HTTP/Web product path, general chat, SSE, tool expansion, connector, external write, M8 capability, real-data dogfood, or production readiness.

The implementation preserves these boundaries:

- Provider selection remains disabled by default;
- the P1-01 API/UI continues to use only the server-owned deterministic Fake Provider;
- Ollama remains a distinct Provider and can reach only `127.0.0.1:11434/api/chat`;
- `stream=false`, `think=false`, temperature 0, the exact stage JSON Schema, the canonical local validator, and the single-repair ceiling remain fixed;
- every evaluation Case and its authority are fresh, synthetic, temporary, and deleted after the run;
- the report contains no source body, Case/Source identifier, database/object path, checkpoint, raw response, credential, or detailed log.

Ollama documents `/api/chat` structured output through the `format` JSON Schema field, streaming as enabled by default unless disabled, and `think` as a boolean-capable request setting. LifeCase still treats the response as untrusted and applies its own strict Schema and business-authority validation:

- <https://docs.ollama.com/api/chat>
- <https://docs.ollama.com/capabilities/structured-outputs>
- <https://docs.ollama.com/capabilities/thinking>

## Gate

Two to five runs must all complete the fixed seven stages and recover an equal safe projection after SQLite restart. Exact Claim value/disposition accuracy must be at least 90%; all checked Claim and answer citations must be valid; unsupported Claims must be zero; the matched goal must be `answered`; the unmatched goal must be fail-closed `unknown` without citations. Calls, tokens, and per-run/aggregate elapsed time must be reported within explicit bounds. Passing time or recovery bounds cannot compensate for failed quality or authority gates.

## Safe diagnostic classification

The evaluation-only observer compares each provider-neutral JSON candidate with the same requested strict Schema before the canonical runner validator receives it. It emits only a closed set of content-free categories: `non_json`, `envelope_fields`, `stage_const`, `checkpoint_shape`, `type`, `enum`, `additional_fields`, `length_boundary`, `pattern`, `numeric_boundary`, and `business_validation`. P1-05C later added a separate canonical extraction diagnostic that may name only one fixed reviewed warranty field (or `checkpoint`) and a closed business failure class. P1-05D adds only a fixed failed-stage name to the v4 safe report. Neither layer retains a candidate value, JSON path, validator message, checkpoint, identifier, hash, evidence body, or raw response, and neither replaces or relaxes canonical Schema or business-authority validation.

Offline regression proves classification for representative non-JSON, missing envelope, stage const, checkpoint shape, type, enum, additional-field, length, pattern, numeric-boundary, and schema-valid business-authority failures. The existing deterministic pass remains category-free. No live socket is opened by these tests.

The 2026-08-11 `qwen2.5:3b` outputs were not retained, so their `agent_schema_invalid` results cannot be retroactively assigned a narrower category. This record deliberately does not infer one.

## 2026-08-11 live-local result

Model: `qwen2.5:3b`

Result: `FAIL`

| Evidence | Result |
|---|---:|
| Fresh synthetic Cases | 2 |
| Seven-stage completions | 0/2 |
| Restart recovery | 2/2 |
| Stable failure | `agent_schema_invalid` |
| Provider calls | 4 |
| Input tokens | 1,826 |
| Output tokens | 9,082 |
| Total tokens | 10,908 |
| Run elapsed seconds | 175.207385, 150.092068 |
| Aggregate seconds | 325.299453 / 600 bound |
| Median seconds | 162.649727 |
| Maximum seconds | 175.207385 / 300 bound |

Each Case used the initial call and one permitted repair, then failed strict local Schema/business validation before extraction commit. Therefore no Claims or citations were available to score, and answered/unknown output quality was not established.

## 2026-08-12 low-resource diagnostic rerun

Only `qwen3:4b` was installed, so no model was downloaded or updated. The run used the exact original two-Case gate with CPU-only inference, one parallel request, Ollama process priority `Idle`, and affinity limited to 2 of 20 logical processors. `CUDA_VISIBLE_DEVICES=-1`, `GGML_VK_VISIBLE_DEVICES=-1`, and `OLLAMA_LLM_LIBRARY=cpu_avx2` kept both CUDA/Vulkan GPU inference disabled. Ollama was started only for this run and stopped afterward.

Model/configuration: `qwen3:4b`, CPU-only low-resource configuration

Result: `FAIL`

| Evidence | Result |
|---|---:|
| Fresh synthetic Cases | 2 |
| Answered/unknown scheduled | 1 / 1 |
| Seven-stage completions | 0/2 |
| Restart recovery | 2/2 |
| Stable failure | `agent_provider_failure` |
| Safe Schema diagnostic categories | none observed |
| Provider calls | 2 |
| Reserved input tokens | 8,000 |
| Reserved output tokens | 8,000 |
| Reserved total tokens | 16,000 |
| Run elapsed seconds | 120.018506, 120.033637 |
| Aggregate seconds | 240.052143 / 600 bound |
| Median seconds | 120.026072 |
| Maximum seconds | 120.033637 / 300 bound |
| Claims correct / expected | 0 / 12 |
| Citations checked / valid | 0 / 0 |
| Unsupported Claims | 0 |
| Correct answer runs | 0 |

Both Cases reached the unchanged 120-second first-stage timeout before the adapter returned a provider-neutral candidate. Consequently the canonical validator and diagnostic classifier had no candidate to inspect, and no structural category is claimed. The token figures are conservative reservation charges recorded by the existing budget authority after timeout, not provider-reported generation usage. Recovery and outer time bounds passed, but all seven-stage, Claim, citation, answered, and unknown quality gates remained unsatisfied.

Ollama's official GPU and troubleshooting documentation identifies the environment controls used for the CPU-only boundary:

- <https://docs.ollama.com/gpu>
- <https://docs.ollama.com/troubleshooting>

## Decision

The unchanged P1-03 gate remains `FAIL`. No reviewed installed model/configuration has passed it: the historical model failed strict validation, while the currently installed low-resource configuration produced no candidate before timeout. This does not justify routing Ollama into P1-01, relaxing Schema or authority checks, adding tolerant parsing or unbounded repair, claiming successful live-model Agent quality, or entering M8. The product baseline remains disabled/Fake.

## Repository regression

Focused Ruff, strict Mypy, and five diagnostic/evaluation tests pass. One clean full serial repository run passes Ruff lint, Ruff format over 158 API files, strict Mypy over 117 source files, 439 Pytest tests, Web TypeScript, 12 Vitest files / 47 tests, and the Vite production build. A later repeat had one transient Windows daily-use startup failure after 438 other tests passed; the failed PID and listener were absent afterward, and the exact isolated acceptance immediately passed. The fixed M7 corpus and canonical JSON/Markdown reports pass byte checks with unchanged 27/30 product processing. The existing single Starlette TestClient transition warning remains. Markdown/diff/sensitive/ignored audits and exact local commit evidence are recorded at closeout; none of these deterministic checks turns either failed live-model result into a pass.
