"""Immutable M6-01 Evidence Pack values."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

EVIDENCE_PACK_FORMAT_VERSION = 1


class EvidencePackLayer(StrEnum):
    ORIGINAL = "original"
    DERIVED = "derived"
    GENERATED = "generated"
    CONFIRMED = "confirmed"


@dataclass(frozen=True, slots=True)
class EvidencePackPolicy:
    max_members: int = 1024
    max_member_bytes: int = 25 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    chunk_size_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if (
            self.max_members <= 0
            or self.max_member_bytes <= 0
            or self.max_total_bytes <= 0
            or self.chunk_size_bytes <= 0
            or self.max_member_bytes > self.max_total_bytes
        ):
            raise ValueError("Evidence Pack policy is invalid")


@dataclass(frozen=True, slots=True)
class EvidencePackArtifact:
    id: str
    case_id: str
    filename: str
    sha256: str
    size_bytes: int
    format_version: int
    created_at: str


@dataclass(frozen=True, slots=True)
class EvidencePackDownload:
    artifact: EvidencePackArtifact
    path: Path
