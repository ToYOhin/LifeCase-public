"""Integration coverage for the deterministic M2-03 warranty template engine."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from lifecase.cases import (
    CasePersistenceError,
    CaseService,
    CaseStatus,
    CaseTemplateBindingError,
    CaseValidationError,
)
from lifecase.claims import (
    ClaimConfirmationBasis,
    ClaimCreatedBy,
    ClaimRecord,
    ClaimService,
    ClaimStatus,
    ClaimValue,
    ConfirmClaimCommand,
    CorrectClaimCommand,
    CreateClaimCommand,
    RejectClaimCommand,
)
from lifecase.storage import apply_migrations, connect_database
from lifecase.templates import (
    BUILT_IN_TEMPLATE_REGISTRY,
    EvaluateTemplateCommand,
    TemplateModelValidationError,
    TemplateNotFoundError,
    TemplateParseError,
    parse_case_template,
)
from lifecase.templates.service import (
    TemplateConfigurationError,
    TemplateEvaluationInputError,
    TemplateEvaluationPersistenceError,
    TemplateService,
)

_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[4] / "packages" / "case-templates" / "warranty_claim.v1.yaml"
)


def test_builtin_warranty_template_parses_completely_and_deterministically() -> None:
    payload = _TEMPLATE_PATH.read_bytes()

    first = parse_case_template(payload)
    second = parse_case_template(payload)
    registered = BUILT_IN_TEMPLATE_REGISTRY.load_for("warranty", 1)

    assert first == second == registered
    assert first.identity == "warranty_claim@1"
    assert first.title == "商品保修、维修或换新"
    assert first.goal_options == ("repair", "replace", "refund")
    assert first.required_claims == (
        "merchant_or_manufacturer",
        "product_name",
        "purchase_date",
        "order_number",
        "desired_outcome",
    )
    assert [(item.when, item.field) for item in first.conditional_claims] == [
        ("manufacturer_requires_serial", "serial_number")
    ]
    assert [item.key for item in first.evidence_requirements] == [
        "purchase_proof",
        "defect_description",
        "defect_media",
    ]
    assert first.close_conditions == (
        "repaired_and_received",
        "replacement_received",
        "refund_received",
        "user_abandoned",
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda text: text + "unknown_key: value\n",
        lambda text: text.replace(
            "    field: serial_number", "    field: serial_number\n    extra: x"
        ),
        lambda text: text.replace("id: warranty_claim", "id: warranty_claim\nid: duplicate"),
        lambda text: text.replace(
            "    field: serial_number", "    field: serial_number\n    field: duplicate"
        ),
        lambda text: text.replace("id: warranty_claim", "id: !unsafe warranty_claim"),
        lambda text: text.replace("id: warranty_claim", "id: &template_id warranty_claim").replace(
            "title: 商品保修、维修或换新", "title: *template_id"
        ),
        lambda text: text.replace("version: 1", "version: true"),
        lambda text: text.replace("title: 商品保修、维修或换新", "title: ''"),
        lambda text: text.replace("title: 商品保修、维修或换新\n", ""),
        lambda text: text.replace("  - refund", "  - repair", 1),
        lambda text: text.replace("  - product_name", "  - Invalid-Field"),
        lambda text: text.replace("title: 商品保修、维修或换新", f"title: {'x' * 161}"),
    ],
)
def test_template_parser_rejects_unsafe_or_ambiguous_yaml(
    mutator: Callable[[str], str],
    tmp_path: Path,
) -> None:
    original = _TEMPLATE_PATH.read_text(encoding="utf-8")
    mutated = mutator(original)

    with pytest.raises(TemplateParseError) as captured:
        parse_case_template(mutated.encode())

    assert str(captured.value) == "Built-in template is invalid"
    assert "warranty_claim" not in str(captured.value)
    assert str(tmp_path) not in str(captured.value)


def test_template_parser_rejects_overdeep_and_oversized_input() -> None:
    overdeep = (
        "id: warranty_claim\nversion: 1\ntitle: x\ngoal_options: "
        + "[" * 9
        + "repair"
        + "]" * 9
        + "\n"
    ).encode()

    with pytest.raises(TemplateParseError):
        parse_case_template(overdeep)
    with pytest.raises(TemplateParseError):
        parse_case_template(b"x" * 65_537)


def test_evaluation_command_has_finite_typed_boundaries() -> None:
    command = EvaluateTemplateCommand(
        case_id=" case_one ",
        active_conditions=frozenset({"manufacturer_requires_serial"}),
        completed_close_conditions=frozenset({"refund_received"}),
    )

    assert command.case_id == "case_one"
    with pytest.raises(TemplateModelValidationError):
        EvaluateTemplateCommand(case_id="case_one", active_conditions={"bad"})  # type: ignore[arg-type]
    with pytest.raises(TemplateModelValidationError):
        EvaluateTemplateCommand(case_id=" ")


def test_unknown_template_type_or_version_is_typed_and_safe(tmp_path: Path) -> None:
    with pytest.raises(TemplateNotFoundError) as captured:
        BUILT_IN_TEMPLATE_REGISTRY.load_for("unknown_type", 1)

    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    try:
        with pytest.raises(CaseValidationError):
            CaseService(connection).create_case(
                title="Synthetic unknown template",
                goal="Reject an unregistered binding",
                template_id="unknown_type",
            )
    finally:
        connection.close()

    assert "unknown_type" not in str(captured.value)
    assert str(tmp_path) not in str(captured.value)


def test_missing_evidence_uses_only_authoritative_satisfying_claim_states(
    tmp_path: Path,
) -> None:
    connection, case_id = _database_with_case(tmp_path)
    claims = ClaimService(connection)
    try:
        _create_claim(claims, case_id, "merchant_or_manufacturer", ClaimStatus.CANDIDATE)
        _create_claim(claims, case_id, "product_name", ClaimStatus.INFERRED)
        rejected = _create_claim(claims, case_id, "purchase_date", ClaimStatus.CANDIDATE)
        claims.reject_claim(
            RejectClaimCommand(
                claim_id=rejected.id,
                expected_version=1,
                actor="local_user",
                reason="Synthetic rejection",
            )
        )
        _confirmed_claim(claims, case_id, "order_number")
        _corrected_claim(claims, case_id, "desired_outcome")
        _confirmed_claim(claims, case_id, "purchase_proof")

        result = TemplateService(connection).evaluate(EvaluateTemplateCommand(case_id=case_id))

        assert result.missing_required_claims == (
            "merchant_or_manufacturer",
            "product_name",
            "purchase_date",
        )
        assert result.missing_conditional_claims == ()
        assert [item.key for item in result.missing_evidence_requirements] == [
            "defect_description",
            "defect_media",
        ]
    finally:
        connection.close()


def test_contradictory_claim_never_satisfies_a_template_requirement(tmp_path: Path) -> None:
    connection, case_id = _database_with_case(tmp_path)
    try:
        claim = _confirmed_claim(ClaimService(connection), case_id, "order_number")
        connection.execute("UPDATE claim SET contradiction = 1 WHERE id = ?", (claim.id,))

        result = TemplateService(connection).evaluate(EvaluateTemplateCommand(case_id=case_id))

        assert "order_number" in result.missing_required_claims
    finally:
        connection.close()


def test_template_claim_satisfaction_is_case_scoped_in_sql(tmp_path: Path) -> None:
    connection, case_id = _database_with_case(tmp_path)
    other_case = CaseService(connection).create_case(
        title="Other synthetic Case",
        goal="Prove template Case isolation",
        template_id="warranty",
    )
    try:
        _confirmed_claim(ClaimService(connection), other_case.id, "order_number")

        result = TemplateService(connection).evaluate(EvaluateTemplateCommand(case_id=case_id))

        assert "order_number" in result.missing_required_claims
    finally:
        connection.close()


def test_conditional_inputs_are_finite_order_independent_and_validated(tmp_path: Path) -> None:
    connection, case_id = _database_with_case(tmp_path)
    service = TemplateService(connection)
    try:
        inactive = service.evaluate(EvaluateTemplateCommand(case_id=case_id))
        active = service.evaluate(
            EvaluateTemplateCommand(
                case_id=case_id,
                active_conditions=frozenset({"manufacturer_requires_serial"}),
            )
        )

        assert inactive.missing_conditional_claims == ()
        assert active.missing_conditional_claims == ("serial_number",)
        with pytest.raises(TemplateEvaluationInputError):
            service.evaluate(
                EvaluateTemplateCommand(
                    case_id=case_id,
                    active_conditions=frozenset({"unknown_condition"}),
                )
            )
    finally:
        connection.close()


def test_close_condition_is_pure_and_any_declared_outcome_can_close(tmp_path: Path) -> None:
    connection, case_id = _database_with_case(tmp_path)
    service = TemplateService(connection)
    try:
        before = CaseService(connection).get_case(case_id)
        result = service.evaluate(
            EvaluateTemplateCommand(
                case_id=case_id,
                completed_close_conditions=frozenset({"refund_received", "repaired_and_received"}),
            )
        )
        after = CaseService(connection).get_case(case_id)

        assert result.close_conditions.satisfied == (
            "repaired_and_received",
            "refund_received",
        )
        assert result.close_conditions.can_close is True
        assert after == before
        assert after.status is CaseStatus.INTAKE
    finally:
        connection.close()


def test_case_persists_real_template_identity_and_restores_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection = connect_database(database_path)
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic warranty Case",
        goal="Verify template identity",
        template_id="warranty",
    )
    connection.close()

    restarted = connect_database(database_path)
    try:
        restored = CaseService(restarted).get_case(case.id)
        assert (restored.type, restored.template_id, restored.template_version) == (
            "warranty",
            "warranty_claim",
            1,
        )
        evaluation = TemplateService(restarted).evaluate(EvaluateTemplateCommand(case_id=case.id))
        assert (evaluation.template_id, evaluation.template_version) == (
            "warranty_claim",
            1,
        )
    finally:
        restarted.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [("template_id", "wrong_template"), ("template_version", 2), ("type", "unknown")],
)
def test_unknown_or_mismatched_case_template_binding_fails_closed(
    tmp_path: Path,
    column: str,
    value: object,
) -> None:
    connection, case_id = _database_with_case(tmp_path)
    try:
        connection.execute(f"UPDATE case_record SET {column} = ? WHERE id = ?", (value, case_id))

        with pytest.raises(CaseTemplateBindingError):
            CaseService(connection).get_case(case_id)
        with pytest.raises(TemplateConfigurationError):
            TemplateService(connection).evaluate(EvaluateTemplateCommand(case_id=case_id))
    finally:
        connection.close()


def test_template_evaluation_database_failure_is_typed_and_safe(tmp_path: Path) -> None:
    connection, case_id = _database_with_case(tmp_path)
    connection.close()

    with pytest.raises(TemplateEvaluationPersistenceError) as captured:
        TemplateService(connection).evaluate(EvaluateTemplateCommand(case_id=case_id))

    assert "closed database" not in str(captured.value)
    assert str(tmp_path) not in str(captured.value)


def test_case_template_binding_write_failure_rolls_back_without_partial_case(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    connection.execute(
        """
        CREATE TRIGGER fail_template_bound_case
        BEFORE INSERT ON case_record
        BEGIN
            SELECT RAISE(ABORT, 'synthetic private template persistence detail');
        END
        """
    )
    try:
        with pytest.raises(CasePersistenceError) as captured:
            CaseService(connection).create_case(
                title="Synthetic rollback Case",
                goal="Verify atomic binding write",
                template_id="warranty",
            )

        assert connection.execute("SELECT COUNT(*) FROM case_record").fetchone()[0] == 0
        assert connection.in_transaction is False
        assert "synthetic private template persistence detail" not in str(captured.value)
        assert str(tmp_path) not in str(captured.value)
    finally:
        connection.close()


def _database_with_case(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title="Synthetic template Case",
        goal="Evaluate deterministic template gaps",
        template_id="warranty",
    )
    return connection, case.id


def _create_claim(
    service: ClaimService,
    case_id: str,
    field: str,
    status: ClaimStatus,
) -> ClaimRecord:
    return service.create_claim(
        CreateClaimCommand(
            case_id=case_id,
            field=field,
            value=ClaimValue.from_input(f"Synthetic {field}"),
            status=status,
            confidence=0.99,
            created_by=ClaimCreatedBy.AGENT,
            actor="agent_run_synthetic",
            reason="Synthetic template evaluation fixture",
        )
    )


def _confirmed_claim(service: ClaimService, case_id: str, field: str) -> ClaimRecord:
    claim = _create_claim(service, case_id, field, ClaimStatus.CANDIDATE)
    return service.confirm_claim(
        ConfirmClaimCommand(
            claim_id=claim.id,
            expected_version=1,
            basis=ClaimConfirmationBasis.USER,
            actor="local_user",
            reason="Explicit synthetic confirmation",
        )
    )


def _corrected_claim(service: ClaimService, case_id: str, field: str) -> ClaimRecord:
    claim = _create_claim(service, case_id, field, ClaimStatus.CANDIDATE)
    return service.correct_claim(
        CorrectClaimCommand(
            claim_id=claim.id,
            expected_version=1,
            value=ClaimValue.from_input(f"Corrected {field}"),
            actor="local_user",
            reason="Explicit synthetic correction",
        )
    )
