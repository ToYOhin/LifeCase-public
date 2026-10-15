# ADR-0001: Local-first runtime and storage

Status: Accepted

Date: 2026-08-01

## Context

LifeCase handles receipts, messages, identity details, and dispute evidence for one user. A cloud-first architecture would add deployment, authentication, cost, and privacy obligations before product value is proven.

## Decision

Run the API on loopback and store structured state in local SQLite with evidence bytes in a local content-addressed object directory. Cloud model calls receive only explicitly scoped material. Existing cases remain readable and exportable without a model connection.

## Consequences

- setup and backup must work well on Windows;
- multi-device sync and collaboration are postponed;
- local filesystem and database recovery need strong tests;
- later cloud sync requires a new ADR and threat model.
