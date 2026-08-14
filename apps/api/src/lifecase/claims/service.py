"""Deterministic transactional service for Claim provenance."""

import hashlib
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from lifecase.claims.models import (
    CONFIRMABLE_CLAIM_STATUSES,
    CORRECTABLE_CLAIM_STATUSES,
    REJECTABLE_CLAIM_STATUSES,
    SOURCE_ATTACHABLE_CLAIM_STATUSES,
    AttachClaimSourceCommand,
    ClaimConfirmation,
    ClaimConfirmationBasis,
    ClaimCreatedBy,
    ClaimEvent,
    ClaimEventType,
    ClaimRecord,
    ClaimSource,
    ClaimSourceInput,
    ClaimSourceRelation,
    ClaimStatus,
    ClaimValue,
    ConfirmClaimCommand,
    CorrectClaimCommand,
    CreateClaimCommand,
    RejectClaimCommand,
    validate_case_id,
    validate_claim_id,
)


class ClaimServiceError(RuntimeError):
    """Base class for safe Claim service failures."""


class ClaimNotFoundError(ClaimServiceError):
    """Raised when a requested Claim does not exist."""


class ClaimCaseNotFoundError(ClaimServiceError):
    """Raised when a requested owning Case does not exist."""


class ClaimValidationError(ClaimServiceError):
    """Raised when a Claim read or mutation request is invalid."""


class ClaimSourceInvalidError(ClaimServiceError):
    """Raised when source provenance is missing, stale, or out of Case scope."""


class ClaimSourceRequiredError(ClaimServiceError):
    """Raised when evidence confirmation lacks a supporting source."""


class ClaimTransitionNotAllowedError(ClaimServiceError):
    """Raised when the requested Claim operation is not allowed."""


class ClaimVersionConflictError(ClaimServiceError):
    """Raised when a Claim command targets a stale version."""

    def __init__(self, current_version: int) -> None:
        super().__init__("Claim version does not match the command")
        self.current_version = current_version


class ClaimPersistenceError(ClaimServiceError):
    """Raised when Claim state cannot be read or committed safely."""


class ClaimService:
    """Own all Claim writes and authoritative provenance validation."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create_claim(self, command: CreateClaimCommand) -> ClaimRecord:
        """Atomically create one Claim, its sources, and its initial history."""

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            (claim_id,) = self.create_claims_in_transaction((command,))
            self._connection.execute("COMMIT")
        except ClaimServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, ValueError) as error:
            self._rollback()
            raise ClaimPersistenceError("Claim creation was rolled back") from error
        return self.get_claim(claim_id)

    def create_claims_in_transaction(
        self,
        commands: tuple[CreateClaimCommand, ...],
    ) -> tuple[str, ...]:
        """Create Claims inside an existing caller-owned SQLite transaction.

        This is the transaction-aware Claim write path used when another domain
        authority, such as an Agent checkpoint, must commit atomically with the
        Claim rows. The caller remains responsible for commit or rollback.
        """

        if not self._connection.in_transaction:
            raise ClaimPersistenceError("Claim batch requires an active transaction")
        if not isinstance(commands, tuple) or not all(
            isinstance(command, CreateClaimCommand) for command in commands
        ):
            raise ClaimPersistenceError("Claim batch is invalid")
        claim_ids: list[str] = []
        for command in commands:
            claim_ids.append(self._insert_claim(command))
        return tuple(claim_ids)

    def get_claim(self, claim_id: str) -> ClaimRecord:
        """Read one authoritative Claim without transport dependencies."""

        try:
            clean_claim_id = validate_claim_id(claim_id)
        except ValueError as error:
            raise ClaimValidationError("Claim ID is invalid") from error
        try:
            row = self._connection.execute(
                """
                SELECT id, case_id, field, value_json, value_sha256,
                       comparison_sha256, status, confidence, created_by,
                       contradiction, version, created_at, updated_at
                FROM claim WHERE id = ?
                """,
                (clean_claim_id,),
            ).fetchone()
            if row is None:
                raise ClaimNotFoundError
            return _claim_record(row)
        except ClaimServiceError:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise ClaimPersistenceError("Claim lookup failed") from error

    def list_case_claims(self, case_id: str) -> tuple[ClaimRecord, ...]:
        """Read one Case's Claims in deterministic field/creation order."""

        try:
            clean_case_id = validate_case_id(case_id)
        except ValueError as error:
            raise ClaimValidationError("Case ID is invalid") from error
        try:
            self._require_case(clean_case_id)
            rows = self._connection.execute(
                """
                SELECT id, case_id, field, value_json, value_sha256,
                       comparison_sha256, status, confidence, created_by,
                       contradiction, version, created_at, updated_at
                FROM claim
                WHERE case_id = ?
                ORDER BY field, created_at, id
                """,
                (clean_case_id,),
            ).fetchall()
            return tuple(_claim_record(row) for row in rows)
        except ClaimServiceError:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise ClaimPersistenceError("Case Claims could not be read") from error

    def list_claim_sources(self, claim_id: str) -> tuple[ClaimSource, ...]:
        """Rejoin and validate every Claim citation against authoritative rows."""

        try:
            clean_claim_id = validate_claim_id(claim_id)
        except ValueError as error:
            raise ClaimValidationError("Claim ID is invalid") from error
        try:
            claim = self.get_claim(clean_claim_id)
            return self._load_claim_sources(claim)
        except ClaimServiceError:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise ClaimPersistenceError("Claim sources could not be read") from error

    def attach_claim_source(self, command: AttachClaimSourceCommand) -> ClaimRecord:
        """Atomically attach one validated source and append one history event."""

        try:
            claim = self._begin_mutation(command.claim_id, command.expected_version)
            if claim.status not in SOURCE_ATTACHABLE_CLAIM_STATUSES:
                raise ClaimTransitionNotAllowedError
            occurred_at = _utc_now()
            self._insert_source(
                claim_id=claim.id,
                case_id=claim.case_id,
                source=command.source,
                actor=command.actor,
                attached_at=occurred_at,
            )
            new_version = claim.version + 1
            self._update_claim(
                claim=claim,
                status=claim.status,
                value=claim.value,
                new_version=new_version,
                updated_at=occurred_at,
            )
            self._insert_event(
                claim_id=claim.id,
                event_type=ClaimEventType.SOURCE_ATTACHED,
                prior_status=claim.status,
                new_status=claim.status,
                prior_value=claim.value,
                new_value=claim.value,
                prior_version=claim.version,
                new_version=new_version,
                actor=command.actor,
                reason=command.reason,
                occurred_at=occurred_at,
            )
            self._connection.execute("COMMIT")
        except ClaimServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, ValueError) as error:
            self._rollback()
            raise ClaimPersistenceError("Claim source attachment was rolled back") from error
        return self.get_claim(command.claim_id)

    def confirm_claim(self, command: ConfirmClaimCommand) -> ClaimRecord:
        """Confirm one active candidate using evidence or explicit user authority."""

        try:
            claim = self._begin_mutation(command.claim_id, command.expected_version)
            if claim.status not in CONFIRMABLE_CLAIM_STATUSES:
                raise ClaimTransitionNotAllowedError
            if command.basis is ClaimConfirmationBasis.EVIDENCE:
                sources = self._load_claim_sources(claim)
                if not any(source.relation is ClaimSourceRelation.SUPPORTS for source in sources):
                    raise ClaimSourceRequiredError

            occurred_at = _utc_now()
            new_version = claim.version + 1
            self._update_claim(
                claim=claim,
                status=ClaimStatus.CONFIRMED,
                value=claim.value,
                new_version=new_version,
                updated_at=occurred_at,
            )
            event_id = self._insert_event(
                claim_id=claim.id,
                event_type=ClaimEventType.CONFIRMED,
                prior_status=claim.status,
                new_status=ClaimStatus.CONFIRMED,
                prior_value=claim.value,
                new_value=claim.value,
                prior_version=claim.version,
                new_version=new_version,
                actor=command.actor,
                reason=command.reason,
                occurred_at=occurred_at,
            )
            self._connection.execute(
                """
                INSERT INTO claim_confirmation (
                    id, claim_id, event_id, claim_version, basis,
                    value_sha256, actor, reason, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("claim_confirmation"),
                    claim.id,
                    event_id,
                    new_version,
                    command.basis.value,
                    claim.value.sha256,
                    command.actor,
                    command.reason,
                    occurred_at,
                ),
            )
            self._recompute_contradictions(claim.case_id, claim.field)
            self._connection.execute("COMMIT")
        except ClaimServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, ValueError) as error:
            self._rollback()
            raise ClaimPersistenceError("Claim confirmation was rolled back") from error
        return self.get_claim(command.claim_id)

    def correct_claim(self, command: CorrectClaimCommand) -> ClaimRecord:
        """Append a correction while retaining the exact prior value and reason."""

        try:
            claim = self._begin_mutation(command.claim_id, command.expected_version)
            if (
                claim.status not in CORRECTABLE_CLAIM_STATUSES
                or command.value.sha256 == claim.value.sha256
            ):
                raise ClaimTransitionNotAllowedError
            occurred_at = _utc_now()
            new_version = claim.version + 1
            self._update_claim(
                claim=claim,
                status=ClaimStatus.CORRECTED,
                value=command.value,
                new_version=new_version,
                updated_at=occurred_at,
            )
            self._insert_event(
                claim_id=claim.id,
                event_type=ClaimEventType.CORRECTED,
                prior_status=claim.status,
                new_status=ClaimStatus.CORRECTED,
                prior_value=claim.value,
                new_value=command.value,
                prior_version=claim.version,
                new_version=new_version,
                actor=command.actor,
                reason=command.reason,
                occurred_at=occurred_at,
            )
            self._recompute_contradictions(claim.case_id, claim.field)
            self._connection.execute("COMMIT")
        except ClaimServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, ValueError) as error:
            self._rollback()
            raise ClaimPersistenceError("Claim correction was rolled back") from error
        return self.get_claim(command.claim_id)

    def reject_claim(self, command: RejectClaimCommand) -> ClaimRecord:
        """Reject one Claim while retaining its value and immutable history."""

        try:
            claim = self._begin_mutation(command.claim_id, command.expected_version)
            if claim.status not in REJECTABLE_CLAIM_STATUSES:
                raise ClaimTransitionNotAllowedError
            occurred_at = _utc_now()
            new_version = claim.version + 1
            self._update_claim(
                claim=claim,
                status=ClaimStatus.REJECTED,
                value=claim.value,
                new_version=new_version,
                updated_at=occurred_at,
            )
            self._insert_event(
                claim_id=claim.id,
                event_type=ClaimEventType.REJECTED,
                prior_status=claim.status,
                new_status=ClaimStatus.REJECTED,
                prior_value=claim.value,
                new_value=claim.value,
                prior_version=claim.version,
                new_version=new_version,
                actor=command.actor,
                reason=command.reason,
                occurred_at=occurred_at,
            )
            self._recompute_contradictions(claim.case_id, claim.field)
            self._connection.execute("COMMIT")
        except ClaimServiceError:
            self._rollback()
            raise
        except (sqlite3.Error, ValueError) as error:
            self._rollback()
            raise ClaimPersistenceError("Claim rejection was rolled back") from error
        return self.get_claim(command.claim_id)

    def list_claim_events(self, claim_id: str) -> tuple[ClaimEvent, ...]:
        """Return immutable Claim history ordered by committed version."""

        try:
            clean_claim_id = validate_claim_id(claim_id)
        except ValueError as error:
            raise ClaimValidationError("Claim ID is invalid") from error
        try:
            self.get_claim(clean_claim_id)
            rows = self._connection.execute(
                """
                SELECT id, claim_id, event_type, prior_status, new_status,
                       prior_value_json, prior_value_sha256,
                       prior_comparison_sha256, new_value_json,
                       new_value_sha256, new_comparison_sha256,
                       prior_version, new_version, actor, reason, occurred_at
                FROM claim_event
                WHERE claim_id = ?
                ORDER BY new_version, id
                """,
                (clean_claim_id,),
            ).fetchall()
            return tuple(_claim_event(row) for row in rows)
        except ClaimServiceError:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise ClaimPersistenceError("Claim history could not be read") from error

    def list_claim_confirmations(self, claim_id: str) -> tuple[ClaimConfirmation, ...]:
        """Return evidence and explicit-user confirmations deterministically."""

        try:
            clean_claim_id = validate_claim_id(claim_id)
        except ValueError as error:
            raise ClaimValidationError("Claim ID is invalid") from error
        try:
            self.get_claim(clean_claim_id)
            rows = self._connection.execute(
                """
                SELECT id, claim_id, claim_version, basis, value_sha256,
                       actor, reason, confirmed_at
                FROM claim_confirmation
                WHERE claim_id = ?
                ORDER BY claim_version, id
                """,
                (clean_claim_id,),
            ).fetchall()
            return tuple(_claim_confirmation(row) for row in rows)
        except ClaimServiceError:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise ClaimPersistenceError("Claim confirmations could not be read") from error

    def _begin_mutation(self, claim_id: str, expected_version: int) -> ClaimRecord:
        self._connection.execute("BEGIN IMMEDIATE")
        claim = self.get_claim(claim_id)
        if claim.version != expected_version:
            raise ClaimVersionConflictError(claim.version)
        return claim

    def _insert_claim(self, command: CreateClaimCommand) -> str:
        claim_id = _new_id("claim")
        occurred_at = _utc_now()
        self._require_case(command.case_id)
        self._connection.execute(
            """
            INSERT INTO claim (
                id, case_id, field, value_json, value_sha256,
                comparison_sha256, status, confidence, created_by,
                contradiction, version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
            """,
            (
                claim_id,
                command.case_id,
                command.field,
                command.value.json_text,
                command.value.sha256,
                command.value.comparison_sha256,
                command.status.value,
                command.confidence,
                command.created_by.value,
                occurred_at,
                occurred_at,
            ),
        )
        for source in command.sources:
            self._insert_source(
                claim_id=claim_id,
                case_id=command.case_id,
                source=source,
                actor=command.actor,
                attached_at=occurred_at,
            )
        self._insert_event(
            claim_id=claim_id,
            event_type=ClaimEventType.CREATED,
            prior_status=None,
            new_status=command.status,
            prior_value=None,
            new_value=command.value,
            prior_version=0,
            new_version=1,
            actor=command.actor,
            reason=command.reason,
            occurred_at=occurred_at,
        )
        self._recompute_contradictions(command.case_id, command.field)
        return claim_id

    def _update_claim(
        self,
        *,
        claim: ClaimRecord,
        status: ClaimStatus,
        value: ClaimValue,
        new_version: int,
        updated_at: str,
    ) -> None:
        updated = self._connection.execute(
            """
            UPDATE claim
            SET value_json = ?, value_sha256 = ?, comparison_sha256 = ?,
                status = ?, version = ?, updated_at = ?
            WHERE id = ? AND version = ?
            """,
            (
                value.json_text,
                value.sha256,
                value.comparison_sha256,
                status.value,
                new_version,
                updated_at,
                claim.id,
                claim.version,
            ),
        )
        if updated.rowcount != 1:
            raise ClaimPersistenceError("Claim mutation update was not applied")

    def _require_case(self, case_id: str) -> None:
        exists = self._connection.execute(
            "SELECT 1 FROM case_record WHERE id = ?", (case_id,)
        ).fetchone()
        if exists is None:
            raise ClaimCaseNotFoundError

    def _insert_source(
        self,
        *,
        claim_id: str,
        case_id: str,
        source: ClaimSourceInput,
        actor: str,
        attached_at: str,
    ) -> None:
        self._validate_source_input(case_id, source)
        duplicate = self._connection.execute(
            """
            SELECT 1 FROM claim_source
            WHERE claim_id = ? AND segment_id = ? AND quote_start = ?
              AND quote_end = ? AND relation = ?
            """,
            (
                claim_id,
                source.segment_id,
                source.quote_start,
                source.quote_end,
                source.relation.value,
            ),
        ).fetchone()
        if duplicate is not None:
            raise ClaimSourceInvalidError
        self._connection.execute(
            """
            INSERT INTO claim_source (
                claim_id, source_id, segment_id, segment_text_sha256,
                quote_start, quote_end, quote_hash, relation,
                attached_by, attached_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim_id,
                source.source_id,
                source.segment_id,
                source.segment_text_sha256,
                source.quote_start,
                source.quote_end,
                source.quote_hash,
                source.relation.value,
                actor,
                attached_at,
            ),
        )

    def _validate_source_input(self, case_id: str, source: ClaimSourceInput) -> None:
        row = self._connection.execute(
            """
            SELECT s.case_id, ss.sequence, ss.page, ss.text, ss.text_sha256
            FROM source AS s
            JOIN source_segment AS ss ON ss.source_id = s.id
            WHERE s.id = ? AND ss.id = ?
            """,
            (source.source_id, source.segment_id),
        ).fetchone()
        if row is None or str(row["case_id"]) != case_id:
            raise ClaimSourceInvalidError
        text = str(row["text"])
        text_sha256 = _sha256_text(text)
        if (
            str(row["text_sha256"]) != text_sha256
            or source.segment_text_sha256 != text_sha256
            or source.quote_end > len(text)
            or _sha256_text(text[source.quote_start : source.quote_end]) != source.quote_hash
        ):
            raise ClaimSourceInvalidError

    def _load_claim_sources(self, claim: ClaimRecord) -> tuple[ClaimSource, ...]:
        expected_count = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM claim_source WHERE claim_id = ?", (claim.id,)
            ).fetchone()[0]
        )
        rows = self._connection.execute(
            """
            SELECT cs.claim_id, c.case_id, cs.source_id, cs.segment_id,
                   ss.sequence, ss.page, ss.text, ss.text_sha256,
                   cs.segment_text_sha256, cs.quote_start, cs.quote_end,
                   cs.quote_hash, cs.relation, cs.attached_by, cs.attached_at
            FROM claim_source AS cs
            JOIN claim AS c ON c.id = cs.claim_id
            JOIN source AS s ON s.id = cs.source_id AND s.case_id = c.case_id
            JOIN source_segment AS ss
              ON ss.id = cs.segment_id AND ss.source_id = cs.source_id
            WHERE cs.claim_id = ?
            ORDER BY cs.source_id, ss.sequence, cs.segment_id,
                     cs.quote_start, cs.quote_end, cs.relation
            """,
            (claim.id,),
        ).fetchall()
        if len(rows) != expected_count:
            raise ClaimSourceInvalidError
        sources: list[ClaimSource] = []
        for row in rows:
            text = str(row["text"])
            text_sha256 = _sha256_text(text)
            quote_start = int(row["quote_start"])
            quote_end = int(row["quote_end"])
            quote_hash = str(row["quote_hash"])
            if (
                str(row["case_id"]) != claim.case_id
                or str(row["text_sha256"]) != text_sha256
                or str(row["segment_text_sha256"]) != text_sha256
                or quote_start < 0
                or quote_end <= quote_start
                or quote_end > len(text)
                or _sha256_text(text[quote_start:quote_end]) != quote_hash
            ):
                raise ClaimSourceInvalidError
            sources.append(
                ClaimSource(
                    claim_id=str(row["claim_id"]),
                    case_id=str(row["case_id"]),
                    source_id=str(row["source_id"]),
                    segment_id=str(row["segment_id"]),
                    sequence=int(row["sequence"]),
                    page=int(row["page"]) if row["page"] is not None else None,
                    segment_text_sha256=text_sha256,
                    quote_start=quote_start,
                    quote_end=quote_end,
                    quote_hash=quote_hash,
                    relation=ClaimSourceRelation(str(row["relation"])),
                    attached_by=str(row["attached_by"]),
                    attached_at=str(row["attached_at"]),
                )
            )
        return tuple(sources)

    def _insert_event(
        self,
        *,
        claim_id: str,
        event_type: ClaimEventType,
        prior_status: ClaimStatus | None,
        new_status: ClaimStatus,
        prior_value: ClaimValue | None,
        new_value: ClaimValue,
        prior_version: int,
        new_version: int,
        actor: str,
        reason: str,
        occurred_at: str,
    ) -> str:
        event_id = _new_id("claim_event")
        self._connection.execute(
            """
            INSERT INTO claim_event (
                id, claim_id, event_type, prior_status, new_status,
                prior_value_json, prior_value_sha256, prior_comparison_sha256,
                new_value_json, new_value_sha256, new_comparison_sha256,
                prior_version, new_version, actor, reason, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                claim_id,
                event_type.value,
                prior_status.value if prior_status is not None else None,
                new_status.value,
                prior_value.json_text if prior_value is not None else None,
                prior_value.sha256 if prior_value is not None else None,
                prior_value.comparison_sha256 if prior_value is not None else None,
                new_value.json_text,
                new_value.sha256,
                new_value.comparison_sha256,
                prior_version,
                new_version,
                actor,
                reason,
                occurred_at,
            ),
        )
        return event_id

    def _recompute_contradictions(self, case_id: str, field: str) -> None:
        active_values = self._connection.execute(
            """
            SELECT DISTINCT comparison_sha256 FROM claim
            WHERE case_id = ? AND field = ? AND status IN ('candidate', 'inferred')
            """,
            (case_id, field),
        ).fetchall()
        contradiction = int(len(active_values) > 1)
        self._connection.execute(
            "UPDATE claim SET contradiction = 0 WHERE case_id = ? AND field = ?",
            (case_id, field),
        )
        if contradiction:
            self._connection.execute(
                """
                UPDATE claim SET contradiction = 1
                WHERE case_id = ? AND field = ?
                  AND status IN ('candidate', 'inferred')
                """,
                (case_id, field),
            )

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")


def _claim_record(row: sqlite3.Row) -> ClaimRecord:
    confidence = row["confidence"]
    return ClaimRecord(
        id=str(row["id"]),
        case_id=str(row["case_id"]),
        field=str(row["field"]),
        value=ClaimValue.from_storage(
            str(row["value_json"]),
            str(row["value_sha256"]),
            str(row["comparison_sha256"]),
        ),
        status=ClaimStatus(str(row["status"])),
        confidence=float(confidence) if confidence is not None else None,
        created_by=ClaimCreatedBy(str(row["created_by"])),
        contradiction=bool(row["contradiction"]),
        version=int(row["version"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _claim_event(row: sqlite3.Row) -> ClaimEvent:
    prior_value = None
    if row["prior_value_json"] is not None:
        prior_value = ClaimValue.from_storage(
            str(row["prior_value_json"]),
            str(row["prior_value_sha256"]),
            str(row["prior_comparison_sha256"]),
        )
    return ClaimEvent(
        id=str(row["id"]),
        claim_id=str(row["claim_id"]),
        event_type=ClaimEventType(str(row["event_type"])),
        prior_status=(
            ClaimStatus(str(row["prior_status"])) if row["prior_status"] is not None else None
        ),
        new_status=ClaimStatus(str(row["new_status"])),
        prior_value=prior_value,
        new_value=ClaimValue.from_storage(
            str(row["new_value_json"]),
            str(row["new_value_sha256"]),
            str(row["new_comparison_sha256"]),
        ),
        prior_version=int(row["prior_version"]),
        new_version=int(row["new_version"]),
        actor=str(row["actor"]),
        reason=str(row["reason"]),
        occurred_at=str(row["occurred_at"]),
    )


def _claim_confirmation(row: sqlite3.Row) -> ClaimConfirmation:
    return ClaimConfirmation(
        id=str(row["id"]),
        claim_id=str(row["claim_id"]),
        claim_version=int(row["claim_version"]),
        basis=ClaimConfirmationBasis(str(row["basis"])),
        value_sha256=str(row["value_sha256"]),
        actor=str(row["actor"]),
        reason=str(row["reason"]),
        confirmed_at=str(row["confirmed_at"]),
    )


def _new_id(prefix: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"{prefix}_{timestamp}_{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
