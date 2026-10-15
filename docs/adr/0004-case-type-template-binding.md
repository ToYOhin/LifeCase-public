# ADR 0004: Separate Case type from template identity

Status: Accepted

Date: 2026-08-02

## Context

The existing Case API and database use `warranty` as `case_record.type`, while the reviewed workflow resource declares `id: warranty_claim` and `version: 1`. Treating these values as interchangeable would make persisted identity ambiguous and would complicate future template versions or additional workflows.

## Decision

- Keep `warranty` as the business Case type.
- Keep `warranty_claim` as the workflow-template ID.
- Define the closed typed binding `warranty -> warranty_claim@1`.
- Persist `type`, `template_id`, and `template_version` separately and validate their combination on Case reads and template evaluation.
- Backfill existing warranty version 1 Cases to `template_id = warranty_claim`; do not rename or migrate their Case type.
- Retain the M1 create-request field name `template_id` temporarily for compatibility, documenting that its current `warranty` value is a Case-type selector.

## Consequences

Cases restore the exact reviewed workflow identity after restart and fail closed if the binding is unknown or inconsistent. Future Case types or template versions require an explicit registry and migration decision. The current transport field name remains imperfect but avoids an unrelated API/client breaking change in M2-03.
