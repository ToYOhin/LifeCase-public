"""Product-path coverage for the reviewed subscription-cancellation template."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from lifecase.actions import (
    ActionKind,
    ActionPayload,
    ActionRiskLevel,
    ActionService,
    ActionStatus,
    ActionStatusCommand,
    ActionTransitionNotAllowedError,
    ClaimRequirementInput,
    CreateActionCommand,
    DeadlineLinkInput,
)
from lifecase.approvals import (
    ApprovalActionInvalidError,
    ApprovalDecisionCommand,
    ApprovalService,
    ApprovalSummary,
    RequestApprovalCommand,
)
from lifecase.cases import CaseService
from lifecase.claims import (
    ClaimConfirmationBasis,
    ClaimCreatedBy,
    ClaimRecord,
    ClaimService,
    ClaimSourceInput,
    ClaimSourceRelation,
    ClaimStatus,
    ClaimValue,
    ConfirmClaimCommand,
    CreateClaimCommand,
)
from lifecase.config import ApplicationConfig
from lifecase.deadlines import (
    CALENDAR_DAYS_AFTER_BASIS,
    CreateCalculatedDeadlineCommand,
    CreateExplicitDeadlineCommand,
    CreateSuggestedDeadlineCommand,
    DeadlineBasisInput,
    DeadlineDate,
    DeadlineDateKind,
    DeadlineKind,
    DeadlineRecord,
    DeadlineRuleParameters,
    DeadlineService,
    DeadlineStatusCommand,
)
from lifecase.evidence import SourceImporter
from lifecase.evidence.extraction.models import ExtractionConfig
from lifecase.evidence.extraction.service import ExtractionService
from lifecase.evidence.pack import EvidencePackService
from lifecase.storage import (
    ContentAddressedObjectStore,
    ObjectStoreConfig,
    apply_migrations,
    connect_database,
)
from lifecase.storage.backup import BackupService
from lifecase.templates import (
    BUILT_IN_TEMPLATE_REGISTRY,
    EvaluateTemplateCommand,
    TemplateBindingMismatchError,
    parse_case_template,
)
from lifecase.templates.service import TemplateService
from lifecase.timeline import TimelineService

_REPOSITORY = Path(__file__).resolve().parents[4]
_TEMPLATE_PATH = (
    _REPOSITORY / "packages" / "case-templates" / "subscription_cancellation_claim.v1.yaml"
)
_CORPUS_ROOT = _REPOSITORY / "fixtures" / "synthetic-cases" / "m7-v1"
_CASE_IDS = tuple(f"m7-subscription-cancellation-{index:02d}" for index in range(1, 5))
_RESTORE_CONFIRMATION = "RESTORE INTO NEW DIRECTORY"


def test_template_resource_hash_registry_order_and_wheel_mapping_are_closed() -> None:
    payload = _TEMPLATE_PATH.read_bytes()
    parsed = parse_case_template(payload)
    registered = BUILT_IN_TEMPLATE_REGISTRY.load_for("subscription_cancellation", 1)
    pyproject = tomllib.loads((_REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert parsed == registered
    assert registered.identity == "subscription_cancellation_claim@1"
    assert registered.content_sha256 == hashlib.sha256(payload).hexdigest()
    assert [item.case_type for item in BUILT_IN_TEMPLATE_REGISTRY.catalog()] == [
        "warranty",
        "return_refund",
        "reimbursement",
        "subscription_cancellation",
    ]
    assert (
        force_include["packages/case-templates/subscription_cancellation_claim.v1.yaml"]
        == "lifecase/templates/resources/subscription_cancellation_claim.v1.yaml"
    )
    with pytest.raises(TemplateBindingMismatchError):
        BUILT_IN_TEMPLATE_REGISTRY.load_exact(
            case_type="subscription_cancellation",
            template_id="unreviewed_template",
            template_version=1,
        )


def test_four_fixed_cases_use_real_product_services_and_survive_restore(tmp_path: Path) -> None:
    config = _config(tmp_path)
    connection = connect_database(config.database_path)
    apply_migrations(connection)
    config.upload_root.mkdir(parents=True)
    importer = SourceImporter(
        connection,
        ContentAddressedObjectStore(
            ObjectStoreConfig(
                data_root=config.data_root,
                allowed_source_roots=(config.upload_root,),
                max_file_size_bytes=1_000_000,
                allowed_media_types=frozenset({"text/plain"}),
            )
        ),
    )
    created: list[tuple[str, str, tuple[str, ...], tuple[str, ...], str]] = []
    try:
        for fixture_id in _CASE_IDS:
            fixture = _CORPUS_ROOT / fixture_id
            observed = _read_json(fixture / "observed.json")
            expected_gaps = _read_json(fixture / "expected" / "gaps.json")
            expected_deadline = _read_json(fixture / "expected" / "deadlines.json")["items"][0]
            expected_action = _read_json(fixture / "expected" / "actions.json")["items"][0]

            case = CaseService(connection).create_case(
                title=f"Synthetic product path {fixture_id}",
                goal="Exercise the reviewed subscription cancellation workflow",
                template_id="subscription_cancellation",
            )
            selected = config.upload_root / f"{fixture_id}.txt"
            selected.write_bytes((fixture / "input" / "evidence.txt").read_bytes())
            imported = importer.import_selected(case_id=case.id, selected_path=selected)
            extraction = ExtractionService(
                connection,
                ExtractionConfig(data_root=config.data_root),
            ).extract_source(imported.source_id)
            assert len(extraction.segments) == 1
            segment = extraction.segments[0]

            claims = _create_fixture_claims(connection, case.id, observed, segment)
            evaluation = TemplateService(connection).evaluate(
                EvaluateTemplateCommand(case_id=case.id)
            )
            missing_claims = tuple(expected_gaps["missing_claims"])
            missing_evidence = tuple(expected_gaps["missing_evidence"])
            assert evaluation.missing_required_claims == missing_claims
            assert tuple(item.key for item in evaluation.missing_evidence_requirements) == (
                missing_evidence
            )
            assert [f"missing_{field}" for field in missing_claims] == expected_gaps["blockers"]
            for claim in claims.values():
                assert ClaimService(connection).list_claim_sources(claim.id)

            deadline = _create_fixture_deadline(
                connection,
                case.id,
                claims["purchase_or_event_date"],
                expected_deadline,
            )
            assert (deadline.kind.value, deadline.due.kind.value, deadline.due.value) == (
                expected_deadline["kind"],
                expected_deadline["date_kind"],
                expected_deadline["due_value"],
            )
            deadline = DeadlineService(connection).confirm(
                DeadlineStatusCommand(
                    deadline.id,
                    deadline.version,
                    "local_user",
                    "Confirm synthetic subscription deadline",
                )
            )

            action = _create_fixture_action(
                connection,
                case.id,
                claims,
                deadline,
                expected_action,
            )
            assert (action.kind.value, action.risk_level.value) == (
                expected_action["kind"],
                expected_action["risk_level"],
            )
            if expected_action["requires_approval"]:
                approval = ApprovalService(connection).request(
                    RequestApprovalCommand(
                        action.id,
                        action.version,
                        action.payload.sha256,
                        ApprovalSummary.from_input("Approve exact synthetic cancellation request"),
                        "local_user",
                        "Request synthetic subscription Approval",
                    )
                )
                _, action = ApprovalService(connection).approve(
                    ApprovalDecisionCommand(
                        approval.id,
                        action.id,
                        approval.version,
                        action.version,
                        "local_user",
                        "Approve synthetic subscription request",
                    )
                )
                with pytest.raises(ActionTransitionNotAllowedError):
                    ActionService(connection).start(
                        ActionStatusCommand(
                            action.id,
                            action.version,
                            "local_user",
                            "Risk C remains record-only",
                        )
                    )
            else:
                with pytest.raises(ApprovalActionInvalidError):
                    ApprovalService(connection).request(
                        RequestApprovalCommand(
                            action.id,
                            action.version,
                            action.payload.sha256,
                            ApprovalSummary.from_input("Risk A needs no Approval"),
                            "local_user",
                            "Reject unnecessary Approval",
                        )
                    )
                action = ActionService(connection).start(
                    ActionStatusCommand(
                        action.id,
                        action.version,
                        "local_user",
                        "Start deterministic evidence collection",
                    )
                )
                assert action.status is ActionStatus.RUNNING

            timeline = TimelineService(connection).list_case_timeline(case.id)
            assert timeline.events
            pack = EvidencePackService(connection, config.data_root).create(case.id)
            created.append((case.id, pack.id, missing_claims, missing_evidence, fixture_id))
    finally:
        connection.close()

    restarted = connect_database(config.database_path)
    try:
        for case_id, pack_id, missing_claims, missing_evidence, _fixture_id in created:
            case = CaseService(restarted).get_case(case_id)
            evaluation = TemplateService(restarted).evaluate(
                EvaluateTemplateCommand(case_id=case_id)
            )
            assert (case.type, case.template_id, case.template_version) == (
                "subscription_cancellation",
                "subscription_cancellation_claim",
                1,
            )
            assert evaluation.missing_required_claims == missing_claims
            assert tuple(item.key for item in evaluation.missing_evidence_requirements) == (
                missing_evidence
            )
            assert len(DeadlineService(restarted).list_case_deadlines(case_id)) == 1
            assert len(ActionService(restarted).list_case_actions(case_id)) == 1
            assert TimelineService(restarted).list_case_timeline(case_id).events
            assert (
                EvidencePackService(restarted, config.data_root)
                .get_download(pack_id)
                .path.is_file()
            )

        backup = BackupService(restarted, config.database_path, config.data_root)
        artifact = backup.create()
        validation = backup.validate(backup.get_download(artifact.id).path)
        restored = backup.apply(
            validation.validation_id,
            "subscription-cancellation-copy",
            _RESTORE_CONFIRMATION,
        )
    finally:
        restarted.close()

    restored_root = config.database_path.parent / "recovery" / "restored" / restored.runtime_id
    restored_connection = connect_database(restored_root / "lifecase.sqlite3")
    try:
        for case_id, pack_id, missing_claims, missing_evidence, fixture_id in created:
            evaluation = TemplateService(restored_connection).evaluate(
                EvaluateTemplateCommand(case_id=case_id)
            )
            assert evaluation.missing_required_claims == missing_claims
            assert tuple(item.key for item in evaluation.missing_evidence_requirements) == (
                missing_evidence
            )
            assert TimelineService(restored_connection).list_case_timeline(case_id).events
            assert (
                EvidencePackService(
                    restored_connection,
                    restored_root / "data",
                )
                .get_download(pack_id)
                .path.is_file()
            )
            expected_approvals = 0 if fixture_id.endswith("-04") else 1
            assert len(ApprovalService(restored_connection).list_case_approvals(case_id)) == (
                expected_approvals
            )
    finally:
        restored_connection.close()


def _config(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _create_fixture_claims(
    connection: Any,
    case_id: str,
    observed: dict[str, Any],
    segment: Any,
) -> dict[str, ClaimRecord]:
    service = ClaimService(connection)
    claims: dict[str, ClaimRecord] = {}
    for field in observed["critical_fields"]:
        if field["status"] != "fact":
            continue
        citation = field["citations"][0]
        source = ClaimSourceInput(
            source_id=segment.source_id,
            segment_id=segment.id,
            segment_text_sha256=segment.text_sha256,
            quote_start=citation["quote_start"],
            quote_end=citation["quote_end"],
            quote_hash=citation["quote_sha256"],
            relation=ClaimSourceRelation.SUPPORTS,
        )
        claim = service.create_claim(
            CreateClaimCommand(
                case_id=case_id,
                field=field["field"],
                value=ClaimValue.from_input(field["value"]),
                status=ClaimStatus.CANDIDATE,
                confidence=1.0,
                created_by=ClaimCreatedBy.USER,
                actor="local_user",
                reason="Create deterministic fixture Claim",
                sources=(source,),
            )
        )
        claims[field["field"]] = service.confirm_claim(
            ConfirmClaimCommand(
                claim.id,
                claim.version,
                ClaimConfirmationBasis.USER,
                "local_user",
                "Confirm deterministic fixture Claim",
            )
        )
    reference = claims.get("order_or_reference")
    if reference is not None:
        source = service.list_claim_sources(reference.id)[0]
        evidence = service.create_claim(
            CreateClaimCommand(
                case_id=case_id,
                field="reference_evidence",
                value=ClaimValue.from_input("Reference evidence recorded"),
                status=ClaimStatus.CANDIDATE,
                confidence=1.0,
                created_by=ClaimCreatedBy.USER,
                actor="local_user",
                reason="Record deterministic reference evidence",
                sources=(
                    ClaimSourceInput(
                        source.source_id,
                        source.segment_id,
                        source.segment_text_sha256,
                        source.quote_start,
                        source.quote_end,
                        source.quote_hash,
                        source.relation,
                    ),
                ),
            )
        )
        claims["reference_evidence"] = service.confirm_claim(
            ConfirmClaimCommand(
                evidence.id,
                evidence.version,
                ClaimConfirmationBasis.USER,
                "local_user",
                "Confirm deterministic reference evidence",
            )
        )
    return claims


def _create_fixture_deadline(
    connection: Any,
    case_id: str,
    basis_claim: ClaimRecord,
    expected: dict[str, Any],
) -> DeadlineRecord:
    service = DeadlineService(connection)
    date_kind = DeadlineDateKind(expected["date_kind"])
    basis = DeadlineBasisInput(
        basis_claim.id,
        basis_claim.version,
        basis_claim.value.sha256,
        date_kind,
    )
    kind = DeadlineKind(expected["kind"])
    if kind is DeadlineKind.EXPLICIT:
        return service.create_explicit(
            CreateExplicitDeadlineCommand(
                case_id,
                basis,
                expected["timezone"],
                "local_user",
                "Create evidence-backed subscription deadline",
            )
        )
    if kind is DeadlineKind.CALCULATED:
        rule = expected["rule"]
        return service.create_calculated(
            CreateCalculatedDeadlineCommand(
                case_id,
                (basis,),
                expected["timezone"],
                CALENDAR_DAYS_AFTER_BASIS,
                rule["rule_version"],
                DeadlineRuleParameters.from_input(rule["parameters"]),
                "local_rule",
                "Calculate deterministic subscription deadline",
            )
        )
    return service.create_suggested(
        CreateSuggestedDeadlineCommand(
            case_id,
            DeadlineDate(date_kind, expected["due_value"], expected["timezone"]),
            "local_user",
            "Create suggested subscription follow-up",
        )
    )


def _create_fixture_action(
    connection: Any,
    case_id: str,
    claims: dict[str, ClaimRecord],
    deadline: DeadlineRecord,
    expected: dict[str, Any],
) -> Any:
    kind = ActionKind(expected["kind"])
    if kind is ActionKind.COLLECT_EVIDENCE:
        payload = ActionPayload.from_input(
            kind,
            {"requirement_key": "order_or_reference", "requirement_type": "claim"},
        )
        requirements: tuple[ClaimRequirementInput, ...] = ()
    else:
        payload = ActionPayload.from_input(
            kind,
            {"intent": "request_remedy", "recipient_role": "merchant"},
        )
        requirements = tuple(
            ClaimRequirementInput(
                claims[field].id,
                claims[field].version,
                claims[field].value.sha256,
                field,
            )
            for field in BUILT_IN_TEMPLATE_REGISTRY.load_for(
                "subscription_cancellation", 1
            ).required_claims
        )
    return ActionService(connection).create_action(
        CreateActionCommand(
            case_id=case_id,
            kind=kind,
            risk_level=ActionRiskLevel(expected["risk_level"]),
            payload=payload,
            requirements=requirements,
            dependencies=(),
            deadline=DeadlineLinkInput(
                deadline.id,
                deadline.version,
                deadline.definition_sha256,
            ),
            actor="local_user",
            reason="Create deterministic subscription Action",
        )
    )
