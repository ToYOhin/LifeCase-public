"""Integration coverage for deterministic M2-02 Claim provenance."""

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifecase.api.claims as claims_api
from lifecase.cases import CaseService
from lifecase.claims.models import (
    ACTIVE_CLAIM_STATUSES,
    CONFIRMABLE_CLAIM_STATUSES,
    CORRECTABLE_CLAIM_STATUSES,
    REJECTABLE_CLAIM_STATUSES,
    SOURCE_ATTACHABLE_CLAIM_STATUSES,
    AttachClaimSourceCommand,
    ClaimCommandValidationError,
    ClaimConfirmationBasis,
    ClaimCreatedBy,
    ClaimRecord,
    ClaimSourceInput,
    ClaimSourceRelation,
    ClaimStatus,
    ClaimValue,
    ConfirmClaimCommand,
    CorrectClaimCommand,
    CreateClaimCommand,
    RejectClaimCommand,
)
from lifecase.claims.service import (
    ClaimCaseNotFoundError,
    ClaimPersistenceError,
    ClaimService,
    ClaimSourceInvalidError,
    ClaimSourceRequiredError,
    ClaimTransitionNotAllowedError,
    ClaimVersionConflictError,
)
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.storage import apply_migrations, connect_database

_NOW = "2026-08-01T00:00:00.000000Z"


def test_claim_states_and_active_candidate_set_are_explicit() -> None:
    assert {status.value for status in ClaimStatus} == {
        "candidate",
        "inferred",
        "confirmed",
        "corrected",
        "rejected",
    }
    assert {ClaimStatus.CANDIDATE, ClaimStatus.INFERRED} == ACTIVE_CLAIM_STATUSES
    assert ACTIVE_CLAIM_STATUSES == CONFIRMABLE_CLAIM_STATUSES
    mutable = {
        ClaimStatus.CANDIDATE,
        ClaimStatus.INFERRED,
        ClaimStatus.CONFIRMED,
        ClaimStatus.CORRECTED,
    }
    assert mutable == CORRECTABLE_CLAIM_STATUSES
    assert mutable == REJECTABLE_CLAIM_STATUSES
    assert mutable == SOURCE_ATTACHABLE_CLAIM_STATUSES


def test_claim_value_is_canonical_bounded_and_comparable() -> None:
    first = ClaimValue.from_input({"currency": " CNY ", "amount_minor": 25900})
    reordered = ClaimValue.from_input({"amount_minor": 25900, "currency": "CNY"})
    case_variant = ClaimValue.from_input({"currency": "cny", "amount_minor": 25900})

    assert first.json_text == '{"amount_minor":25900,"currency":"CNY"}'
    assert first == reordered
    assert first.sha256 != case_variant.sha256
    assert first.comparison_sha256 == case_variant.comparison_sha256
    assert first.to_python() == {"amount_minor": 25900, "currency": "CNY"}

    with pytest.raises(ClaimCommandValidationError):
        ClaimValue(json_text='"forged"', sha256="0" * 64, comparison_sha256="0" * 64)


@pytest.mark.parametrize(
    "value",
    [
        None,
        " ",
        "x" * 4097,
        1.5,
        [],
        {},
        9_007_199_254_740_992,
        {"bad-key": "value"},
    ],
)
def test_claim_value_rejects_unsafe_or_empty_input(value: object) -> None:
    with pytest.raises(ClaimCommandValidationError):
        ClaimValue.from_input(value)


def test_claim_commands_normalize_and_validate_boundaries() -> None:
    value = ClaimValue.from_input(" Synthetic order ")
    source = _source_input()
    create = CreateClaimCommand(
        case_id=" case_synthetic ",
        field="order_number",
        value=value,
        status=ClaimStatus.CANDIDATE,
        confidence=1,
        created_by=ClaimCreatedBy.AGENT,
        actor=" agent_run_synthetic ",
        reason=" Synthetic extraction ",
        sources=(source,),
    )

    assert create.case_id == "case_synthetic"
    assert create.value.to_python() == "Synthetic order"
    assert create.confidence == 1.0
    assert create.actor == "agent_run_synthetic"
    assert create.reason == "Synthetic extraction"

    assert (
        AttachClaimSourceCommand(
            claim_id=" claim_synthetic ",
            expected_version=1,
            source=source,
            actor="local_user",
            reason="Attach synthetic source",
        ).claim_id
        == "claim_synthetic"
    )
    assert (
        ConfirmClaimCommand(
            claim_id="claim_synthetic",
            expected_version=1,
            basis=ClaimConfirmationBasis.USER,
            actor="local_user",
            reason="Confirm synthetic fact",
        ).basis
        is ClaimConfirmationBasis.USER
    )
    assert (
        CorrectClaimCommand(
            claim_id="claim_synthetic",
            expected_version=1,
            value=ClaimValue.from_input("Corrected"),
            actor="local_user",
            reason="Correct synthetic fact",
        ).value.to_python()
        == "Corrected"
    )
    assert (
        RejectClaimCommand(
            claim_id="claim_synthetic",
            expected_version=1,
            actor="local_user",
            reason="Reject synthetic fact",
        ).expected_version
        == 1
    )


@pytest.mark.parametrize(
    "override",
    [
        {"case_id": " "},
        {"field": "Invalid-Field"},
        {"status": ClaimStatus.CONFIRMED},
        {"confidence": True},
        {"confidence": 1.1},
        {"created_by": "agent"},
        {"actor": " "},
        {"actor": "x" * 121},
        {"reason": " "},
        {"reason": "x" * 1001},
    ],
)
def test_create_claim_command_rejects_invalid_boundaries(
    override: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "case_id": "case_synthetic",
        "field": "order_number",
        "value": ClaimValue.from_input("Synthetic order"),
        "status": ClaimStatus.CANDIDATE,
        "confidence": 0.8,
        "created_by": ClaimCreatedBy.AGENT,
        "actor": "agent_run_synthetic",
        "reason": "Synthetic extraction",
    }
    values.update(override)

    with pytest.raises(ClaimCommandValidationError):
        CreateClaimCommand(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "override",
    [
        {"source_id": " "},
        {"segment_id": "x" * 201},
        {"segment_text_sha256": "not-a-hash"},
        {"quote_start": True},
        {"quote_start": -1},
        {"quote_end": 0},
        {"quote_hash": "0" * 63},
        {"relation": "supports"},
    ],
)
def test_claim_source_input_rejects_invalid_boundaries(
    override: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "source_id": "source_synthetic",
        "segment_id": "segment_synthetic",
        "segment_text_sha256": "0" * 64,
        "quote_start": 0,
        "quote_end": 9,
        "quote_hash": hashlib.sha256(b"synthetic").hexdigest(),
        "relation": ClaimSourceRelation.SUPPORTS,
    }
    values.update(override)

    with pytest.raises(ClaimCommandValidationError):
        ClaimSourceInput(**values)  # type: ignore[arg-type]


def test_create_claim_persists_authoritative_sources_and_history(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    text = "Synthetic receipt order LC-2026-001 was accepted"
    source = _insert_source(connection, "case_one", "source_one", "segment_one", text)
    citation = _excerpt(source, text, "LC-2026-001")
    service = ClaimService(connection)
    try:
        claim = service.create_claim(
            _create_command(
                case_id="case_one",
                value="LC-2026-001",
                source=citation,
            )
        )

        assert service.get_claim(claim.id) == claim
        assert service.list_case_claims("case_one") == (claim,)
        assert (claim.status, claim.version, claim.contradiction) == (
            ClaimStatus.CANDIDATE,
            1,
            False,
        )
        sources = service.list_claim_sources(claim.id)
        assert len(sources) == 1
        assert (
            sources[0].case_id,
            sources[0].source_id,
            sources[0].segment_id,
            sources[0].segment_text_sha256,
            sources[0].quote_hash,
        ) == (
            "case_one",
            "source_one",
            "segment_one",
            source.segment_text_sha256,
            citation.quote_hash,
        )
        event = connection.execute(
            "SELECT event_type, prior_version, new_version FROM claim_event"
        ).fetchone()
        assert tuple(event) == ("created", 0, 1)
    finally:
        connection.close()


def test_cross_case_or_stale_source_rolls_back_creation(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    text = "Synthetic cross Case reference"
    source = _insert_source(connection, "case_two", "source_two", "segment_two", text)
    service = ClaimService(connection)
    try:
        with pytest.raises(ClaimSourceInvalidError):
            service.create_claim(
                _create_command(
                    case_id="case_one",
                    value="Synthetic value",
                    source=_excerpt(source, text, "cross Case"),
                )
            )
        stale = ClaimSourceInput(
            source_id=source.source_id,
            segment_id=source.segment_id,
            segment_text_sha256="f" * 64,
            quote_start=source.quote_start,
            quote_end=source.quote_end,
            quote_hash=source.quote_hash,
            relation=source.relation,
        )
        with pytest.raises(ClaimSourceInvalidError):
            service.create_claim(
                _create_command(case_id="case_two", value="Synthetic value", source=stale)
            )
        missing = ClaimSourceInput(
            source_id="source_missing",
            segment_id="segment_missing",
            segment_text_sha256="0" * 64,
            quote_start=0,
            quote_end=1,
            quote_hash=hashlib.sha256(b"x").hexdigest(),
            relation=ClaimSourceRelation.SUPPORTS,
        )
        with pytest.raises(ClaimSourceInvalidError):
            service.create_claim(
                _create_command(case_id="case_two", value="Synthetic value", source=missing)
            )

        assert connection.execute("SELECT COUNT(*) FROM claim").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM claim_event").fetchone()[0] == 0
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_claim_source_read_fails_closed_after_authoritative_tampering(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    text = "Synthetic immutable authority"
    source = _insert_source(connection, "case_one", "source_one", "segment_one", text)
    service = ClaimService(connection)
    try:
        claim = service.create_claim(
            _create_command(
                case_id="case_one",
                value="authority",
                source=_excerpt(source, text, "authority"),
            )
        )
        connection.execute("UPDATE source_segment SET text = 'tampered' WHERE id = 'segment_one'")

        with pytest.raises(ClaimSourceInvalidError):
            service.list_claim_sources(claim.id)
    finally:
        connection.close()


def test_stable_segment_replacement_preserves_claim_provenance(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    text = "Synthetic stable replacement citation"
    source = _insert_source(connection, "case_one", "source_one", "segment_one", text)
    citation = _excerpt(source, text, "stable replacement")
    service = ClaimService(connection)
    try:
        claim = service.create_claim(
            _create_command(case_id="case_one", value="stable", source=citation)
        )
        before = service.list_claim_sources(claim.id)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM source_segment_fts WHERE segment_id = 'segment_one'")
        connection.execute("DELETE FROM source_segment WHERE id = 'segment_one'")
        connection.execute(
            """
            INSERT INTO source_segment (
                id, source_id, sequence, page, bbox_json, text,
                extraction_method, confidence, text_sha256, extractor_version
            ) VALUES ('segment_one', 'source_one', 0, 1, NULL, ?,
                      'direct', 1.0, ?, 'synthetic.v1')
            """,
            (text, source.segment_text_sha256),
        )
        connection.execute(
            """
            INSERT INTO source_segment_fts(rowid, source_id, segment_id, text, text_sha256)
            SELECT rowid, source_id, id, text, text_sha256
            FROM source_segment WHERE id = 'segment_one'
            """
        )
        connection.execute("COMMIT")

        assert service.list_claim_sources(claim.id) == before
    finally:
        connection.close()


def test_active_candidate_contradictions_are_case_and_field_scoped(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    service = ClaimService(connection)
    try:
        first = service.create_claim(_create_command(case_id="case_one", value="Store A"))
        second = service.create_claim(_create_command(case_id="case_one", value="Store B"))
        equivalent = service.create_claim(_create_command(case_id="case_one", value=" store   b "))
        other_case = service.create_claim(_create_command(case_id="case_two", value="Store C"))

        assert ClaimService(connection).get_claim(first.id).contradiction is True
        assert ClaimService(connection).get_claim(second.id).contradiction is True
        assert ClaimService(connection).get_claim(equivalent.id).contradiction is True
        assert other_case.contradiction is False
    finally:
        connection.close()


def test_create_claim_requires_existing_case(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        with pytest.raises(ClaimCaseNotFoundError):
            ClaimService(connection).create_claim(
                _create_command(case_id="case_missing", value="Synthetic value")
            )
        assert connection.execute("SELECT COUNT(*) FROM claim").fetchone()[0] == 0
    finally:
        connection.close()


def test_agent_claim_requires_evidence_or_explicit_user_confirmation(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    service = ClaimService(connection)
    try:
        claim = service.create_claim(_create_command(case_id="case_one", value="Store A"))
        with pytest.raises(ClaimSourceRequiredError):
            service.confirm_claim(
                ConfirmClaimCommand(
                    claim_id=claim.id,
                    expected_version=1,
                    basis=ClaimConfirmationBasis.EVIDENCE,
                    actor="agent_run_synthetic",
                    reason="Synthetic evidence confirmation",
                )
            )
        unchanged = service.get_claim(claim.id)
        assert (unchanged.status, unchanged.version) == (ClaimStatus.CANDIDATE, 1)
        assert len(service.list_claim_events(claim.id)) == 1
        assert service.list_claim_confirmations(claim.id) == ()

        confirmed = service.confirm_claim(
            ConfirmClaimCommand(
                claim_id=claim.id,
                expected_version=1,
                basis=ClaimConfirmationBasis.USER,
                actor="local_user",
                reason="Explicitly verified synthetic merchant",
            )
        )
        confirmation = service.list_claim_confirmations(claim.id)
        assert (confirmed.status, confirmed.version) == (ClaimStatus.CONFIRMED, 2)
        assert len(confirmation) == 1
        assert (
            confirmation[0].basis,
            confirmation[0].claim_version,
            confirmation[0].value_sha256,
            confirmation[0].actor,
        ) == (
            ClaimConfirmationBasis.USER,
            2,
            claim.value.sha256,
            "local_user",
        )
    finally:
        connection.close()


def test_supporting_source_can_confirm_agent_claim(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    text = "Merchant: Synthetic Store"
    source = _insert_source(connection, "case_one", "source_one", "segment_one", text)
    service = ClaimService(connection)
    try:
        claim = service.create_claim(
            _create_command(
                case_id="case_one",
                value="Synthetic Store",
                source=_excerpt(source, text, "Synthetic Store"),
            )
        )
        confirmed = service.confirm_claim(
            ConfirmClaimCommand(
                claim_id=claim.id,
                expected_version=1,
                basis=ClaimConfirmationBasis.EVIDENCE,
                actor="rule_synthetic",
                reason="Authoritative synthetic citation validated",
            )
        )

        assert (confirmed.status, confirmed.version) == (ClaimStatus.CONFIRMED, 2)
        assert service.list_claim_confirmations(claim.id)[0].basis is (
            ClaimConfirmationBasis.EVIDENCE
        )
        assert [event.event_type.value for event in service.list_claim_events(claim.id)] == [
            "created",
            "confirmed",
        ]
    finally:
        connection.close()


def test_attach_source_is_versioned_and_evidence_confirmation_revalidates_it(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    text = "Order number SYN-2026-88"
    source = _insert_source(connection, "case_one", "source_one", "segment_one", text)
    citation = _excerpt(source, text, "SYN-2026-88")
    service = ClaimService(connection)
    try:
        claim = service.create_claim(_create_command(case_id="case_one", value="SYN-2026-88"))
        attached = service.attach_claim_source(
            AttachClaimSourceCommand(
                claim_id=claim.id,
                expected_version=1,
                source=citation,
                actor="local_user",
                reason="Attach reviewed synthetic receipt",
            )
        )
        confirmed = service.confirm_claim(
            ConfirmClaimCommand(
                claim_id=claim.id,
                expected_version=2,
                basis=ClaimConfirmationBasis.EVIDENCE,
                actor="rule_synthetic",
                reason="Validate supporting citation",
            )
        )

        assert (attached.version, confirmed.version) == (2, 3)
        assert [event.event_type.value for event in service.list_claim_events(claim.id)] == [
            "created",
            "source_attached",
            "confirmed",
        ]
    finally:
        connection.close()


def test_correction_retains_prior_values_reasons_and_versions(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    service = ClaimService(connection)
    try:
        claim = service.create_claim(_create_command(case_id="case_one", value="Incorrect Store"))
        corrected = service.correct_claim(
            CorrectClaimCommand(
                claim_id=claim.id,
                expected_version=1,
                value=ClaimValue.from_input("Correct Store"),
                actor="local_user",
                reason="Receipt names a different synthetic merchant",
            )
        )
        corrected_again = service.correct_claim(
            CorrectClaimCommand(
                claim_id=claim.id,
                expected_version=2,
                value=ClaimValue.from_input("Correct Store Ltd"),
                actor="local_user",
                reason="Include the synthetic legal suffix",
            )
        )
        events = service.list_claim_events(claim.id)

        assert (corrected.status, corrected.version) == (ClaimStatus.CORRECTED, 2)
        assert (corrected_again.status, corrected_again.version) == (
            ClaimStatus.CORRECTED,
            3,
        )
        assert [event.new_version for event in events] == [1, 2, 3]
        assert events[1].prior_value == claim.value
        assert events[1].new_value == corrected.value
        assert events[1].reason == "Receipt names a different synthetic merchant"
        assert events[2].prior_value == corrected.value
        assert events[2].new_value == corrected_again.value
        assert events[2].reason == "Include the synthetic legal suffix"
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("operation", "prior_status"),
    [
        ("confirm", ClaimStatus.CANDIDATE),
        ("confirm", ClaimStatus.INFERRED),
        ("correct", ClaimStatus.CANDIDATE),
        ("correct", ClaimStatus.INFERRED),
        ("correct", ClaimStatus.CONFIRMED),
        ("correct", ClaimStatus.CORRECTED),
        ("reject", ClaimStatus.CANDIDATE),
        ("reject", ClaimStatus.INFERRED),
        ("reject", ClaimStatus.CONFIRMED),
        ("reject", ClaimStatus.CORRECTED),
        ("attach", ClaimStatus.CANDIDATE),
        ("attach", ClaimStatus.INFERRED),
        ("attach", ClaimStatus.CONFIRMED),
        ("attach", ClaimStatus.CORRECTED),
    ],
)
def test_every_allowed_claim_operation_commits_one_version_and_event(
    tmp_path: Path,
    operation: str,
    prior_status: ClaimStatus,
) -> None:
    connection = _database(tmp_path)
    service = ClaimService(connection)
    try:
        claim = _claim_in_status(service, prior_status)
        prior_events = service.list_claim_events(claim.id)
        if operation == "confirm":
            result = service.confirm_claim(
                ConfirmClaimCommand(
                    claim_id=claim.id,
                    expected_version=claim.version,
                    basis=ClaimConfirmationBasis.USER,
                    actor="local_user",
                    reason="Synthetic allowed confirmation",
                )
            )
            expected_status = ClaimStatus.CONFIRMED
        elif operation == "correct":
            result = service.correct_claim(
                CorrectClaimCommand(
                    claim_id=claim.id,
                    expected_version=claim.version,
                    value=ClaimValue.from_input(f"Corrected {claim.version}"),
                    actor="local_user",
                    reason="Synthetic allowed correction",
                )
            )
            expected_status = ClaimStatus.CORRECTED
        elif operation == "reject":
            result = service.reject_claim(
                RejectClaimCommand(
                    claim_id=claim.id,
                    expected_version=claim.version,
                    actor="local_user",
                    reason="Synthetic allowed rejection",
                )
            )
            expected_status = ClaimStatus.REJECTED
        else:
            text = f"Synthetic source for {prior_status.value}"
            source = _insert_source(
                connection, "case_one", "source_operation", "segment_operation", text
            )
            result = service.attach_claim_source(
                AttachClaimSourceCommand(
                    claim_id=claim.id,
                    expected_version=claim.version,
                    source=_excerpt(source, text, prior_status.value),
                    actor="local_user",
                    reason="Synthetic allowed source attachment",
                )
            )
            expected_status = prior_status

        assert (result.status, result.version) == (expected_status, claim.version + 1)
        events = service.list_claim_events(claim.id)
        assert len(events) == len(prior_events) + 1
        assert (events[-1].prior_version, events[-1].new_version) == (
            claim.version,
            claim.version + 1,
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("operation", "prior_status"),
    [
        ("confirm", ClaimStatus.CONFIRMED),
        ("confirm", ClaimStatus.CORRECTED),
        ("confirm", ClaimStatus.REJECTED),
        ("correct", ClaimStatus.REJECTED),
        ("reject", ClaimStatus.REJECTED),
        ("attach", ClaimStatus.REJECTED),
    ],
)
def test_forbidden_claim_operations_do_not_change_state_or_history(
    tmp_path: Path,
    operation: str,
    prior_status: ClaimStatus,
) -> None:
    connection = _database(tmp_path)
    service = ClaimService(connection)
    try:
        claim = _claim_in_status(service, prior_status)
        prior_events = service.list_claim_events(claim.id)
        prior_sources = service.list_claim_sources(claim.id)
        with pytest.raises(ClaimTransitionNotAllowedError):
            if operation == "confirm":
                service.confirm_claim(
                    ConfirmClaimCommand(
                        claim_id=claim.id,
                        expected_version=claim.version,
                        basis=ClaimConfirmationBasis.USER,
                        actor="local_user",
                        reason="Synthetic forbidden confirmation",
                    )
                )
            elif operation == "correct":
                service.correct_claim(
                    CorrectClaimCommand(
                        claim_id=claim.id,
                        expected_version=claim.version,
                        value=ClaimValue.from_input("Forbidden correction"),
                        actor="local_user",
                        reason="Synthetic forbidden correction",
                    )
                )
            elif operation == "reject":
                service.reject_claim(
                    RejectClaimCommand(
                        claim_id=claim.id,
                        expected_version=claim.version,
                        actor="local_user",
                        reason="Synthetic duplicate rejection",
                    )
                )
            else:
                service.attach_claim_source(
                    AttachClaimSourceCommand(
                        claim_id=claim.id,
                        expected_version=claim.version,
                        source=_source_input(),
                        actor="local_user",
                        reason="Synthetic forbidden source attachment",
                    )
                )

        assert service.get_claim(claim.id) == claim
        assert service.list_claim_events(claim.id) == prior_events
        assert service.list_claim_sources(claim.id) == prior_sources
        assert connection.in_transaction is False
    finally:
        connection.close()


@pytest.mark.parametrize("operation", ["confirm", "correct", "reject"])
def test_leaving_active_candidates_recomputes_contradictions(
    tmp_path: Path,
    operation: str,
) -> None:
    connection = _database(tmp_path)
    service = ClaimService(connection)
    try:
        first = service.create_claim(_create_command(case_id="case_one", value="Store A"))
        second = service.create_claim(_create_command(case_id="case_one", value="Store B"))
        assert service.get_claim(first.id).contradiction is True
        if operation == "confirm":
            service.confirm_claim(
                ConfirmClaimCommand(
                    claim_id=first.id,
                    expected_version=1,
                    basis=ClaimConfirmationBasis.USER,
                    actor="local_user",
                    reason="Resolve synthetic contradiction",
                )
            )
        elif operation == "correct":
            service.correct_claim(
                CorrectClaimCommand(
                    claim_id=first.id,
                    expected_version=1,
                    value=ClaimValue.from_input("Store B"),
                    actor="local_user",
                    reason="Resolve synthetic contradiction",
                )
            )
        else:
            service.reject_claim(
                RejectClaimCommand(
                    claim_id=first.id,
                    expected_version=1,
                    actor="local_user",
                    reason="Resolve synthetic contradiction",
                )
            )

        assert service.get_claim(first.id).contradiction is False
        assert service.get_claim(second.id).contradiction is False
    finally:
        connection.close()


def test_stale_claim_version_fails_without_second_write(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    service = ClaimService(connection)
    try:
        claim = service.create_claim(_create_command(case_id="case_one", value="Store A"))
        command = RejectClaimCommand(
            claim_id=claim.id,
            expected_version=1,
            actor="local_user",
            reason="Synthetic competing rejection",
        )
        first = service.reject_claim(command)
        with pytest.raises(ClaimVersionConflictError) as captured:
            service.reject_claim(command)

        assert (first.status, first.version) == (ClaimStatus.REJECTED, 2)
        assert captured.value.current_version == 2
        assert len(service.list_claim_events(claim.id)) == 2
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_claim_history_failure_rolls_back_correction(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    service = ClaimService(connection)
    try:
        claim = service.create_claim(_create_command(case_id="case_one", value="Store A"))
        connection.execute(
            """
            CREATE TRIGGER fail_claim_history
            BEFORE INSERT ON claim_event
            BEGIN
                SELECT RAISE(ABORT, 'synthetic private persistence detail');
            END
            """
        )
        with pytest.raises(ClaimPersistenceError) as captured:
            service.correct_claim(
                CorrectClaimCommand(
                    claim_id=claim.id,
                    expected_version=1,
                    value=ClaimValue.from_input("Store B"),
                    actor="local_user",
                    reason="Synthetic rollback correction",
                )
            )

        assert service.get_claim(claim.id) == claim
        assert len(service.list_claim_events(claim.id)) == 1
        assert "synthetic private persistence detail" not in str(captured.value)
        assert str(tmp_path) not in str(captured.value)
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_source_history_failure_rolls_back_attached_reference(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    text = "Synthetic rollback source"
    source = _insert_source(connection, "case_one", "source_one", "segment_one", text)
    service = ClaimService(connection)
    try:
        claim = service.create_claim(_create_command(case_id="case_one", value="Store A"))
        connection.execute(
            """
            CREATE TRIGGER fail_claim_source_history
            BEFORE INSERT ON claim_event
            WHEN NEW.event_type = 'source_attached'
            BEGIN
                SELECT RAISE(ABORT, 'synthetic source history detail');
            END
            """
        )
        with pytest.raises(ClaimPersistenceError):
            service.attach_claim_source(
                AttachClaimSourceCommand(
                    claim_id=claim.id,
                    expected_version=1,
                    source=_excerpt(source, text, "rollback source"),
                    actor="local_user",
                    reason="Synthetic source rollback",
                )
            )

        assert service.get_claim(claim.id) == claim
        assert service.list_claim_sources(claim.id) == ()
        assert len(service.list_claim_events(claim.id)) == 1
    finally:
        connection.close()


def test_confirmation_write_failure_rolls_back_claim_and_event(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    service = ClaimService(connection)
    try:
        claim = service.create_claim(_create_command(case_id="case_one", value="Store A"))
        connection.execute(
            """
            CREATE TRIGGER fail_claim_confirmation
            BEFORE INSERT ON claim_confirmation
            BEGIN
                SELECT RAISE(ABORT, 'synthetic confirmation detail');
            END
            """
        )
        with pytest.raises(ClaimPersistenceError):
            service.confirm_claim(
                ConfirmClaimCommand(
                    claim_id=claim.id,
                    expected_version=1,
                    basis=ClaimConfirmationBasis.USER,
                    actor="local_user",
                    reason="Synthetic confirmation rollback",
                )
            )

        assert service.get_claim(claim.id) == claim
        assert len(service.list_claim_events(claim.id)) == 1
        assert service.list_claim_confirmations(claim.id) == ()
    finally:
        connection.close()


def test_claim_provenance_survives_restarted_connection(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection = _database(tmp_path)
    text = "Synthetic merchant Restart Store"
    source = _insert_source(connection, "case_one", "source_one", "segment_one", text)
    service = ClaimService(connection)
    claim = service.create_claim(
        _create_command(
            case_id="case_one",
            value="Restart Store",
            source=_excerpt(source, text, "Restart Store"),
        )
    )
    expected = service.confirm_claim(
        ConfirmClaimCommand(
            claim_id=claim.id,
            expected_version=1,
            basis=ClaimConfirmationBasis.EVIDENCE,
            actor="rule_synthetic",
            reason="Synthetic restart confirmation",
        )
    )
    expected_sources = service.list_claim_sources(claim.id)
    expected_events = service.list_claim_events(claim.id)
    expected_confirmations = service.list_claim_confirmations(claim.id)
    connection.close()

    restarted = connect_database(database_path)
    try:
        restored = ClaimService(restarted)
        assert restored.get_claim(claim.id) == expected
        assert restored.list_claim_sources(claim.id) == expected_sources
        assert restored.list_claim_events(claim.id) == expected_events
        assert restored.list_claim_confirmations(claim.id) == expected_confirmations
    finally:
        restarted.close()


def test_claim_decision_api_exposes_thin_optimistic_contracts(tmp_path: Path) -> None:
    config, claim = _api_claim(tmp_path)
    with TestClient(create_app(config)) as client:
        confirmed = client.post(
            f"/api/v1/claims/{claim.id}/confirm",
            json={
                "expected_version": 1,
                "basis": "user",
                "actor": "local_user",
                "reason": "Explicit synthetic confirmation",
            },
        )
        corrected = client.post(
            f"/api/v1/claims/{claim.id}/correct",
            json={
                "expected_version": 2,
                "value": "Corrected API Store",
                "actor": "local_user",
                "reason": "Correct synthetic API value",
            },
        )
        rejected = client.post(
            f"/api/v1/claims/{claim.id}/reject",
            json={
                "expected_version": 3,
                "actor": "local_user",
                "reason": "Reject synthetic API value",
            },
        )

    assert confirmed.status_code == corrected.status_code == rejected.status_code == 200
    assert (confirmed.json()["status"], confirmed.json()["version"]) == ("confirmed", 2)
    assert (corrected.json()["status"], corrected.json()["version"]) == ("corrected", 3)
    assert corrected.json()["value"] == "Corrected API Store"
    assert (rejected.json()["status"], rejected.json()["version"]) == ("rejected", 4)
    assert rejected.json()["case_id"] == claim.case_id


def test_claim_api_safely_rejects_source_stale_illegal_and_invalid_requests(
    tmp_path: Path,
) -> None:
    config, claim = _api_claim(tmp_path)
    with TestClient(create_app(config)) as client:
        source_required = client.post(
            f"/api/v1/claims/{claim.id}/confirm",
            json={
                "expected_version": 1,
                "basis": "evidence",
                "actor": "agent_run_synthetic",
                "reason": "Synthetic unsupported confirmation",
            },
        )
        accepted = client.post(
            f"/api/v1/claims/{claim.id}/confirm",
            json={
                "expected_version": 1,
                "basis": "user",
                "actor": "local_user",
                "reason": "Explicit synthetic confirmation",
            },
        )
        stale = client.post(
            f"/api/v1/claims/{claim.id}/correct",
            json={
                "expected_version": 1,
                "value": "Stale value",
                "actor": "local_user",
                "reason": "Synthetic stale command",
            },
        )
        illegal = client.post(
            f"/api/v1/claims/{claim.id}/confirm",
            json={
                "expected_version": 2,
                "basis": "user",
                "actor": "local_user",
                "reason": "Synthetic repeated confirmation",
            },
        )
        empty_value = client.post(
            f"/api/v1/claims/{claim.id}/correct",
            json={
                "expected_version": 2,
                "value": " ",
                "actor": "local_user",
                "reason": "Synthetic invalid value",
            },
        )
        float_value = client.post(
            f"/api/v1/claims/{claim.id}/correct",
            json={
                "expected_version": 2,
                "value": 1.5,
                "actor": "local_user",
                "reason": "Synthetic invalid float",
            },
        )
        boolean_version = client.post(
            f"/api/v1/claims/{claim.id}/reject",
            json={
                "expected_version": True,
                "actor": "local_user",
                "reason": "Synthetic invalid version",
            },
        )
        missing = client.post(
            "/api/v1/claims/claim_missing/reject",
            json={
                "expected_version": 1,
                "actor": "local_user",
                "reason": "Synthetic missing Claim",
            },
        )

    assert (source_required.status_code, source_required.json()["error"]["code"]) == (
        409,
        "claim_source_required",
    )
    assert accepted.status_code == 200
    assert (stale.status_code, stale.json()["error"]["code"]) == (
        409,
        "claim_version_conflict",
    )
    assert stale.json()["error"]["details"] == {"current_version": 2}
    assert (illegal.status_code, illegal.json()["error"]["code"]) == (
        409,
        "claim_transition_not_allowed",
    )
    assert (empty_value.status_code, empty_value.json()["error"]["code"]) == (
        422,
        "claim_invalid",
    )
    assert (float_value.status_code, float_value.json()["error"]["code"]) == (
        422,
        "claim_invalid",
    )
    assert boolean_version.status_code == 422
    assert boolean_version.json()["error"]["code"] == "validation_error"
    assert (missing.status_code, missing.json()["error"]["code"]) == (
        404,
        "claim_not_found",
    )
    assert "Synthetic stale command" not in stale.text
    assert str(config.database_path) not in stale.text


def test_claim_api_storage_failure_hides_private_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, claim = _api_claim(tmp_path)

    def fail_claim(*_args: object, **_kwargs: object) -> None:
        raise ClaimPersistenceError("synthetic private SQL detail")

    monkeypatch.setattr(claims_api.ClaimService, "reject_claim", fail_claim)
    with TestClient(create_app(config)) as client:
        response = client.post(
            f"/api/v1/claims/{claim.id}/reject",
            json={
                "expected_version": 1,
                "actor": "local_user",
                "reason": "Synthetic persistence failure",
            },
        )

    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "claim_storage_failed",
    )
    assert response.json()["error"]["retryable"] is True
    assert "synthetic private SQL detail" not in response.text
    assert str(config.database_path) not in response.text


def _source_input() -> ClaimSourceInput:
    return ClaimSourceInput(
        source_id="source_synthetic",
        segment_id="segment_synthetic",
        segment_text_sha256="0" * 64,
        quote_start=0,
        quote_end=9,
        quote_hash=hashlib.sha256(b"synthetic").hexdigest(),
        relation=ClaimSourceRelation.SUPPORTS,
    )


def _database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    for case_id in ("case_one", "case_two"):
        connection.execute(
            """
            INSERT INTO case_record (
                id, type, template_version, title, goal, status,
                priority, version, created_at, updated_at
            ) VALUES (?, 'warranty', 1, 'Synthetic Claim Case',
                      'Test Claim provenance', 'intake', 0, 1, ?, ?)
            """,
            (case_id, _NOW, _NOW),
        )
    return connection


def _insert_source(
    connection: sqlite3.Connection,
    case_id: str,
    source_id: str,
    segment_id: str,
    text: str,
) -> ClaimSourceInput:
    text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO source (
            id, case_id, original_name, media_type, sha256,
            size_bytes, object_path, imported_at
        ) VALUES (?, ?, 'synthetic.txt', 'text/plain', ?, 1, ?, ?)
        """,
        (source_id, case_id, "0" * 64, f"objects/00/00/{'0' * 64}", _NOW),
    )
    connection.execute(
        """
        INSERT INTO source_segment (
            id, source_id, sequence, page, bbox_json, text,
            extraction_method, confidence, text_sha256, extractor_version
        ) VALUES (?, ?, 0, 1, NULL, ?, 'direct', 1.0, ?, 'synthetic.v1')
        """,
        (segment_id, source_id, text, text_sha256),
    )
    return ClaimSourceInput(
        source_id=source_id,
        segment_id=segment_id,
        segment_text_sha256=text_sha256,
        quote_start=0,
        quote_end=1,
        quote_hash=hashlib.sha256(text[:1].encode()).hexdigest(),
        relation=ClaimSourceRelation.SUPPORTS,
    )


def _excerpt(source: ClaimSourceInput, text: str, quote: str) -> ClaimSourceInput:
    quote_start = text.index(quote)
    quote_end = quote_start + len(quote)
    return ClaimSourceInput(
        source_id=source.source_id,
        segment_id=source.segment_id,
        segment_text_sha256=source.segment_text_sha256,
        quote_start=quote_start,
        quote_end=quote_end,
        quote_hash=hashlib.sha256(quote.encode()).hexdigest(),
        relation=ClaimSourceRelation.SUPPORTS,
    )


def _create_command(
    *,
    case_id: str,
    value: object,
    source: ClaimSourceInput | None = None,
    status: ClaimStatus = ClaimStatus.CANDIDATE,
) -> CreateClaimCommand:
    return CreateClaimCommand(
        case_id=case_id,
        field="merchant_or_manufacturer",
        value=ClaimValue.from_input(value),
        status=status,
        confidence=0.8,
        created_by=ClaimCreatedBy.AGENT,
        actor="agent_run_synthetic",
        reason="Synthetic Claim candidate",
        sources=(source,) if source is not None else (),
    )


def _claim_in_status(service: ClaimService, status: ClaimStatus) -> ClaimRecord:
    claim = service.create_claim(
        _create_command(
            case_id="case_one",
            value=f"Synthetic {status.value}",
            status=(
                ClaimStatus.INFERRED if status is ClaimStatus.INFERRED else ClaimStatus.CANDIDATE
            ),
        )
    )
    if status is ClaimStatus.CONFIRMED:
        return service.confirm_claim(
            ConfirmClaimCommand(
                claim_id=claim.id,
                expected_version=claim.version,
                basis=ClaimConfirmationBasis.USER,
                actor="local_user",
                reason="Prepare synthetic confirmed Claim",
            )
        )
    if status is ClaimStatus.CORRECTED:
        return service.correct_claim(
            CorrectClaimCommand(
                claim_id=claim.id,
                expected_version=claim.version,
                value=ClaimValue.from_input("Prepared corrected value"),
                actor="local_user",
                reason="Prepare synthetic corrected Claim",
            )
        )
    if status is ClaimStatus.REJECTED:
        return service.reject_claim(
            RejectClaimCommand(
                claim_id=claim.id,
                expected_version=claim.version,
                actor="local_user",
                reason="Prepare synthetic rejected Claim",
            )
        )
    return claim


def _api_claim(tmp_path: Path) -> tuple[ApplicationConfig, ClaimRecord]:
    config = ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )
    connection = connect_database(config.database_path)
    try:
        apply_migrations(connection)
        case = CaseService(connection).create_case(
            title="Synthetic Claim API Case",
            goal="Test deterministic Claim decisions",
            template_id="warranty",
        )
        claim = ClaimService(connection).create_claim(
            _create_command(case_id=case.id, value="API Store")
        )
        return config, claim
    finally:
        connection.close()
