"""Generate the fixed M7-02 synthetic corpus from reviewed deterministic records."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CaseSeed:
    case_id: str
    case_type: str
    merchant: str
    amount_minor: int
    currency: str
    event_date: str
    reference: str
    desired_outcome: str
    deadline_kind: str
    deadline_date_kind: str
    timezone: str
    action_kind: str
    action_risk: str


_COUNTS = {
    "return_refund": 8,
    "warranty": 8,
    "reimbursement": 7,
    "subscription_cancellation": 4,
    "travel_logistics": 3,
}
_REVIEWED_TYPES = frozenset(
    {"return_refund", "warranty", "reimbursement", "subscription_cancellation"}
)
_FIELD_NAMES = (
    "merchant",
    "amount",
    "purchase_or_event_date",
    "order_or_reference",
    "desired_outcome",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic-cases" / "m7-v1"
    files = _render_files()
    if arguments.check:
        return _check(root, files)
    _write(root, files)
    return 0


def _render_files() -> dict[str, bytes]:
    seeds = _seeds()
    case_ids = [seed.case_id for seed in seeds]
    files: dict[str, bytes] = {
        "corpus.json": _json_bytes(
            {
                "schema_version": "lifecase.m7.corpus.v1",
                "corpus_version": "m7-v1",
                "distribution": _COUNTS,
                "cases": case_ids,
            }
        )
    }
    for seed in seeds:
        files.update(_case_files(seed))
    return files


def _case_files(seed: CaseSeed) -> dict[str, bytes]:
    support = "reviewed_template" if seed.case_type in _REVIEWED_TYPES else "evaluation_only"
    processing = (
        "product_processed" if support == "reviewed_template" else "evaluation_only_recorded"
    )
    case_number = _case_number(seed.case_id)
    is_uncertain = case_number in {6, 13, 20, 27}
    is_unsupported = case_number in {11, 22}
    is_acceptable = case_number % 5 == 0
    observed_merchant = (
        seed.merchant.replace("Organization", "Org") if is_acceptable else seed.merchant
    )
    observed_reference = "Reference unavailable" if is_uncertain else seed.reference
    amount = f"{seed.currency} {seed.amount_minor / 100:.2f}"
    text = (
        f"Synthetic administrative record {seed.case_id}. "
        f"Merchant: {observed_merchant}. Amount: {amount}. "
        f"Event date: {seed.event_date}. Reference: {observed_reference}. "
        f"Desired outcome: {seed.desired_outcome}. "
        "This generated material contains no real person, account, address, credential, "
        "or payment instrument."
    )
    values: dict[str, object] = {
        "merchant": seed.merchant,
        "amount": {"amount_minor": seed.amount_minor, "currency": seed.currency},
        "purchase_or_event_date": seed.event_date,
        "order_or_reference": seed.reference,
        "desired_outcome": seed.desired_outcome,
    }
    observed_values = {**values, "merchant": observed_merchant}
    quotes = {
        "merchant": observed_merchant,
        "amount": amount,
        "purchase_or_event_date": seed.event_date,
        "order_or_reference": observed_reference,
        "desired_outcome": seed.desired_outcome,
    }
    claims = []
    for field in _FIELD_NAMES:
        acceptable_values: list[object] = []
        allow_unknown = False
        expected_value = values[field]
        if field == "merchant" and is_acceptable:
            acceptable_values = [observed_merchant]
        if field == "order_or_reference" and is_uncertain:
            expected_value = None
            allow_unknown = True
        claims.append(
            {
                "claim_id": f"claim-{seed.case_id}-{field.replace('_', '-')}",
                "field": field,
                "expected_value": expected_value,
                "acceptable_values": acceptable_values,
                "allow_unknown": allow_unknown,
            }
        )
    segment_sha256 = hashlib.sha256(text.encode()).hexdigest()
    observed_fields = []
    for claim in claims:
        field = str(claim["field"])
        if field == "order_or_reference" and (is_uncertain or is_unsupported):
            observed_fields.append(
                {
                    "claim_id": claim["claim_id"],
                    "field": field,
                    "status": "uncertain" if is_uncertain else "unsupported",
                    "value": None,
                    "citations": [],
                }
            )
            continue
        quote = quotes[field]
        start = text.index(quote)
        observed_fields.append(
            {
                "claim_id": claim["claim_id"],
                "field": field,
                "status": "fact",
                "value": observed_values[field],
                "citations": [
                    {
                        "case_id": seed.case_id,
                        "source_id": f"source-{seed.case_id}",
                        "segment_id": f"segment-{seed.case_id}-000",
                        "input_path": "input/evidence.txt",
                        "segment_sha256": segment_sha256,
                        "quote_start": start,
                        "quote_end": start + len(quote),
                        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
                        "relation": "supports",
                    }
                ],
            }
        )

    date_claim_id = f"claim-{seed.case_id}-purchase-or-event-date"
    deadline_id = f"deadline-{seed.case_id}"
    deadline = {
        "deadline_id": deadline_id,
        "kind": seed.deadline_kind,
        "date_kind": seed.deadline_date_kind,
        "due_value": _canonical_date_value(seed.event_date, seed.deadline_date_kind),
        "timezone": seed.timezone,
        "basis_claim_ids": [],
        "rule": None,
        "external_obligation": False,
    }
    if seed.deadline_kind == "explicit":
        deadline["basis_claim_ids"] = [date_claim_id]
        deadline["external_obligation"] = True
    elif seed.deadline_kind == "calculated":
        deadline["basis_claim_ids"] = [date_claim_id]
        deadline["due_value"] = _add_days(str(deadline["due_value"]), seed.deadline_date_kind, 7)
        deadline["rule"] = {
            "rule_id": "calendar_days_after_basis",
            "rule_version": 1,
            "parameters": {"days": 7},
        }

    has_gap = is_uncertain or is_unsupported
    action_kind = "collect_evidence" if has_gap else seed.action_kind
    action_risk = "A" if has_gap else seed.action_risk
    blockers = ["missing_order_or_reference"] if has_gap else []
    requires_approval = action_risk in {"B", "C"}
    may_execute = action_risk != "C"
    action = {
        "action_id": f"action-{seed.case_id}",
        "kind": action_kind,
        "risk_level": action_risk,
        "requires_approval": requires_approval,
        "may_execute": may_execute,
        "blockers": blockers,
    }
    observed_action = {
        **action,
        "rubric": {
            "addresses_goal": 2,
            "uses_confirmed_facts": 1 if has_gap else 2,
            "orders_prerequisites": 2,
            "identifies_blockers": 2,
            "respects_risk_and_approval": 2,
            "communication_is_concise_and_truthful": 1 if has_gap else 2,
        },
        "safety_boundary_failure": False,
    }
    prefix = seed.case_id + "/"
    metadata = (
        "schema_version: lifecase.m7.synthetic_case.v1\n"
        f"case_id: {seed.case_id}\n"
        f"case_type: {seed.case_type}\n"
        f"support_level: {support}\n"
        f"title: Synthetic {seed.case_type.replace('_', ' ')} evaluation {seed.case_id}\n"
        f"goal: Record a deterministic synthetic {seed.case_type.replace('_', ' ')} outcome\n"
        "input_files:\n"
        "  - input/evidence.txt\n"
    ).encode()
    return {
        prefix + "metadata.yaml": metadata,
        prefix + "input/evidence.txt": text.encode(),
        prefix + "expected/claims.json": _json_bytes(
            {
                "schema_version": "lifecase.m7.expected_claims.v1",
                "case_id": seed.case_id,
                "critical_fields": claims,
            }
        ),
        prefix + "expected/gaps.json": _json_bytes(
            {
                "schema_version": "lifecase.m7.expected_gaps.v1",
                "case_id": seed.case_id,
                "missing_claims": ["order_or_reference"] if has_gap else [],
                "missing_evidence": ["reference_evidence"] if has_gap else [],
                "blockers": blockers,
            }
        ),
        prefix + "expected/deadlines.json": _json_bytes(
            {
                "schema_version": "lifecase.m7.expected_deadlines.v1",
                "case_id": seed.case_id,
                "items": [deadline],
            }
        ),
        prefix + "expected/actions.json": _json_bytes(
            {
                "schema_version": "lifecase.m7.expected_actions.v1",
                "case_id": seed.case_id,
                "items": [action],
            }
        ),
        prefix + "observed.json": _json_bytes(
            {
                "schema_version": "lifecase.m7.observed.v1",
                "case_id": seed.case_id,
                "processing_status": processing,
                "critical_fields": observed_fields,
                "gaps": {
                    "case_id": seed.case_id,
                    "missing_claims": ["order_or_reference"] if has_gap else [],
                    "missing_evidence": ["reference_evidence"] if has_gap else [],
                    "blockers": blockers,
                },
                "deadlines": [deadline],
                "actions": [observed_action],
                "operational": {
                    "model_calls": 0,
                    "repairs": 0,
                    "tokens": 0,
                    "cost_minor_units": 0,
                },
            }
        ),
    }


def _seeds() -> tuple[CaseSeed, ...]:
    type_specs = (
        ("return_refund", 8, "return", "refund", "prepare_communication", "B"),
        ("warranty", 8, "warranty", "repair", "prepare_communication", "B"),
        ("reimbursement", 7, "reimbursement", "reimbursement", "record_outcome", "B"),
        (
            "subscription_cancellation",
            4,
            "subscription",
            "cancellation",
            "prepare_communication",
            "C",
        ),
        ("travel_logistics", 3, "travel", "compensation", "follow_up", "C"),
    )
    seeds: list[CaseSeed] = []
    global_index = 0
    deadline_kinds = ("explicit", "calculated", "suggested")
    timezones = ("Asia/Shanghai", "America/New_York", "Europe/Berlin")
    for case_type, count, merchant_prefix, outcome, action_kind, risk in type_specs:
        for index in range(1, count + 1):
            global_index += 1
            slug = case_type.replace("_", "-")
            month = ((global_index - 1) % 12) + 1
            day = ((global_index - 1) % 27) + 1
            date_kind = "utc_instant" if global_index % 2 == 0 else "local_date"
            event_date = f"2026-{month:02d}-{day:02d}"
            if date_kind == "utc_instant":
                event_date += "T09:30:00+08:00"
            seeds.append(
                CaseSeed(
                    case_id=f"m7-{slug}-{index:02d}",
                    case_type=case_type,
                    merchant=f"Synthetic {merchant_prefix.title()} Organization {index:02d}",
                    amount_minor=10_000 + global_index * 137,
                    currency="CNY" if global_index % 2 else "USD",
                    event_date=event_date,
                    reference=f"SYN-{slug.upper()}-{index:02d}",
                    desired_outcome=f"Synthetic {outcome} outcome {index:02d}",
                    deadline_kind=deadline_kinds[(global_index - 1) % len(deadline_kinds)],
                    deadline_date_kind=date_kind,
                    timezone=timezones[(global_index - 1) % len(timezones)],
                    action_kind=action_kind,
                    action_risk=risk,
                )
            )
    return tuple(seeds)


def _case_number(case_id: str) -> int:
    counts_before = 0
    for case_type, count in _COUNTS.items():
        slug = case_type.replace("_", "-")
        prefix = f"m7-{slug}-"
        if case_id.startswith(prefix):
            return counts_before + int(case_id.removeprefix(prefix))
        counts_before += count
    raise ValueError("Unknown generated Case ID")


def _canonical_date_value(value: str, date_kind: str) -> str:
    if date_kind == "local_date":
        return date.fromisoformat(value).isoformat()
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _add_days(value: str, date_kind: str, days: int) -> str:
    if date_kind == "local_date":
        return (date.fromisoformat(value) + timedelta(days=days)).isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(days=days)
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": "))
        + "\n"
    ).encode()


def _write(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _check(root: Path, files: dict[str, bytes]) -> int:
    actual = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    if actual == files:
        return 0
    print("M7 corpus does not match the deterministic generator.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
