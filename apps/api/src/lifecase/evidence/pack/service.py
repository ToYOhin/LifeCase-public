"""Deterministic, Case-scoped, verifiable Evidence Pack export."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import cast
from uuid import uuid4

from lifecase import __version__
from lifecase.actions import ActionRecord, ActionService, ActionServiceError
from lifecase.approvals import ApprovalRecord, ApprovalService, ApprovalServiceError
from lifecase.cases import CaseNotFoundError, CaseRecord, CaseService, CaseServiceError
from lifecase.claims import ClaimRecord, ClaimService, ClaimServiceError, ClaimStatus
from lifecase.deadlines import DeadlineRecord, DeadlineService, DeadlineServiceError, DeadlineStatus
from lifecase.timeline import TimelineAuthorityType, TimelineError, TimelineResult, TimelineService

from .errors import (
    EvidencePackArtifactNotFoundError,
    EvidencePackCaseNotFoundError,
    EvidencePackError,
    EvidencePackIntegrityError,
    EvidencePackLimitError,
    EvidencePackPersistenceError,
    EvidencePackValidationError,
)
from .models import (
    EVIDENCE_PACK_FORMAT_VERSION,
    EvidencePackArtifact,
    EvidencePackDownload,
    EvidencePackLayer,
    EvidencePackPolicy,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EXPORT_ARTIFACT_TYPES = frozenset({"backup", "manifest", "pack"})
_DERIVED_ARTIFACT_TYPES = frozenset({"derived_text", "thumbnail"})
_GENERATED_ARTIFACT_TYPES = frozenset({"calendar", "draft"})
_ALL_ARTIFACT_TYPES = frozenset(
    {"source", *_EXPORT_ARTIFACT_TYPES, *_DERIVED_ARTIFACT_TYPES, *_GENERATED_ARTIFACT_TYPES}
)
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_DEFAULT_POLICY = EvidencePackPolicy()


@dataclass(frozen=True, slots=True)
class _Member:
    path: str
    layer: EvidencePackLayer
    media_type: str
    authority_type: str
    authority_id: str
    authority_version: int | None
    sha256: str
    size_bytes: int
    content: bytes | None = None
    source_path: Path | None = None

    def manifest_value(self) -> dict[str, object]:
        return {
            "authority": {
                "id": self.authority_id,
                "sha256": self.sha256,
                "type": self.authority_type,
                "version": self.authority_version,
            },
            "layer": self.layer.value,
            "media_type": self.media_type,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


class EvidencePackService:
    """Build and retain one deterministic archive as existing Artifact authority."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        data_root: Path,
        policy: EvidencePackPolicy = _DEFAULT_POLICY,
    ) -> None:
        self._connection = connection
        self._data_root = data_root.expanduser().resolve()
        self._policy = policy

    def create(self, case_id: str) -> EvidencePackArtifact:
        clean_case_id = _identifier(case_id, "Case ID is invalid")
        if self._connection.in_transaction:
            raise EvidencePackPersistenceError("Evidence Pack requires an idle connection")

        temporary_path: Path | None = None
        destination: Path | None = None
        created_destination = False
        try:
            export_root = self._export_root()
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".lifecase-evidence-pack-",
                suffix=".tmp",
                dir=export_root,
            )
            os.close(descriptor)
            temporary_path = Path(temporary_name)

            self._connection.execute("BEGIN IMMEDIATE")
            case = CaseService(self._connection).get_case(clean_case_id)
            members = self._collect_members(case)
            self._write_archive(temporary_path, case, members)
            verify_evidence_pack(temporary_path, self._policy)
            pack_sha256, pack_size = _hash_file(
                temporary_path,
                self._policy.chunk_size_bytes,
                self._policy.max_total_bytes,
            )
            relative_path = PurePosixPath(
                "exports", "evidence-packs", pack_sha256[:2], f"{pack_sha256}.zip"
            ).as_posix()
            destination = self._data_root.joinpath(*PurePosixPath(relative_path).parts)
            existing = self._existing_pack(clean_case_id, pack_sha256)
            if existing is not None:
                _verify_data_file(
                    self._data_root,
                    str(existing["relative_path"]),
                    pack_sha256,
                    pack_size,
                    self._policy,
                )
                self._connection.execute("COMMIT")
                return _artifact_record(existing)

            created_destination = _publish_archive(
                temporary_path,
                destination,
                self._data_root,
                pack_sha256,
                pack_size,
                self._policy,
            )
            artifact_id = _new_id("art")
            created_at = _utc_now()
            self._connection.execute(
                """
                INSERT INTO artifact (
                    id, case_id, source_id, type, relative_path,
                    sha256, size_bytes, created_by_run, created_at
                ) VALUES (?, ?, NULL, 'pack', ?, ?, ?, NULL, ?)
                """,
                (
                    artifact_id,
                    clean_case_id,
                    relative_path,
                    pack_sha256,
                    pack_size,
                    created_at,
                ),
            )
            self._connection.execute("COMMIT")
            return EvidencePackArtifact(
                id=artifact_id,
                case_id=clean_case_id,
                filename=_filename(pack_sha256),
                sha256=pack_sha256,
                size_bytes=pack_size,
                format_version=EVIDENCE_PACK_FORMAT_VERSION,
                created_at=created_at,
            )
        except CaseNotFoundError as error:
            _rollback(self._connection)
            raise EvidencePackCaseNotFoundError from error
        except EvidencePackError:
            _rollback(self._connection)
            raise
        except (
            ActionServiceError,
            ApprovalServiceError,
            CaseServiceError,
            ClaimServiceError,
            DeadlineServiceError,
            TimelineError,
            ValueError,
        ) as error:
            _rollback(self._connection)
            raise EvidencePackIntegrityError("Evidence Pack authority is invalid") from error
        except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
            _rollback(self._connection)
            raise EvidencePackPersistenceError("Evidence Pack could not be created") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            if created_destination and destination is not None and self._connection.in_transaction:
                _rollback(self._connection)
            if created_destination and destination is not None:
                self._discard_unreferenced(destination)

    def get_download(self, artifact_id: str) -> EvidencePackDownload:
        clean_artifact_id = _identifier(artifact_id, "Artifact ID is invalid")
        try:
            row = self._connection.execute(
                """
                SELECT id, case_id, relative_path, sha256, size_bytes, created_at
                FROM artifact WHERE id = ? AND type = 'pack'
                """,
                (clean_artifact_id,),
            ).fetchone()
            if row is None:
                raise EvidencePackArtifactNotFoundError
            path = _verify_data_file(
                self._data_root,
                str(row["relative_path"]),
                str(row["sha256"]),
                int(row["size_bytes"]),
                self._policy,
            )
            verify_evidence_pack(path, self._policy)
            return EvidencePackDownload(_artifact_record(row), path)
        except EvidencePackError:
            raise
        except (OSError, sqlite3.Error, ValueError, zipfile.BadZipFile) as error:
            raise EvidencePackPersistenceError("Evidence Pack could not be read") from error

    def get_latest(self, case_id: str) -> EvidencePackArtifact:
        """Return and verify the newest retained pack for one Case."""

        clean_case_id = _identifier(case_id, "Case ID is invalid")
        try:
            CaseService(self._connection).get_case(clean_case_id)
            row = self._connection.execute(
                """
                SELECT id, case_id, relative_path, sha256, size_bytes, created_at
                FROM artifact
                WHERE case_id = ? AND type = 'pack'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (clean_case_id,),
            ).fetchone()
            if row is None:
                raise EvidencePackArtifactNotFoundError
            return self.get_download(str(row["id"])).artifact
        except CaseNotFoundError as error:
            raise EvidencePackCaseNotFoundError from error
        except EvidencePackError:
            raise
        except (OSError, sqlite3.Error, ValueError) as error:
            raise EvidencePackPersistenceError("Evidence Pack could not be read") from error

    def _collect_members(self, case: CaseRecord) -> tuple[_Member, ...]:
        timeline = TimelineService(self._connection).list_case_timeline_in_transaction(case.id)
        sources = self._source_members(case.id)
        artifact_members, artifact_types = self._artifact_members(case.id)
        claims = ClaimService(self._connection).list_case_claims(case.id)
        deadlines = DeadlineService(self._connection).list_case_deadlines_in_transaction(case.id)
        actions = ActionService(self._connection).list_case_actions_in_transaction(case.id)
        approvals = ApprovalService(self._connection).list_case_approvals_in_transaction(case.id)

        members = [*sources, *artifact_members]
        members.extend(
            self._projection_members(
                case, claims, deadlines, actions, approvals, timeline, artifact_types
            )
        )
        ordered = tuple(sorted(members, key=lambda item: item.path))
        _validate_members(ordered, self._policy)
        return ordered

    def _source_members(self, case_id: str) -> list[_Member]:
        rows = self._connection.execute(
            """
            SELECT id, original_name, media_type, sha256, size_bytes, object_path
            FROM source WHERE case_id = ? ORDER BY imported_at, id
            """,
            (case_id,),
        ).fetchall()
        result: list[_Member] = []
        for row in rows:
            source_id = _identifier(str(row["id"]), "Source authority is invalid")
            sha256 = _hash(str(row["sha256"]), "Source authority is invalid")
            size_bytes = _size(row["size_bytes"], self._policy, "Source authority is invalid")
            path = _verify_data_file(
                self._data_root,
                str(row["object_path"]),
                sha256,
                size_bytes,
                self._policy,
            )
            result.append(
                _Member(
                    path=f"original/{source_id}/content.bin",
                    layer=EvidencePackLayer.ORIGINAL,
                    media_type=str(row["media_type"]),
                    authority_type="source",
                    authority_id=source_id,
                    authority_version=None,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    source_path=path,
                )
            )
        return result

    def _artifact_members(self, case_id: str) -> tuple[list[_Member], dict[str, str]]:
        rows = self._connection.execute(
            """
            SELECT id, type, relative_path, sha256, size_bytes
            FROM artifact WHERE case_id = ? ORDER BY created_at, id
            """,
            (case_id,),
        ).fetchall()
        result: list[_Member] = []
        types: dict[str, str] = {}
        for row in rows:
            artifact_id = _identifier(str(row["id"]), "Artifact authority is invalid")
            artifact_type = str(row["type"])
            if artifact_type not in _ALL_ARTIFACT_TYPES:
                raise EvidencePackIntegrityError("Artifact authority is invalid")
            types[artifact_id] = artifact_type
            if artifact_type == "source" or artifact_type in _EXPORT_ARTIFACT_TYPES:
                continue
            layer = (
                EvidencePackLayer.DERIVED
                if artifact_type in _DERIVED_ARTIFACT_TYPES
                else EvidencePackLayer.GENERATED
            )
            sha256 = _hash(str(row["sha256"]), "Artifact authority is invalid")
            size_bytes = _size(row["size_bytes"], self._policy, "Artifact authority is invalid")
            path = _verify_data_file(
                self._data_root,
                str(row["relative_path"]),
                sha256,
                size_bytes,
                self._policy,
            )
            result.append(
                _Member(
                    path=f"{layer.value}/artifacts/{artifact_id}/content.bin",
                    layer=layer,
                    media_type="application/octet-stream",
                    authority_type="artifact",
                    authority_id=artifact_id,
                    authority_version=None,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    source_path=path,
                )
            )
        return result, types

    def _projection_members(
        self,
        case: CaseRecord,
        claims: tuple[ClaimRecord, ...],
        deadlines: tuple[DeadlineRecord, ...],
        actions: tuple[ActionRecord, ...],
        approvals: tuple[ApprovalRecord, ...],
        timeline: TimelineResult,
        artifact_types: dict[str, str],
    ) -> list[_Member]:
        claim_values = [self._claim_value(item) for item in claims]
        deadline_values = [_deadline_value(item) for item in deadlines]
        action_values = [_action_value(item) for item in actions]
        approval_values = [_approval_value(item) for item in approvals]
        members = [
            _bytes_member(
                "derived/source_segments.json",
                EvidencePackLayer.DERIVED,
                "case_projection",
                case.id,
                None,
                self._segments_value(case.id),
            ),
            _bytes_member(
                "derived/claims.json",
                EvidencePackLayer.DERIVED,
                "case_projection",
                case.id,
                None,
                _json_bytes(claim_values),
            ),
            _bytes_member(
                "generated/deadlines.json",
                EvidencePackLayer.GENERATED,
                "case_projection",
                case.id,
                None,
                _json_bytes(deadline_values),
            ),
            _bytes_member(
                "generated/actions.json",
                EvidencePackLayer.GENERATED,
                "case_projection",
                case.id,
                None,
                _json_bytes(action_values),
            ),
            _bytes_member(
                "confirmed/case.json",
                EvidencePackLayer.CONFIRMED,
                "case",
                case.id,
                case.version,
                _json_bytes(_case_value(case)),
            ),
            _bytes_member(
                "confirmed/claims.json",
                EvidencePackLayer.CONFIRMED,
                "case_projection",
                case.id,
                None,
                _json_bytes(
                    [
                        value
                        for item, value in zip(claims, claim_values, strict=True)
                        if item.status in {ClaimStatus.CONFIRMED, ClaimStatus.CORRECTED}
                    ]
                ),
            ),
            _bytes_member(
                "confirmed/deadlines.json",
                EvidencePackLayer.CONFIRMED,
                "case_projection",
                case.id,
                None,
                _json_bytes(
                    [
                        value
                        for item, value in zip(deadlines, deadline_values, strict=True)
                        if item.status is not DeadlineStatus.PROPOSED
                    ]
                ),
            ),
            _bytes_member(
                "confirmed/approvals.json",
                EvidencePackLayer.CONFIRMED,
                "case_projection",
                case.id,
                None,
                _json_bytes(approval_values),
            ),
            _bytes_member(
                "timeline.md",
                EvidencePackLayer.CONFIRMED,
                "timeline",
                case.id,
                None,
                _timeline_bytes(timeline, artifact_types),
                media_type="text/markdown; charset=utf-8",
            ),
        ]
        for action in actions:
            if action.communication_draft is None:
                continue
            members.append(
                _bytes_member(
                    f"generated/communications/{action.id}.md",
                    EvidencePackLayer.GENERATED,
                    "action",
                    action.id,
                    action.version,
                    (action.communication_draft.rstrip() + "\n").encode("utf-8"),
                    media_type="text/markdown; charset=utf-8",
                )
            )
        return members

    def _segments_value(self, case_id: str) -> bytes:
        rows = self._connection.execute(
            """
            SELECT ss.id, ss.source_id, ss.sequence, ss.page, ss.text,
                   ss.extraction_method, ss.confidence, ss.text_sha256,
                   ss.extractor_version
            FROM source_segment AS ss
            JOIN source AS s ON s.id = ss.source_id
            WHERE s.case_id = ?
            ORDER BY ss.source_id, ss.sequence, ss.id
            """,
            (case_id,),
        ).fetchall()
        values: list[dict[str, object]] = []
        for row in rows:
            text = str(row["text"])
            sha256 = _hash(str(row["text_sha256"]), "Source Segment authority is invalid")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != sha256:
                raise EvidencePackIntegrityError("Source Segment authority is invalid")
            values.append(
                {
                    "confidence": row["confidence"],
                    "extraction_method": str(row["extraction_method"]),
                    "extractor_version": str(row["extractor_version"]),
                    "id": str(row["id"]),
                    "page": row["page"],
                    "sequence": int(row["sequence"]),
                    "source_id": str(row["source_id"]),
                    "text": text,
                    "text_sha256": sha256,
                }
            )
        return _json_bytes(values)

    def _claim_value(self, claim: ClaimRecord) -> dict[str, object]:
        service = ClaimService(self._connection)
        return {
            "citations": [
                {
                    "attached_at": item.attached_at,
                    "attached_by": item.attached_by,
                    "page": item.page,
                    "quote_end": item.quote_end,
                    "quote_hash": item.quote_hash,
                    "quote_start": item.quote_start,
                    "relation": item.relation.value,
                    "segment_id": item.segment_id,
                    "segment_text_sha256": item.segment_text_sha256,
                    "sequence": item.sequence,
                    "source_id": item.source_id,
                }
                for item in service.list_claim_sources(claim.id)
            ],
            "confidence": claim.confidence,
            "confirmations": [
                {
                    "actor": item.actor,
                    "basis": item.basis.value,
                    "claim_version": item.claim_version,
                    "confirmed_at": item.confirmed_at,
                    "id": item.id,
                    "reason": item.reason,
                    "value_sha256": item.value_sha256,
                }
                for item in service.list_claim_confirmations(claim.id)
            ],
            "contradiction": claim.contradiction,
            "created_at": claim.created_at,
            "created_by": claim.created_by.value,
            "field": claim.field,
            "history": [
                {
                    "actor": item.actor,
                    "event_type": item.event_type.value,
                    "id": item.id,
                    "new_status": item.new_status.value,
                    "new_value": item.new_value.to_python(),
                    "new_value_sha256": item.new_value.sha256,
                    "new_version": item.new_version,
                    "occurred_at": item.occurred_at,
                    "prior_status": item.prior_status.value if item.prior_status else None,
                    "prior_value": item.prior_value.to_python() if item.prior_value else None,
                    "prior_value_sha256": item.prior_value.sha256 if item.prior_value else None,
                    "prior_version": item.prior_version,
                    "reason": item.reason,
                }
                for item in service.list_claim_events(claim.id)
            ],
            "id": claim.id,
            "status": claim.status.value,
            "updated_at": claim.updated_at,
            "value": claim.value.to_python(),
            "value_sha256": claim.value.sha256,
            "version": claim.version,
        }

    def _write_archive(self, path: Path, case: CaseRecord, members: tuple[_Member, ...]) -> None:
        manifest = {
            "archive_format": "zip-stored",
            "case_id": case.id,
            "case_version": case.version,
            "checksum_algorithm": "sha256",
            "excluded_recursive_artifact_types": sorted(_EXPORT_ARTIFACT_TYPES),
            "format_version": EVIDENCE_PACK_FORMAT_VERSION,
            "layers": [item.value for item in EvidencePackLayer],
            "members": [item.manifest_value() for item in members],
            "minimum_application_version": __version__,
        }
        manifest_member = _bytes_member(
            "manifest.json",
            EvidencePackLayer.CONFIRMED,
            "case",
            case.id,
            case.version,
            _json_bytes(manifest),
        )
        checksum_members = (manifest_member, *members)
        checksums = "".join(f"{item.sha256}  {item.path}\n" for item in checksum_members).encode(
            "utf-8"
        )
        with zipfile.ZipFile(
            path, "w", compression=zipfile.ZIP_STORED, allowZip64=False
        ) as archive:
            _write_member(archive, manifest_member, self._policy)
            for member in members:
                _write_member(archive, member, self._policy)
            _write_raw_entry(archive, "checksums.sha256", checksums)

    def _existing_pack(self, case_id: str, sha256: str) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._connection.execute(
                """
            SELECT id, case_id, relative_path, sha256, size_bytes, created_at
            FROM artifact
            WHERE case_id = ? AND type = 'pack' AND sha256 = ?
            ORDER BY created_at, id LIMIT 1
            """,
                (case_id, sha256),
            ).fetchone(),
        )

    def _export_root(self) -> Path:
        try:
            self._data_root.mkdir(parents=True, exist_ok=True)
            root = self._data_root / "exports" / "evidence-packs"
            root.mkdir(parents=True, exist_ok=True)
            resolved = root.resolve(strict=True)
        except OSError as error:
            raise EvidencePackPersistenceError("Evidence Pack storage is unavailable") from error
        if not resolved.is_relative_to(self._data_root):
            raise EvidencePackIntegrityError("Evidence Pack storage is invalid")
        return resolved

    def _discard_unreferenced(self, destination: Path) -> None:
        try:
            row = self._connection.execute(
                "SELECT 1 FROM artifact WHERE relative_path = ? LIMIT 1",
                (destination.relative_to(self._data_root).as_posix(),),
            ).fetchone()
            if row is None:
                destination.unlink(missing_ok=True)
        except (OSError, sqlite3.Error, ValueError):
            return


def verify_evidence_pack(path: Path, policy: EvidencePackPolicy = _DEFAULT_POLICY) -> None:
    """Verify portable paths, manifest membership, sizes, and every listed SHA-256."""

    try:
        with zipfile.ZipFile(path, "r", allowZip64=False) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            _validate_archive_names(names, policy)
            if any(item.file_size > policy.max_member_bytes for item in infos):
                raise EvidencePackLimitError("Evidence Pack member limit was exceeded")
            if sum(item.file_size for item in infos) > policy.max_total_bytes:
                raise EvidencePackLimitError("Evidence Pack total limit was exceeded")
            if "manifest.json" not in names or "checksums.sha256" not in names:
                raise EvidencePackIntegrityError("Evidence Pack control files are missing")
            checksum_text = archive.read("checksums.sha256").decode("utf-8")
            expected = _parse_checksums(checksum_text)
            if set(expected) != set(names) - {"checksums.sha256"}:
                raise EvidencePackIntegrityError("Evidence Pack checksum membership is invalid")
            for info in infos:
                if info.filename == "checksums.sha256":
                    continue
                if info.file_size > policy.max_member_bytes:
                    raise EvidencePackLimitError("Evidence Pack member limit was exceeded")
                digest = hashlib.sha256()
                size_bytes = 0
                with archive.open(info, "r") as member:
                    while chunk := member.read(policy.chunk_size_bytes):
                        size_bytes += len(chunk)
                        digest.update(chunk)
                if size_bytes != info.file_size or digest.hexdigest() != expected[info.filename]:
                    raise EvidencePackIntegrityError("Evidence Pack member checksum is invalid")
            manifest = json.loads(archive.read("manifest.json"))
            _validate_manifest(
                manifest,
                expected,
                {item.filename: item.file_size for item in infos},
            )
    except EvidencePackError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        raise EvidencePackIntegrityError("Evidence Pack archive is invalid") from error


def _case_value(case: CaseRecord) -> dict[str, object]:
    return {
        "closed_at": case.closed_at,
        "created_at": case.created_at,
        "goal": case.goal,
        "id": case.id,
        "priority": case.priority,
        "status": case.status.value,
        "template_id": case.template_id,
        "template_version": case.template_version,
        "title": case.title,
        "type": case.type,
        "updated_at": case.updated_at,
        "version": case.version,
    }


def _deadline_value(item: DeadlineRecord) -> dict[str, object]:
    return {
        "bases": [
            {
                "claim_id": basis.claim_id,
                "claim_version": basis.claim_version,
                "date_kind": basis.date_kind.value,
                "field": basis.field,
                "value_sha256": basis.value_sha256,
            }
            for basis in item.bases
        ],
        "confidence": item.confidence,
        "created_at": item.created_at,
        "date_kind": item.due.kind.value,
        "definition_sha256": item.definition_sha256,
        "due_value": item.due.value,
        "id": item.id,
        "kind": item.kind.value,
        "rule_id": item.rule_id,
        "rule_parameters": item.rule_parameters.to_dict() if item.rule_parameters else None,
        "rule_parameters_sha256": item.rule_parameters.sha256 if item.rule_parameters else None,
        "rule_version": item.rule_version,
        "status": item.status.value,
        "timezone": item.due.timezone,
        "uncertainty": item.uncertainty.value,
        "updated_at": item.updated_at,
        "version": item.version,
    }


def _action_value(item: ActionRecord) -> dict[str, object]:
    return {
        "blockers": [
            {
                "authority_id": blocker.authority_id,
                "code": blocker.code.value,
                "field": blocker.field,
            }
            for blocker in item.blockers
        ],
        "blockers_sha256": item.blockers_sha256,
        "communication_draft_sha256": (
            hashlib.sha256(item.communication_draft.encode("utf-8")).hexdigest()
            if item.communication_draft is not None
            else None
        ),
        "completion_evidence": [
            {
                "authority_id": evidence.authority_id,
                "authority_sha256": evidence.authority_sha256,
                "authority_type": evidence.authority_type.value,
                "authority_version": evidence.authority_version,
            }
            for evidence in item.completion_evidence
        ],
        "created_at": item.created_at,
        "deadline": (
            {
                "date_kind": item.deadline.date_kind,
                "deadline_id": item.deadline.deadline_id,
                "deadline_version": item.deadline.deadline_version,
                "definition_sha256": item.deadline.definition_sha256,
                "due_value": item.deadline.due_value,
                "status": item.deadline.status,
                "timezone": item.deadline.timezone,
            }
            if item.deadline
            else None
        ),
        "dependencies": [
            {
                "action_id": dependency.action_id,
                "action_version": dependency.action_version,
                "status": dependency.status.value,
            }
            for dependency in item.dependencies
        ],
        "id": item.id,
        "kind": item.kind.value,
        "payload": item.payload.to_dict(),
        "payload_sha256": item.payload.sha256,
        "requirements": [
            {
                "claim_id": requirement.claim_id,
                "claim_version": requirement.claim_version,
                "field": requirement.field,
                "value_sha256": requirement.value_sha256,
            }
            for requirement in item.requirements
        ],
        "risk_level": item.risk_level.value,
        "status": item.status.value,
        "updated_at": item.updated_at,
        "version": item.version,
    }


def _approval_value(item: ApprovalRecord) -> dict[str, object]:
    return {
        "action_id": item.action_id,
        "action_version": item.action_version,
        "created_at": item.created_at,
        "id": item.id,
        "payload_sha256": item.payload_sha256,
        "requested_by": item.requested_by,
        "requested_reason": item.requested_reason,
        "status": item.status.value,
        "summary": item.summary.text,
        "summary_sha256": item.summary.sha256,
        "updated_at": item.updated_at,
        "version": item.version,
    }


def _timeline_bytes(timeline: TimelineResult, artifact_types: dict[str, str]) -> bytes:
    lines = ["# Case Timeline", "", f"Case: `{timeline.case_id}`", ""]
    index = 0
    for event in timeline.events:
        if (
            event.authority_type is TimelineAuthorityType.ARTIFACT
            and artifact_types.get(event.authority_id) in _EXPORT_ARTIFACT_TYPES
        ):
            continue
        index += 1
        transition = (
            f"{event.prior_status} -> {event.status}"
            if event.prior_status is not None
            else event.status
        )
        lines.extend(
            [
                f"## {index:03d} {event.kind.value}",
                "",
                f"- occurred_at: `{event.occurred_at}`",
                f"- authority: `{event.authority_type.value}:{event.authority_id}`",
                f"- subject: `{event.subject_id}`",
                f"- parent: `{event.parent_id}`" if event.parent_id else "- parent: none",
                f"- status: `{transition}`" if transition else "- status: none",
                f"- version: `{event.version}`" if event.version is not None else "- version: none",
                f"- actor: `{event.actor}`" if event.actor else f"- origin: `{event.origin.value}`",
                f"- label: {event.label}" if event.label else "- label: none",
                f"- reason: {event.reason}" if event.reason else "- reason: none",
                "",
            ]
        )
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _bytes_member(
    path: str,
    layer: EvidencePackLayer,
    authority_type: str,
    authority_id: str,
    authority_version: int | None,
    content: bytes,
    *,
    media_type: str = "application/json; charset=utf-8",
) -> _Member:
    return _Member(
        path=path,
        layer=layer,
        media_type=media_type,
        authority_type=authority_type,
        authority_id=authority_id,
        authority_version=authority_version,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        content=content,
    )


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, separators=(",", ": "))
        + "\n"
    ).encode("utf-8")


def _write_member(archive: zipfile.ZipFile, member: _Member, policy: EvidencePackPolicy) -> None:
    info = _zip_info(member.path)
    if member.content is not None:
        _write_raw_entry(archive, member.path, member.content)
        return
    if member.source_path is None:
        raise EvidencePackIntegrityError("Evidence Pack member has no content")
    digest = hashlib.sha256()
    size_bytes = 0
    with (
        member.source_path.open("rb") as source,
        archive.open(info, "w", force_zip64=False) as output,
    ):
        while chunk := source.read(policy.chunk_size_bytes):
            size_bytes += len(chunk)
            if size_bytes > policy.max_member_bytes:
                raise EvidencePackLimitError("Evidence Pack member limit was exceeded")
            digest.update(chunk)
            output.write(chunk)
    if size_bytes != member.size_bytes or digest.hexdigest() != member.sha256:
        raise EvidencePackIntegrityError("Evidence Pack member changed during export")


def _write_raw_entry(archive: zipfile.ZipFile, path: str, content: bytes) -> None:
    archive.writestr(_zip_info(path), content)


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, _ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def _validate_members(members: tuple[_Member, ...], policy: EvidencePackPolicy) -> None:
    if len(members) + 2 > policy.max_members:
        raise EvidencePackLimitError("Evidence Pack member limit was exceeded")
    total = 0
    lowered: set[str] = set()
    for member in members:
        _portable_path(member.path)
        folded = member.path.casefold()
        if folded in lowered:
            raise EvidencePackIntegrityError("Evidence Pack member path is duplicated")
        lowered.add(folded)
        if member.size_bytes > policy.max_member_bytes:
            raise EvidencePackLimitError("Evidence Pack member limit was exceeded")
        total += member.size_bytes
        if total > policy.max_total_bytes:
            raise EvidencePackLimitError("Evidence Pack total limit was exceeded")


def _validate_archive_names(names: list[str], policy: EvidencePackPolicy) -> None:
    if len(names) > policy.max_members or len(names) != len(set(names)):
        raise EvidencePackIntegrityError("Evidence Pack archive membership is invalid")
    lowered: set[str] = set()
    for name in names:
        _portable_path(name)
        folded = name.casefold()
        if folded in lowered:
            raise EvidencePackIntegrityError("Evidence Pack archive path is duplicated")
        lowered.add(folded)


def _validate_manifest(
    value: object,
    checksums: dict[str, str],
    archive_sizes: dict[str, int],
) -> None:
    if not isinstance(value, dict) or set(value) != {
        "archive_format",
        "case_id",
        "case_version",
        "checksum_algorithm",
        "excluded_recursive_artifact_types",
        "format_version",
        "layers",
        "members",
        "minimum_application_version",
    }:
        raise EvidencePackIntegrityError("Evidence Pack manifest is invalid")
    if (
        value["format_version"] != EVIDENCE_PACK_FORMAT_VERSION
        or value["archive_format"] != "zip-stored"
        or value["checksum_algorithm"] != "sha256"
        or value["layers"] != [item.value for item in EvidencePackLayer]
        or value["excluded_recursive_artifact_types"] != sorted(_EXPORT_ARTIFACT_TYPES)
        or not isinstance(value["members"], list)
    ):
        raise EvidencePackIntegrityError("Evidence Pack manifest is invalid")
    member_paths: set[str] = set()
    for member in value["members"]:
        if not isinstance(member, dict) or set(member) != {
            "authority",
            "layer",
            "media_type",
            "path",
            "sha256",
            "size_bytes",
        }:
            raise EvidencePackIntegrityError("Evidence Pack manifest member is invalid")
        path = member["path"]
        if (
            not isinstance(path, str)
            or path not in checksums
            or checksums[path] != member["sha256"]
        ):
            raise EvidencePackIntegrityError("Evidence Pack manifest member is invalid")
        if (
            path in member_paths
            or path not in archive_sizes
            or member["size_bytes"] != archive_sizes[path]
        ):
            raise EvidencePackIntegrityError("Evidence Pack manifest member is invalid")
        member_paths.add(path)
    if member_paths != set(archive_sizes) - {"manifest.json", "checksums.sha256"}:
        raise EvidencePackIntegrityError("Evidence Pack manifest membership is invalid")


def _parse_checksums(value: str) -> dict[str, str]:
    if not value.endswith("\n"):
        raise EvidencePackIntegrityError("Evidence Pack checksums are invalid")
    result: dict[str, str] = {}
    for line in value.splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise EvidencePackIntegrityError("Evidence Pack checksums are invalid")
        sha256 = _hash(line[:64], "Evidence Pack checksums are invalid")
        path = line[66:]
        _portable_path(path)
        if path in result:
            raise EvidencePackIntegrityError("Evidence Pack checksums are invalid")
        result[path] = sha256
    return result


def _verify_data_file(
    data_root: Path,
    relative_path: str,
    expected_sha256: str,
    expected_size: int,
    policy: EvidencePackPolicy,
) -> Path:
    relative = _portable_path(relative_path)
    candidate = data_root.joinpath(*relative.parts)
    try:
        resolved_root = data_root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        file_stat = resolved.stat()
    except OSError as error:
        raise EvidencePackIntegrityError("Evidence Pack member authority is unavailable") from error
    if (
        candidate.is_symlink()
        or not resolved.is_relative_to(resolved_root)
        or not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_size != expected_size
    ):
        raise EvidencePackIntegrityError("Evidence Pack member authority is invalid")
    actual_sha256, actual_size = _hash_file(
        resolved,
        policy.chunk_size_bytes,
        max(policy.max_member_bytes, expected_size),
    )
    if actual_sha256 != expected_sha256 or actual_size != expected_size:
        raise EvidencePackIntegrityError("Evidence Pack member authority is invalid")
    return resolved


def _publish_archive(
    temporary: Path,
    destination: Path,
    data_root: Path,
    sha256: str,
    size_bytes: int,
    policy: EvidencePackPolicy,
) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.parent.resolve(strict=True).is_relative_to(data_root):
        raise EvidencePackIntegrityError("Evidence Pack destination is invalid")
    if os.path.lexists(destination):
        _verify_data_file(
            data_root,
            destination.relative_to(data_root).as_posix(),
            sha256,
            size_bytes,
            policy,
        )
        return False
    try:
        os.link(temporary, destination)
        return True
    except FileExistsError:
        _verify_data_file(
            data_root,
            destination.relative_to(data_root).as_posix(),
            sha256,
            size_bytes,
            policy,
        )
        return False


def _hash_file(path: Path, chunk_size: int, max_size: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            size_bytes += len(chunk)
            if size_bytes > max_size:
                raise EvidencePackLimitError("Evidence Pack file limit was exceeded")
            digest.update(chunk)
    return digest.hexdigest(), size_bytes


def _portable_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or len(value) > 500 or "\\" in value:
        raise EvidencePackIntegrityError("Evidence Pack path is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise EvidencePackIntegrityError("Evidence Pack path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise EvidencePackIntegrityError("Evidence Pack path is invalid")
    if ":" in path.parts[0] or value.startswith("//"):
        raise EvidencePackIntegrityError("Evidence Pack path is invalid")
    return path


def _artifact_record(row: sqlite3.Row) -> EvidencePackArtifact:
    sha256 = _hash(str(row["sha256"]), "Evidence Pack Artifact is invalid")
    return EvidencePackArtifact(
        id=_identifier(str(row["id"]), "Evidence Pack Artifact is invalid"),
        case_id=_identifier(str(row["case_id"]), "Evidence Pack Artifact is invalid"),
        filename=_filename(sha256),
        sha256=sha256,
        size_bytes=int(row["size_bytes"]),
        format_version=EVIDENCE_PACK_FORMAT_VERSION,
        created_at=str(row["created_at"]),
    )


def _size(value: object, policy: EvidencePackPolicy, message: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= policy.max_member_bytes
    ):
        raise EvidencePackLimitError(message)
    return value


def _identifier(value: object, message: str) -> str:
    if not isinstance(value, str):
        raise EvidencePackValidationError(message)
    clean = value.strip()
    if not 1 <= len(clean) <= 200 or any(ord(character) < 32 for character in clean):
        raise EvidencePackValidationError(message)
    return clean


def _hash(value: object, message: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise EvidencePackIntegrityError(message)
    return value


def _filename(sha256: str) -> str:
    return f"lifecase-evidence-pack-{sha256[:16]}.zip"


def _new_id(prefix: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"{prefix}_{timestamp}_{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
    except sqlite3.Error:
        return
