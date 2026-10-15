# Contributing

LifeCase is a personal, local-first project. Contributions should preserve evidence provenance and human control. Repository visibility does not grant an open-source license; see the license statement in `README.md`.

## Before changing code

1. Identify the user-visible outcome, acceptance criteria, and affected behavior.
2. Check the branch and existing changes; preserve unrelated work.
3. Read the affected implementation, tests, and relevant contracts. Consult the roadmap when choosing new work, not as a prerequisite for every fix.
4. Treat dated status and release evidence as historical snapshots; verify live state before making current publication claims.

## Development setup

```powershell
uv sync --frozen
npm ci
.\scripts\verify.ps1
```

## Change requirements

- Add meaningful tests for new behavior and regressions; documentation and cosmetic changes do not need implementation-mirroring tests.
- Keep imported personal material out of commits.
- Update API/data/security documentation with semantic changes.
- Add an ADR for a durable architecture or privacy decision.
- Keep commits narrow and descriptive.
- Do not combine dependency upgrades with feature work.
- Keep personal material, secrets, and machine-specific coordination outside Git.

## Validation proportional to the change

- Documentation: check facts, local links, and `git diff --check`; no application build is needed.
- Local code: run the affected tests and relevant lint/type checks first.
- Shared contracts, persistence, cross-layer behavior, or release code: run `scripts/verify.ps1` and the relevant acceptance checks.
- Windows API changes: also run `uv run mypy --platform linux apps/api/src` and the affected Windows tests. Ubuntu CI alone does not verify native Windows integration or the packaged EXE.
- Packaging: follow `docs/WINDOWS_INSTALLER.md` and `docs/RELEASE_CHECKLIST.md`; installer acceptance changes current-user installation state and is not a routine documentation check.

Run resource-heavy checks serially. Repeat passing checks only after a relevant change, failure, or unresolved concern. Ordinary tests use synthetic fixtures or deterministic fakes, not live billable models or personal data.

## Commit style

Use an imperative summary with an optional roadmap ID:

```text
M1-02 add immutable source storage
fix approval hash validation
docs clarify explicit deadline semantics
```

## Pull request checklist

- [ ] Scope and acceptance criteria are clear.
- [ ] Checks appropriate to the change pass; unverified behavior is stated.
- [ ] No credentials or personal data are included.
- [ ] User-visible and semantic changes are documented.
- [ ] External side effects remain gated or disabled.
- [ ] Changed product facts and contracts are documented; unrelated historical records are preserved.
