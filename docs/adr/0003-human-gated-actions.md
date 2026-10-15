# ADR-0003: Human-gated external actions

Status: Accepted

Date: 2026-08-01

## Context

Sending a message, uploading documents, submitting a form, paying, or signing can create financial, privacy, or legal consequences. A conversational confirmation without payload binding is vulnerable to later mutation.

## Decision

Classify tools A through D. External Risk-C actions require per-action confirmation bound to a canonical payload hash. Risk-D actions are unavailable in the MVP. MVP ships no email-send or browser-submit tool.

## Consequences

- local drafts and exports deliver value before external automation;
- future executors must revalidate payload hashes immediately before execution;
- connector additions require security review, tests, and a new ADR when scopes expand.
