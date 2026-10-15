"""Save warranty intent and explicitly propose it through existing Claim authority."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from lifecase.cases.models import CaseStatus
from lifecase.cases.service import CaseNotFoundError, CaseService, CaseServiceError
from lifecase.claims.models import (
    ClaimCommandValidationError,
    ClaimCreatedBy,
    ClaimRecord,
    ClaimStatus,
    ClaimValue,
    CreateClaimCommand,
)
from lifecase.claims.service import ClaimService, ClaimServiceError


class PreparationIntentError(RuntimeError):
    """Safe domain failure for a preparation intent."""


class PreparationIntentValidationError(PreparationIntentError, ValueError):
    """The supplied text or version cannot be saved."""


class PreparationIntentCaseNotFoundError(PreparationIntentError):
    """The requested Case does not exist."""


class PreparationIntentUnsupportedError(PreparationIntentError):
    """Preparation intent currently belongs only to warranty Cases."""


class PreparationIntentPersistenceError(PreparationIntentError):
    """Stored intent cannot be safely read or written."""


class DesiredOutcomeConflictError(PreparationIntentError):
    """A non-rejected desired outcome must be reviewed through its existing Claim."""


class PreparationIntentVersionConflictError(PreparationIntentError):
    """A different save already replaced the expected version."""

    def __init__(self, current_version: int) -> None:
        super().__init__("Preparation intent version has changed")
        self.current_version = current_version


@dataclass(frozen=True, slots=True)
class PreparationIntent:
    case_id: str
    intent: str
    version: int
    updated_at: str | None


def validate_preparation_intent(intent: str) -> str:
    """Keep text verbatim while rejecting values SQLite cannot reliably retain."""

    if not isinstance(intent, str) or len(intent) > 2000 or "\x00" in intent:
        raise PreparationIntentValidationError("Preparation intent is invalid")
    try:
        intent.encode("utf-8")
    except UnicodeError as error:
        raise PreparationIntentValidationError("Preparation intent is invalid") from error
    return intent


class PreparationIntentService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, case_id: str) -> PreparationIntent:
        """Read the Case and saved text in one snapshot without creating a row."""

        try:
            self._connection.execute("BEGIN")
            current = self._read(case_id)
            self._connection.execute("COMMIT")
            return current
        except PreparationIntentError:
            self._rollback()
            raise
        except (sqlite3.Error, ValueError, UnicodeError) as error:
            self._rollback()
            raise PreparationIntentPersistenceError("Preparation intent is unavailable") from error

    def save(self, case_id: str, *, intent: str, expected_version: int) -> PreparationIntent:
        validate_preparation_intent(intent)
        if type(expected_version) is not int or expected_version < 0:
            raise PreparationIntentValidationError("Expected preparation version is invalid")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._read(case_id)
            if current.version != expected_version:
                raise PreparationIntentVersionConflictError(current.version)
            saved = PreparationIntent(
                case_id=case_id,
                intent=intent,
                version=current.version + 1,
                updated_at=datetime.now(UTC)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
            )
            if current.version == 0:
                self._connection.execute(
                    """
                    INSERT INTO warranty_preparation_intent (case_id, intent, version, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (saved.case_id, saved.intent, saved.version, saved.updated_at),
                )
            else:
                # Keep a cleared row's version so stale version-0 pages cannot resurrect it.
                updated = self._connection.execute(
                    """
                    UPDATE warranty_preparation_intent SET intent = ?, version = ?, updated_at = ?
                    WHERE case_id = ? AND version = ?
                    """,
                    (saved.intent, saved.version, saved.updated_at, saved.case_id, current.version),
                )
                if updated.rowcount != 1:
                    raise PreparationIntentPersistenceError("Preparation intent was not saved")
            self._connection.execute("COMMIT")
            return saved
        except PreparationIntentError:
            self._rollback()
            raise
        except (sqlite3.Error, ValueError, UnicodeError, OverflowError) as error:
            self._rollback()
            raise PreparationIntentPersistenceError("Preparation intent was not saved") from error

    def create_desired_outcome_candidate(
        self,
        case_id: str,
        *,
        expected_intent_version: int,
        actor: str,
        reason: str,
    ) -> ClaimRecord:
        """Propose the saved text atomically, without confirming or replacing a Claim."""

        if type(expected_intent_version) is not int or expected_intent_version <= 0:
            raise PreparationIntentValidationError("Expected preparation version is invalid")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._read(case_id)
            case = CaseService(self._connection).get_case(current.case_id)
            if case.status in {CaseStatus.RESOLVED, CaseStatus.ABANDONED, CaseStatus.ARCHIVED}:
                raise PreparationIntentValidationError("A terminal Case cannot register an outcome")
            if current.version == 0:
                raise PreparationIntentValidationError("A saved preparation intent is required")
            if current.version != expected_intent_version:
                raise PreparationIntentVersionConflictError(current.version)
            if not current.intent.strip():
                raise PreparationIntentValidationError("A non-empty preparation intent is required")
            existing = self._connection.execute(
                """
                SELECT 1 FROM claim
                WHERE case_id = ? AND field = 'desired_outcome' AND status != 'rejected'
                LIMIT 1
                """,
                (current.case_id,),
            ).fetchone()
            if existing is not None:
                raise DesiredOutcomeConflictError("An existing desired outcome requires review")
            command = CreateClaimCommand(
                case_id=current.case_id,
                field="desired_outcome",
                value=ClaimValue.from_input(current.intent),
                status=ClaimStatus.CANDIDATE,
                confidence=None,
                created_by=ClaimCreatedBy.USER,
                actor=actor,
                reason=reason,
            )
            claims = ClaimService(self._connection)
            (claim_id,) = claims.create_claims_in_transaction((command,))
            record = claims.get_claim(claim_id)
            self._connection.execute("COMMIT")
            return record
        except (PreparationIntentError, ClaimServiceError, ClaimCommandValidationError):
            self._rollback()
            raise
        except (sqlite3.Error, CaseServiceError, ValueError, UnicodeError, OverflowError) as error:
            self._rollback()
            raise PreparationIntentPersistenceError("Desired outcome was not registered") from error

    def _read(self, case_id: str) -> PreparationIntent:
        try:
            case = CaseService(self._connection).get_case(case_id)
        except CaseNotFoundError as error:
            raise PreparationIntentCaseNotFoundError("Case was not found") from error
        except CaseServiceError as error:
            raise PreparationIntentPersistenceError("Case is unavailable") from error
        if case.type != "warranty":
            raise PreparationIntentUnsupportedError("Preparation intent requires a warranty Case")
        row = self._connection.execute(
            "SELECT intent, version, updated_at FROM warranty_preparation_intent WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        if row is None:
            return PreparationIntent(case_id, "", 0, None)
        try:
            intent = validate_preparation_intent(row["intent"])
            version, updated_at = row["version"], row["updated_at"]
            if type(version) is not int or version <= 0:
                raise ValueError
            if not isinstance(updated_at, str) or not updated_at.endswith("Z"):
                raise ValueError
            timestamp = datetime.fromisoformat(updated_at)
            if timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z") != updated_at:
                raise ValueError
        except ValueError as error:
            raise PreparationIntentPersistenceError(
                "Stored preparation intent is invalid"
            ) from error
        return PreparationIntent(case_id, intent, version, updated_at)

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")
