# ADR-0002: Evidence-first facts

Status: Accepted

Date: 2026-08-01

## Context

Generative extraction can be useful but may omit uncertainty or invent plausible details. LifeCase decisions often depend on exact amounts, dates, identifiers, and promises.

## Decision

Every factual Agent Claim must reference stable source segments. Inference and unknown states remain distinct from extracted facts. User corrections append history, and original evidence remains immutable.

## Consequences

- extraction must preserve page/coordinate/segment locations;
- schemas and UI must expose provenance;
- evaluation measures citation validity, not only field accuracy;
- storage is more structured than a chat transcript or vector index alone.
