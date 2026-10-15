"""Coverage for reviewed templates added after the warranty baseline."""

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifecase.api.templates as templates_api
from lifecase.actions import (
    ActionKind,
    ActionPayload,
    ActionRiskLevel,
    ActionService,
    ClaimRequirementInput,
    CreateActionCommand,
)
from lifecase.cases import CaseService
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
from lifecase.config import ApplicationConfig
from lifecase.evidence import SourceImporter
from lifecase.evidence.pack import EvidencePackService
from lifecase.main import create_app
from lifecase.storage import (
    ContentAddressedObjectStore,
    ObjectStoreConfig,
    apply_migrations,
    connect_database,
)
from lifecase.storage.backup import BackupService
from lifecase.templates import BUILT_IN_TEMPLATE_REGISTRY, EvaluateTemplateCommand
from lifecase.templates.registry import TemplateResourceError
from lifecase.templates.service import (
    TemplateEvaluationPersistenceError,
    TemplateService,
)

_EXPECTED = {
    "return_refund": {
        "identity": "return_refund_claim@1",
        "required": (
            "merchant",
            "item_name",
            "order_number",
            "paid_amount",
            "delivery_date",
            "return_reason",
            "desired_outcome",
        ),
        "condition": ("merchant_requires_return_tracking", "return_tracking_number"),
        "evidence": ("purchase_proof", "delivery_proof", "item_condition"),
        "close": "refund_received",
    },
    "reimbursement": {
        "identity": "expense_reimbursement@1",
        "required": (
            "expense_date",
            "merchant",
            "amount",
            "currency",
            "expense_category",
            "business_purpose",
        ),
        "condition": ("policy_requires_preapproval", "approval_reference"),
        "evidence": ("receipt",),
        "close": "reimbursement_received",
    },
    "subscription_cancellation": {
        "identity": "subscription_cancellation_claim@1",
        "required": (
            "merchant",
            "amount",
            "purchase_or_event_date",
            "order_or_reference",
            "desired_outcome",
        ),
        "condition": ("cancellation_confirmed", "cancellation_confirmation_reference"),
        "evidence": ("reference_evidence",),
        "close": "cancellation_confirmed",
    },
}


def test_reviewed_catalog_contains_four_distinct_deterministic_templates() -> None:
    first = BUILT_IN_TEMPLATE_REGISTRY.catalog()
    second = BUILT_IN_TEMPLATE_REGISTRY.catalog()

    assert first == second
    assert [item.case_type for item in first] == [
        "warranty",
        "return_refund",
        "reimbursement",
        "subscription_cancellation",
    ]
    assert [f"{item.template_id}@{item.template_version}" for item in first] == [
        "warranty_claim@1",
        "return_refund_claim@1",
        "expense_reimbursement@1",
        "subscription_cancellation_claim@1",
    ]
    assert len({item.content_sha256 for item in first}) == 4
    assert len({item.required_claims for item in first}) == 4


def test_catalog_exposes_bound_conditional_fields_without_promoting_required_claims(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        response = client.get("/api/v1/templates")

    assert response.status_code == 200
    catalog = response.json()
    for item in catalog:
        template = BUILT_IN_TEMPLATE_REGISTRY.load_exact(
            case_type=item["case_type"],
            template_id=item["template_id"],
            template_version=item["template_version"],
        )
        assert item["conditional_claims"] == [
            {"when": condition.when, "field": condition.field}
            for condition in template.conditional_claims
        ]
        assert item["required_claims"] == list(template.required_claims)
        assert item["content_sha256"] == template.content_sha256
    warranty = next(item for item in catalog if item["case_type"] == "warranty")
    assert warranty["conditional_claims"] == [
        {"when": "manufacturer_requires_serial", "field": "serial_number"}
    ]
    assert "serial_number" not in warranty["required_claims"]


@pytest.mark.parametrize("case_type", list(_EXPECTED))
def test_additional_template_semantics_are_explicit_and_pure(
    tmp_path: Path,
    case_type: str,
) -> None:
    expected = _EXPECTED[case_type]
    template = BUILT_IN_TEMPLATE_REGISTRY.load_for(case_type, 1)
    condition, conditional_field = expected["condition"]
    connection, case_id = _database_with_case(tmp_path, case_type)
    try:
        before = CaseService(connection).get_case(case_id)
        evaluation = TemplateService(connection).evaluate(
            EvaluateTemplateCommand(
                case_id=case_id,
                active_conditions=frozenset({condition}),
                completed_close_conditions=frozenset({expected["close"]}),
            )
        )
        after = CaseService(connection).get_case(case_id)

        assert template.identity == expected["identity"]
        assert template.required_claims == expected["required"]
        assert [(item.when, item.field) for item in template.conditional_claims] == [
            (condition, conditional_field)
        ]
        assert tuple(item.key for item in template.evidence_requirements) == expected["evidence"]
        assert evaluation.missing_required_claims == expected["required"]
        assert evaluation.missing_conditional_claims == (conditional_field,)
        assert (
            tuple(item.key for item in evaluation.missing_evidence_requirements)
            == expected["evidence"]
        )
        assert evaluation.close_conditions.can_close is True
        assert after == before
    finally:
        connection.close()


@pytest.mark.parametrize("case_type", list(_EXPECTED))
def test_additional_templates_accept_only_current_authoritative_same_case_claims(
    tmp_path: Path,
    case_type: str,
) -> None:
    connection, case_id = _database_with_case(tmp_path, case_type)
    other = CaseService(connection).create_case(
        title="Other synthetic Case",
        goal="Prove Case-scoped template authority",
        template_id=case_type,
    )
    field = _EXPECTED[case_type]["required"][0]
    claims = ClaimService(connection)
    try:
        candidate = _create_claim(claims, case_id, field, ClaimStatus.CANDIDATE, "candidate")
        _create_claim(claims, case_id, field, ClaimStatus.INFERRED, "inferred")
        rejected = _create_claim(claims, case_id, field, ClaimStatus.CANDIDATE, "rejected")
        claims.reject_claim(
            RejectClaimCommand(rejected.id, 1, "local_user", "Reject synthetic value")
        )
        _confirmed_claim(claims, other.id, field, "cross-case")

        unresolved = TemplateService(connection).evaluate(EvaluateTemplateCommand(case_id=case_id))
        assert field in unresolved.missing_required_claims

        confirmed = claims.confirm_claim(
            ConfirmClaimCommand(
                candidate.id,
                1,
                ClaimConfirmationBasis.USER,
                "local_user",
                "Confirm exact synthetic value",
            )
        )
        resolved = TemplateService(connection).evaluate(EvaluateTemplateCommand(case_id=case_id))
        assert field not in resolved.missing_required_claims

        corrected = claims.correct_claim(
            CorrectClaimCommand(
                confirmed.id,
                confirmed.version,
                ClaimValue.from_input("corrected synthetic authority"),
                "local_user",
                "Correct exact synthetic value",
            )
        )
        corrected_result = TemplateService(connection).evaluate(
            EvaluateTemplateCommand(case_id=case_id)
        )
        assert field not in corrected_result.missing_required_claims

        connection.execute("UPDATE claim SET contradiction = 1 WHERE id = ?", (corrected.id,))
        contradictory = TemplateService(connection).evaluate(
            EvaluateTemplateCommand(case_id=case_id)
        )
        assert field in contradictory.missing_required_claims
        connection.execute("UPDATE claim SET contradiction = 0 WHERE id = ?", (corrected.id,))

        connection.execute(
            "UPDATE claim SET value_sha256 = ? WHERE id = ?",
            ("0" * 64, corrected.id),
        )
        with pytest.raises(TemplateEvaluationPersistenceError):
            TemplateService(connection).evaluate(EvaluateTemplateCommand(case_id=case_id))
    finally:
        connection.close()


def test_additional_case_api_selection_persists_and_recovers_exact_binding(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    created: list[dict[str, object]] = []
    with TestClient(create_app(config)) as client:
        catalog = client.get("/api/v1/templates")
        for case_type in _EXPECTED:
            response = client.post(
                "/api/v1/cases",
                json={
                    "title": f"Synthetic {case_type} Case",
                    "goal": "Exercise explicit reviewed template selection",
                    "template_id": case_type,
                },
            )
            assert response.status_code == 201
            created.append(response.json())
        invalid = client.post(
            "/api/v1/cases",
            json={"title": "Invalid", "goal": "Reject unknown", "template_id": "unknown"},
        )

    assert catalog.status_code == 200
    assert [item["case_type"] for item in catalog.json()] == [
        "warranty",
        "return_refund",
        "reimbursement",
        "subscription_cancellation",
    ]
    assert [(item["type"], item["template_id"], item["template_version"]) for item in created] == [
        ("return_refund", "return_refund_claim", 1),
        ("reimbursement", "expense_reimbursement", 1),
        ("subscription_cancellation", "subscription_cancellation_claim", 1),
    ]
    assert invalid.status_code == 422
    assert "unknown" not in invalid.text

    with TestClient(create_app(config)) as restarted:
        restored = [restarted.get(f"/api/v1/cases/{item['id']}").json() for item in created]
    assert restored == created


def test_template_catalog_failure_is_safe_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_catalog() -> object:
        raise TemplateResourceError("synthetic private template body contains credential")

    monkeypatch.setattr(templates_api.BUILT_IN_TEMPLATE_REGISTRY, "catalog", fail_catalog)
    with TestClient(create_app(_config(tmp_path))) as client:
        response = client.get("/api/v1/templates")

    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "template_catalog_unavailable",
    )
    assert response.json()["error"]["retryable"] is True
    assert "private template body" not in response.text
    assert "credential" not in response.text


def test_three_synthetic_additional_cases_survive_action_pack_backup_and_restore(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.database_path.parent.mkdir(parents=True)
    config.data_root.mkdir(parents=True)
    connection = connect_database(config.database_path)
    apply_migrations(connection)
    config.upload_root.mkdir(parents=True)
    importer = SourceImporter(
        connection,
        ContentAddressedObjectStore(
            ObjectStoreConfig(
                data_root=config.data_root,
                allowed_source_roots=(config.upload_root,),
                max_file_size_bytes=4096,
                allowed_media_types=frozenset({"text/plain"}),
            )
        ),
    )
    cases: list[tuple[str, str, str]] = []
    try:
        for case_type, title in (
            ("return_refund", "Synthetic online purchase return"),
            ("reimbursement", "Synthetic travel expense reimbursement"),
            ("subscription_cancellation", "Synthetic subscription cancellation"),
        ):
            case = CaseService(connection).create_case(
                title=title,
                goal="Complete one local synthetic administrative workflow",
                template_id=case_type,
            )
            selected = config.upload_root / f"{case_type}-synthetic-material.txt"
            selected.write_text(
                f"Synthetic local material for {title}. No personal data.\n",
                encoding="utf-8",
            )
            imported = importer.import_selected(case_id=case.id, selected_path=selected)
            assert (
                connection.execute(
                    "SELECT case_id FROM source WHERE id = ?",
                    (imported.source_id,),
                ).fetchone()[0]
                == case.id
            )
            template = BUILT_IN_TEMPLATE_REGISTRY.load_for(case_type, 1)
            confirmed = {
                field: _confirmed_claim(
                    ClaimService(connection),
                    case.id,
                    field,
                    f"synthetic {field}",
                )
                for field in (
                    *template.required_claims,
                    *(item.key for item in template.evidence_requirements),
                )
            }
            evaluation = TemplateService(connection).evaluate(
                EvaluateTemplateCommand(case_id=case.id)
            )
            assert evaluation.missing_required_claims == ()
            assert evaluation.missing_evidence_requirements == ()

            action = ActionService(connection).create_action(
                CreateActionCommand(
                    case_id=case.id,
                    kind=ActionKind.PREPARE_COMMUNICATION,
                    risk_level=ActionRiskLevel.B,
                    payload=ActionPayload.from_input(
                        ActionKind.PREPARE_COMMUNICATION,
                        {"intent": "request_remedy", "recipient_role": "organization"},
                    ),
                    requirements=tuple(
                        ClaimRequirementInput(
                            confirmed[field].id,
                            confirmed[field].version,
                            confirmed[field].value.sha256,
                            field,
                        )
                        for field in template.required_claims
                    ),
                    dependencies=(),
                    deadline=None,
                    actor="local_user",
                    reason="Prepare synthetic M7-01 local communication",
                )
            )
            pack = EvidencePackService(connection, config.data_root).create(case.id)
            pack_download = EvidencePackService(connection, config.data_root).get_download(pack.id)
            with zipfile.ZipFile(pack_download.path) as archive:
                exported_case = json.loads(archive.read("confirmed/case.json"))
            assert (
                exported_case["type"],
                exported_case["template_id"],
                exported_case["template_version"],
            ) == (case.type, case.template_id, case.template_version)
            cases.append((case.id, action.id, pack.id))

        backup_service = BackupService(connection, config.database_path, config.data_root)
        backup = backup_service.create()
        validation = backup_service.validate(backup_service.get_download(backup.id).path)
    finally:
        connection.close()

    restarted = connect_database(config.database_path)
    restored = BackupService(restarted, config.database_path, config.data_root).apply(
        validation.validation_id,
        "m7-additional-templates",
        "RESTORE INTO NEW DIRECTORY",
    )
    restarted.close()

    restored_root = config.database_path.parent / "recovery" / "restored" / restored.runtime_id
    restored_connection = connect_database(restored_root / "lifecase.sqlite3")
    try:
        for case_id, action_id, pack_id in cases:
            case = CaseService(restored_connection).get_case(case_id)
            evaluation = TemplateService(restored_connection).evaluate(
                EvaluateTemplateCommand(case_id=case_id)
            )
            actions = ActionService(restored_connection).list_case_actions(case_id)
            pack = EvidencePackService(
                restored_connection,
                restored_root / "data",
            ).get_download(pack_id)

            assert case.type in _EXPECTED
            assert evaluation.missing_required_claims == ()
            assert evaluation.missing_evidence_requirements == ()
            assert [item.id for item in actions] == [action_id]
            assert pack.path.is_file()
            assert (
                restored_connection.execute(
                    "SELECT COUNT(*) FROM source WHERE case_id = ?",
                    (case_id,),
                ).fetchone()[0]
                == 1
            )
    finally:
        restored_connection.close()


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _database_with_case(tmp_path: Path, case_type: str) -> tuple[sqlite3.Connection, str]:
    connection = connect_database(tmp_path / f"{case_type}.sqlite3")
    apply_migrations(connection)
    case = CaseService(connection).create_case(
        title=f"Synthetic {case_type} Case",
        goal="Evaluate reviewed template semantics",
        template_id=case_type,
    )
    return connection, case.id


def _create_claim(
    service: ClaimService,
    case_id: str,
    field: str,
    status: ClaimStatus,
    value: str,
) -> ClaimRecord:
    return service.create_claim(
        CreateClaimCommand(
            case_id=case_id,
            field=field,
            value=ClaimValue.from_input(value),
            status=status,
            confidence=0.95,
            created_by=ClaimCreatedBy.USER,
            actor="local_user",
            reason="Create synthetic M7-01 authority",
        )
    )


def _confirmed_claim(
    service: ClaimService,
    case_id: str,
    field: str,
    value: str,
) -> ClaimRecord:
    claim = _create_claim(service, case_id, field, ClaimStatus.CANDIDATE, value)
    return service.confirm_claim(
        ConfirmClaimCommand(
            claim.id,
            1,
            ClaimConfirmationBasis.USER,
            "local_user",
            "Confirm synthetic M7-01 authority",
        )
    )
