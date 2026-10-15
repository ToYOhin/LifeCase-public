# Synthetic case fixtures

Only generated or irreversibly redacted material may be committed here. Never copy a real receipt, email, address, account number, signature, barcode, QR code, or Evidence Pack into the repository.

Each fixture must document its expected claims, evidence gaps, deadline kinds, actions, and prohibited actions.

`m7-v1/` is the fixed M7-02 thirty-case corpus. Each Case contains one synthetic input,
separate expected Claim/gap/Deadline/Action labels, metadata, and an independently scored
recorded observation. The recorded observations exercise the offline scoring and safety
contract; they are not live-model quality evidence. Subscription cancellation and
travel/logistics remain evaluation-only and are not product template registrations.

Regenerate or verify the byte-stable corpus from the repository root:

```text
uv run python scripts/generate_m7_corpus.py
uv run python scripts/generate_m7_corpus.py --check
```
