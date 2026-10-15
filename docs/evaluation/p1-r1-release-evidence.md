# P1-R1 Release Evidence

Date: 2026-08-09

This note records only synthetic, local, non-network release evidence for the P1-01 deterministic Portfolio Agent Demo. It contains no Case body, database, object path, detailed log, credential, or private runtime error.

## OpenAPI and shared-schema audit

The current FastAPI application generated OpenAPI 3.1.0 read-only. Canonical JSON with sorted keys and compact separators has SHA-256 `cfca13156308d2027b5106aa05c1ff5cbfd6dacfdbd74f46a9825d31071e9df7`. The document contains 47 path items; P1-01 contributes five path items and six operations:

| Route | Method | Documented success | Request body | Response model |
|---|---|---:|---|---|
| `/api/v1/cases/{case_id}/agent-run` | GET | 200 | none | `AgentDemoResponse` |
| `/api/v1/cases/{case_id}/agent-run` | POST | 201 | none | `AgentDemoResponse` |
| `/api/v1/cases/{case_id}/agent-run/execute` | POST | 200 | none | `AgentDemoResponse` |
| `/api/v1/cases/{case_id}/agent-run/retry` | POST | 200 | none | `AgentDemoResponse` |
| `/api/v1/cases/{case_id}/agent-run/recover` | POST | 200 | none | `AgentDemoResponse` |
| `/api/v1/cases/{case_id}/agent-run/cancel` | POST | 200 | none | `AgentDemoResponse` |

Those routes, success statuses, absence of client-supplied provider/checkpoint bodies, and response projections match the implementation and `docs/API_CONTRACT.md`. Every operation additionally documents 404, 409, 422, and 503 with `#/components/schemas/ApiErrorResponse`; its required `error` property points to the closed `ApiErrorDetailResponse` fields `code`, `message`, `details`, `retryable`, and `request_id`.

The focused OpenAPI regression verifies all six operations and validates a real disabled-capability error against the same Pydantic envelope. The implementation uses FastAPI's documented [additional-response `model` metadata](https://fastapi.tiangolo.com/advanced/additional-responses/) while retaining the existing `JSONResponse` exception handlers. No runtime status, error mapping, request, success response, service, or product behavior changed.

The shared-schema inventory contains exactly two files:

| File | SHA-256 | Explicit automated validator | Authority result |
|---|---|---|---|
| `packages/contracts/schemas/case.schema.json` | `26110dcb2191efd0d76b046131b9933ffacebe3c3cc447e8fe48002ef5c285d0` | none | not a current cross-boundary authority |
| `packages/contracts/schemas/claim-candidate.schema.json` | `a343c67697601d7f10bdd3c4580d59096832f6587a2e1f5313b0be0360150042` | none | not a current cross-boundary authority |

A repository reference scan found no validator naming either file. Therefore the current count of authoritative shared JSON Schemas is zero. The files must not be treated as a duplicate HTTP contract, P1 response contract, or current Claim authority. Generated FastAPI OpenAPI remains HTTP transport authority.

## Deterministic Fake Provider budget and latency

The focused integration test creates five fresh synthetic warranty Cases. It imports synthetic TXT evidence, creates the queued P1 run, then measures only `POST .../agent-run/execute`. During the test, non-loopback parent-process socket connections fail immediately. Each execution uses the existing server-owned fixed Fake Provider scripts and completes the existing seven stages.

Reproduce from the repository root:

```powershell
uv run pytest -q -s apps/api/tests/integration/test_portfolio_agent_demo_api.py::test_deterministic_agent_demo_usage_and_local_latency_are_bounded
```

Every run produced the same bounded accounting:

| Metric | Fixed observed usage | Existing hard Case ceiling |
|---|---:|---:|
| Provider calls | 7 | 14 |
| Input tokens | 70 | 28,000 |
| Output tokens | 35 | 14,000 |
| Total tokens | 105 | 42,000 |
| Cost | CNY 0.00 | CNY 1.00 |

The 2026-08-09 local sample was:

| Runs | Aggregate | Median | Maximum | Enforced bound |
|---:|---:|---:|---:|---:|
| 5 | 0.129110 s | 0.025807 s | 0.029702 s | 5.0 s per run; 25.0 s aggregate |

This is machine-specific timing for deterministic Python, SQLite, local retrieval, and Fake Provider execution. It is not a benchmark, estimate, or quality claim for OpenAI, a local model, network latency, paid inference, or production load.

## Exact-commit publication evidence

- The reviewed code candidate was published as `8a6e5b606cda8fcaa5fecd405a8672e228ce35a5`; local HEAD, `origin/main`, and remote `refs/heads/main` matched exactly after publication.
- GitHub Actions run `31308695992` completed successfully for that exact SHA. Its `backend` and `web` jobs both reported `success`.
- No tag or GitHub Release was created. This evidence does not authorize either action.

## Remaining release action

- This documentation-only closeout creates a later commit. Before that new SHA can be used as a tag or GitHub Release target, it must be separately authorized for publication and pass its own exact-commit backend/Web CI.
- Dogfood remains `NOT_RUN`; the successful synthetic and CI evidence does not establish live-model quality, sustained real use, or production readiness.
