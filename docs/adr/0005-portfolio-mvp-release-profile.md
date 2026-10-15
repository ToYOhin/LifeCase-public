# ADR 0005: Define a portfolio MVP release profile

Status: Accepted

Date: 2026-08-09

## Context

The original M7 release gate included seven days of private use with at least three real Cases. That gate is useful evidence for a product intended for sustained personal daily use, but LifeCase is currently being completed as a portfolio and interview demonstration project. Requiring elapsed personal use would delay the portfolio outcome without adding evidence about the implemented deterministic architecture, and replacing it with synthetic activity would be misleading.

## Decision

- Define the current deliverable as the **LifeCase Portfolio MVP**.
- Complete the local Portfolio MVP from deterministic repository verification, Windows package acceptance, the synthetic browser operation loop, and the unchanged 27/30 product-processing threshold.
- Make the seven-day private dogfood protocol optional future operational validation. Record it as `NOT_RUN`; do not claim real-use validation, adoption, or production readiness.
- Keep exact-commit backend/Web CI as a separate publication gate. Local completion does not authorize a push and does not establish remote CI for an unpublished commit.
- Keep live model runtime, Agent HTTP/UI, OCR, external connectors, Docker, and M8 outside this decision. Each requires a separately reviewed implementation slice.

## Consequences

M7 is locally complete for the portfolio/demo scope. The existing package and synthetic acceptance evidence remain accurately labelled and are not reclassified as dogfood. A public portfolio release remains unverified until the exact published commit passes remote CI, and any future claim of sustained daily-use readiness requires running the optional dogfood protocol or adopting another explicit operational validation plan.
