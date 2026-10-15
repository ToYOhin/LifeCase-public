> 历史候选快照：原 2026-08-14 检查清单。后续 v0.2.0 已正式发布；下面的未授权/未发布表述只属于当时阶段，不是当前状态。见 [当前发布清单](../RELEASE_CHECKLIST.md)。

# Release Checklist

Updated: 2026-08-14

This checklist prepares the local `v0.2.0` Windows daily-use Release candidate. Its deliverables are the source repository, `scripts/lifecase.ps1`, and an optional unsigned per-user `LifeCaseSetup.exe`. It does not claim MSI, portable mode, native UI, auto-update, hosted service, production readiness, or completed real-personal-data dogfood.

## Evidence baseline

- [x] Historical annotated `v0.1.0` and formal GitHub Release remain fixed at `b6decb4b052e7d8dfc23cafc4af60a5e529aae4f`; Release `LifeCase Portfolio MVP v0.1.0` is published, non-draft, non-prerelease, and has no assets.
- [x] P1-02～P1-12 plus the Linux CI typing closeout are on remote `main@ee23aabadbafe05a30ec322393eaf3f749a64466`; run `31707367477` passed backend and Web for that exact SHA.
- [x] Local P1-13, source candidate, Windows launcher and per-user installer commits remain ahead of remote main and have no exact-head CI result.
- [x] Historical, parent, synthetic, local, dogfood, tag, and formal Release evidence remain separate. Neither run `31707367477` nor the historical `v0.1.0` Release proves the final `v0.2.0` candidate SHA.

## Candidate scope and version

- [x] `pyproject.toml`, root `package.json`, `apps/web/package.json`, Python `__version__`, health response, and OpenAPI metadata declare `0.2.0` consistently.
- [x] `package-lock.json` mirrors only the workspace version change; `uv.lock` additionally pins PyInstaller and its transitive tools in the packaging-only group. Runtime application dependencies are unchanged.
- [x] `docs/releases/v0.2.0.md` provides concise candidate notes, daily-use instructions, evidence boundaries, and the publication gate.
- [x] The candidate supports both the source checkout plus `scripts/lifecase.ps1 setup` and an optional self-contained `LifeCaseSetup.exe` per-user installation.
- [x] The EXE target needs no source/Python/uv/Node/npm, but remains unsigned and unpublished. No MSI, portable mode, native desktop UI, container image, cloud deployment or auto-updater is claimed.

## P1-02～P1-13 inclusion audit

- [x] P1-02: the independent loopback-only Ollama adapter boundary is included; it is not a successful seven-stage model result or a product fallback.
- [x] P1-03: deterministic evaluator/diagnostics are included, while the live-local Ollama quality gate remains `FAIL`; the route remains stopped and no model download/run is part of release verification.
- [x] P1-04: the explicit warranty-only, Case-scoped, read-only Live Agent composition remains default-disabled and consent-bound.
- [x] P1-05A: fixed Registry profiles and offline Groq composition remain capability-gated; no real account, ZDR, quality, cost, or retention claim is added.
- [x] P1-05B: fixed Mistral composition remains offline-proven; the separate moving-alias smoke and failed Large 3 lane are not fixed-profile acceptance.
- [x] P1-05C: safe content-free diagnostics and authority-preserving quote normalization remain fail-closed; Mistral post-correction acceptance remains `FAIL / NOT ESTABLISHED`.
- [x] P1-05D: fixed DeepSeek composition and the bounded answered/unknown synthetic acceptance are included without extending them to personal data, ZDR, dogfood, or production claims.
- [x] P1-06/P1-07: repeatable synthetic demo controls and the result-first evidence dossier are included; the historical port-8765 showcase is not presented as the release entry.
- [x] P1-08/P1-09A: progressive five-area Case workflow and formal density refinement are included as presentation-only behavior over unchanged authority.
- [x] P1-10: the read-only capability catalog remains secret-free and performs no hidden model start or Provider mutation.
- [x] P1-11: allowlisted fixed-model settings use Windows Credential Manager; response contracts never expose a Key, and release verification neither reads nor restores a Provider credential.
- [x] P1-12: fixed DeepSeek V4 Flash/V4 Pro selection, persisted actual model identity, safe retrieval fallback, and consent-bound version history are included. The retained synthetic pair remains version 1 `unknown` and version 5 `answered`; all five Runs remain preserved.
- [x] P1-13: public README, portfolio walkthrough, resume path, and three-viewport repository screenshots use persisted synthetic results only and add no product behavior or model call.

## Invariant product and evidence boundaries

- [x] Default Provider remains disabled; P1-01 remains the server-owned deterministic Fake Provider demo.
- [x] `/live-agent` remains warranty-only, Case-scoped, read-only, and without tools, external writes, general chat, or SSE.
- [x] API routes, request/response schemas, business authority, persistence schemas and Provider defaults are unchanged. Only the isolated packaging dependency group and Windows launch/install wrappers are added.
- [x] Fixed M7 distribution and product-processing result remain 27/30; travel/logistics remains evaluation-only.
- [x] Seven-day private dogfood remains `NOT_RUN`; synthetic acceptance is not real-use or sustained daily-use evidence.
- [x] P1-03 Ollama remains `FAIL`; no Ollama service/model is downloaded, started, or product-composed by this closeout.
- [x] M8 remains unstarted and requires separate demand, permission, and privacy review.
- [x] No model—real, paid, local, or Fake—is invoked by the candidate preparation or deterministic release gate.
- [x] No Provider Key is read, printed, exported, recovered, or reused.

## Local candidate verification

- [x] `scripts/verify.ps1` passes: Ruff lint/format over 183 API files, strict Mypy over 131 source files, 538 Pytest tests, Web TypeScript, 22 Vitest files / 72 tests, and the Vite production build. The one documented Starlette/TestClient transition warning remains.
- [x] All 38 tracked Markdown files and 28 local links resolve with 0 broken links.
- [x] Project/workspace/runtime version declarations and lockfile mirrors are exactly `0.2.0`; dependency metadata is unchanged.
- [x] `git diff --check` passes.
- [x] Candidate changes contain no credential, private path, personal data, runtime database/export/archive/log, or tracked binary installer artifact; generated EXEs remain outside Git.
- [x] `AGENTS.md`, `.github/copilot-instructions.md`, `docs/HANDOFF.md`, and `docs/NEXT_PROMPT.md` remain ignored and untracked.
- [x] Five-axis correctness/readability/architecture/security/performance review finds no blocking issue.

## Publication gates — not authorized by this candidate prompt

- [ ] Explicitly authorize pushing the final local candidate commit to `main`.
- [ ] Wait for backend and Web GitHub Actions jobs to pass for that exact pushed SHA; historical or parent CI cannot substitute.
- [ ] Reconfirm local HEAD, `origin/main`, remote main, clean worktree, exact-SHA CI, and historical `v0.1.0` Release truth.
- [ ] Separately authorize creation and push of annotated tag `v0.2.0` at the exact verified SHA.
- [ ] Separately authorize the matching formal GitHub Release using `docs/releases/v0.2.0.md`, then explicitly decide whether to upload the unsigned installer asset and its SHA-256 manifest.
