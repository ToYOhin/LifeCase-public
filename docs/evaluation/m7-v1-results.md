# M7-02 Thirty-case Evaluation

- Result: **PASS**
- Corpus: `m7-v1` (`a83f1e31b2939826de74381d741e7efbc9190f799c791342a74ea81713c3723c`)
- Mode: offline recorded synthetic outputs; this is not live-model quality evidence.
- Cases: 30; distribution: return_refund=8, warranty=8, reimbursement=7, subscription_cancellation=4, travel_logistics=3
- Product-processed coverage: 27/30 (target >= 27) — PASS
- Evaluation-only recorded cases: 3/30; travel/logistics is not a reviewed UI template.
- Critical fields: 148/150 (98.67%; target >= 90%)
- Evidence validity: 144/144 (100.00%; target 100%)
- Deadline safety: 30/30 (100.00%; target 100%)
- Action quality: 11.60/12 average (target >= 10/12); safety failures=0
- Action rubric values are fixture-authored synthetic review records, not an independent user study.

## Unsupported product capabilities

- `travel_logistics`: evaluation record only; no reviewed Case template or Web selector.

## Failures

- `m7-warranty-03`: critical_field_order_or_reference
- `m7-reimbursement-06`: critical_field_order_or_reference

## Reproduce

```text
uv run python -m lifecase.evaluation fixtures/synthetic-cases/m7-v1
```
