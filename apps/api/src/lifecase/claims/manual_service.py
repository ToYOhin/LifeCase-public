"""Deterministic user-authored Claim intake from reviewed evidence text."""

import hashlib
import sqlite3

from lifecase.cases.service import CaseService
from lifecase.claims.models import (
    ClaimCreatedBy,
    ClaimRecord,
    ClaimSourceInput,
    ClaimSourceRelation,
    ClaimStatus,
    ClaimValue,
    CreateClaimCommand,
)
from lifecase.claims.service import ClaimService, ClaimSourceInvalidError
from lifecase.templates.registry import BUILT_IN_TEMPLATE_REGISTRY


class ManualClaimValidationError(ValueError):
    """Raised when a manual Claim does not use a reviewed template field."""


class ManualClaimService:
    """Compose immutable template binding, evidence, and Claim authorities."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create_evidence_candidate(
        self,
        *,
        case_id: str,
        field: str,
        value: ClaimValue,
        segment_id: str,
        quote_start: int,
        quote_end: int,
        actor: str,
        reason: str,
    ) -> ClaimRecord:
        case = CaseService(self._connection).get_case(case_id)
        template = BUILT_IN_TEMPLATE_REGISTRY.load_exact(
            case_type=case.type,
            template_id=case.template_id,
            template_version=case.template_version,
        )
        allowed_fields = {
            *template.required_claims,
            *(item.field for item in template.conditional_claims),
            *(item.key for item in template.evidence_requirements),
        }
        if field not in allowed_fields:
            raise ManualClaimValidationError("Claim field is not in the bound template")

        row = self._connection.execute(
            """
            SELECT ss.source_id, s.case_id, ss.text, ss.text_sha256
            FROM source_segment AS ss
            JOIN source AS s ON s.id = ss.source_id
            WHERE ss.id = ?
            """,
            (segment_id,),
        ).fetchone()
        if row is None or str(row["case_id"]) != case_id:
            raise ClaimSourceInvalidError
        text = str(row["text"])
        text_sha256 = _sha256(text)
        if (
            str(row["text_sha256"]) != text_sha256
            or quote_start < 0
            or quote_end <= quote_start
            or quote_end > len(text)
        ):
            raise ClaimSourceInvalidError

        return ClaimService(self._connection).create_claim(
            CreateClaimCommand(
                case_id=case_id,
                field=field,
                value=value,
                status=ClaimStatus.CANDIDATE,
                confidence=None,
                created_by=ClaimCreatedBy.USER,
                actor=actor,
                reason=reason,
                sources=(
                    ClaimSourceInput(
                        source_id=str(row["source_id"]),
                        segment_id=segment_id,
                        segment_text_sha256=text_sha256,
                        quote_start=quote_start,
                        quote_end=quote_end,
                        quote_hash=_sha256(text[quote_start:quote_end]),
                        relation=ClaimSourceRelation.SUPPORTS,
                    ),
                ),
            )
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
