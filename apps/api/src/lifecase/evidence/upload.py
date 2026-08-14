"""Bounded staging for one browser-selected multipart upload."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO

from lifecase.storage.object_store import SourceSizeLimitError


class UploadStagingError(RuntimeError):
    """Raised when a selected upload cannot be safely staged."""


@dataclass(frozen=True, slots=True)
class StagedUpload:
    """Server-owned temporary copy with a safe display name."""

    path: Path
    display_name: str

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)


def stage_upload(
    source: BinaryIO,
    *,
    submitted_name: str | None,
    upload_root: Path,
    max_size_bytes: int,
    chunk_size_bytes: int = 1024 * 1024,
) -> StagedUpload:
    """Stream one explicit upload to a random server-owned temporary path."""

    if max_size_bytes <= 0 or chunk_size_bytes <= 0:
        raise ValueError("Upload bounds must be positive")
    display_name = _safe_display_name(submitted_name)
    suffix = Path(display_name).suffix.lower()
    if len(suffix) > 20:
        raise UploadStagingError("Upload filename extension is invalid")

    root = upload_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    descriptor = -1
    staged_path: Path | None = None
    completed = False
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="lifecase-upload-",
            suffix=suffix,
            dir=root,
        )
        staged_path = Path(temporary_name)
        size = 0
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            while chunk := source.read(chunk_size_bytes):
                size += len(chunk)
                if size > max_size_bytes:
                    raise SourceSizeLimitError(
                        f"Upload exceeds the configured {max_size_bytes}-byte limit"
                    )
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        completed = True
        return StagedUpload(path=staged_path, display_name=display_name)
    except (SourceSizeLimitError, UploadStagingError):
        raise
    except (OSError, ValueError) as error:
        raise UploadStagingError("Selected upload could not be staged") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if staged_path is not None and not completed:
            staged_path.unlink(missing_ok=True)


def _safe_display_name(value: str | None) -> str:
    if value is None or "\x00" in value:
        raise UploadStagingError("Upload filename is missing or invalid")
    name = PureWindowsPath(PurePosixPath(value).name).name.strip()
    if not name or name in {".", ".."} or len(name) > 255:
        raise UploadStagingError("Upload filename is missing or invalid")
    return name
