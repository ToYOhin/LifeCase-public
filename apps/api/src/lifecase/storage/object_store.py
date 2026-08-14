"""Content-addressed storage for immutable user-selected source files."""

import hashlib
import mimetypes
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO


class ObjectStoreError(RuntimeError):
    """Base class for content-addressed object storage failures."""


class InvalidSourcePathError(ObjectStoreError):
    """Raised when a selected source is missing or is not a regular file."""


class SourceOutsideAllowedRootError(ObjectStoreError):
    """Raised when a selected source resolves outside configured import roots."""


class SourceSizeLimitError(ObjectStoreError):
    """Raised when a source exceeds the configured byte limit."""


class UnsupportedMediaTypeError(ObjectStoreError):
    """Raised when the filename-derived media type is not allowed."""


class SourceChangedError(ObjectStoreError):
    """Raised when source bytes or metadata change while they are copied."""


class ObjectIntegrityError(ObjectStoreError):
    """Raised when an existing hash path does not contain the expected bytes."""


class ObjectWriteError(ObjectStoreError):
    """Raised when a validated object cannot be written or published."""


@dataclass(frozen=True, slots=True)
class ObjectStoreConfig:
    """Validated limits and roots for one local object store."""

    data_root: Path
    allowed_source_roots: tuple[Path, ...]
    max_file_size_bytes: int
    allowed_media_types: frozenset[str]
    chunk_size_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if self.max_file_size_bytes < 0:
            raise ValueError("max_file_size_bytes must be non-negative")
        if self.chunk_size_bytes <= 0:
            raise ValueError("chunk_size_bytes must be positive")
        if not self.allowed_source_roots:
            raise ValueError("At least one allowed source root is required")
        if not self.allowed_media_types:
            raise ValueError("At least one allowed media type is required")
        if any(media_type != media_type.lower() for media_type in self.allowed_media_types):
            raise ValueError("Allowed media types must be lowercase")


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Result of validating and storing one immutable source object."""

    source_path: Path
    original_name: str
    media_type: str
    sha256: str
    size_bytes: int
    relative_path: str
    absolute_path: Path
    created_new_object: bool


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    size_bytes: int
    modified_ns: int
    device: int
    inode: int


class ContentAddressedObjectStore:
    """Validate selected sources and publish immutable hash-addressed objects."""

    def __init__(self, config: ObjectStoreConfig) -> None:
        self._config = config
        self._data_root = config.data_root.expanduser().resolve()
        self._data_root.mkdir(parents=True, exist_ok=True)
        self._allowed_roots = self._resolve_allowed_roots(config.allowed_source_roots)

    def store(self, selected_path: Path) -> StoredObject:
        """Store one explicitly selected source without modifying the original."""

        source_path = self._resolve_source(selected_path)
        media_type = self._detect_media_type(source_path)
        identity = _file_identity(source_path)
        if identity.size_bytes > self._config.max_file_size_bytes:
            raise SourceSizeLimitError(
                f"Source is {identity.size_bytes} bytes; limit is "
                f"{self._config.max_file_size_bytes} bytes"
            )

        sha256, size_bytes = _hash_file(
            source_path,
            chunk_size_bytes=self._config.chunk_size_bytes,
            max_size_bytes=self._config.max_file_size_bytes,
        )
        _require_unchanged(source_path, identity)

        relative_path = PurePosixPath("objects", sha256[:2], sha256[2:4], sha256).as_posix()
        destination = self._data_root.joinpath(*PurePosixPath(relative_path).parts)
        created_new_object = self._publish(
            source_path=source_path,
            destination=destination,
            expected_sha256=sha256,
            expected_size=size_bytes,
            source_identity=identity,
        )

        return StoredObject(
            source_path=source_path,
            original_name=source_path.name,
            media_type=media_type,
            sha256=sha256,
            size_bytes=size_bytes,
            relative_path=relative_path,
            absolute_path=destination,
            created_new_object=created_new_object,
        )

    def discard_new_object(self, stored: StoredObject) -> None:
        """Remove an object created by this import if metadata cannot be committed."""

        if not stored.created_new_object:
            return
        expected = self._data_root.joinpath(*PurePosixPath(stored.relative_path).parts)
        try:
            resolved_parent = expected.parent.resolve(strict=True)
        except OSError as error:
            raise ObjectIntegrityError("Object parent is unavailable during cleanup") from error
        if (
            expected != stored.absolute_path
            or not expected.is_relative_to(self._data_root)
            or not resolved_parent.is_relative_to(self._data_root)
        ):
            raise ObjectIntegrityError("Refusing to discard an object outside the data root")
        expected.unlink(missing_ok=True)

    def _resolve_allowed_roots(self, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        resolved: list[Path] = []
        for root in roots:
            try:
                candidate = root.expanduser().resolve(strict=True)
            except OSError as error:
                raise InvalidSourcePathError("An allowed source root is unavailable") from error
            if not candidate.is_dir():
                raise InvalidSourcePathError("An allowed source root is not a directory")
            resolved.append(candidate)
        return tuple(resolved)

    def _resolve_source(self, selected_path: Path) -> Path:
        if not selected_path.is_absolute():
            raise InvalidSourcePathError("An explicitly selected source path must be absolute")
        if selected_path.is_symlink():
            raise InvalidSourcePathError("A selected source cannot be a symbolic link")

        try:
            normalized_path = selected_path.expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise InvalidSourcePathError("Selected source path cannot be resolved") from error

        if not any(normalized_path.is_relative_to(root) for root in self._allowed_roots):
            raise SourceOutsideAllowedRootError(
                "Selected source resolves outside configured source roots"
            )
        if normalized_path.is_relative_to(self._data_root):
            raise SourceOutsideAllowedRootError("Object-store files cannot be re-imported")

        try:
            source_path = normalized_path.resolve(strict=True)
            source_stat = source_path.stat()
        except (OSError, RuntimeError) as error:
            raise InvalidSourcePathError("Selected source is unavailable") from error

        if not stat.S_ISREG(source_stat.st_mode):
            raise InvalidSourcePathError("Selected source must be a regular file")
        if not any(source_path.is_relative_to(root) for root in self._allowed_roots):
            raise SourceOutsideAllowedRootError(
                "Selected source resolves outside configured source roots"
            )
        if source_path.is_relative_to(self._data_root):
            raise SourceOutsideAllowedRootError("Object-store files cannot be re-imported")
        return source_path

    def _detect_media_type(self, source_path: Path) -> str:
        media_type, encoding = mimetypes.guess_type(source_path.name, strict=True)
        if encoding is not None:
            raise UnsupportedMediaTypeError(f"Encoded source files are not supported: {encoding}")
        detected = (media_type or "application/octet-stream").lower()
        if detected not in self._config.allowed_media_types:
            raise UnsupportedMediaTypeError(f"Media type is not allowed: {detected}")
        return detected

    def _publish(
        self,
        *,
        source_path: Path,
        destination: Path,
        expected_sha256: str,
        expected_size: int,
        source_identity: _FileIdentity,
    ) -> bool:
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = destination.parent.resolve(strict=True)
        if not resolved_parent.is_relative_to(self._data_root):
            raise ObjectWriteError("Generated object destination escaped the data root")

        if os.path.lexists(destination):
            _verify_existing_object(destination, expected_sha256, expected_size, self._config)
            return False

        descriptor = -1
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".lifecase-import-",
                suffix=".tmp",
                dir=destination.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                copied_sha256, copied_size = _copy_source(
                    source_path,
                    output,
                    chunk_size_bytes=self._config.chunk_size_bytes,
                    max_size_bytes=self._config.max_file_size_bytes,
                )
                output.flush()
                os.fsync(output.fileno())

            _require_unchanged(source_path, source_identity)
            if copied_sha256 != expected_sha256 or copied_size != expected_size:
                raise SourceChangedError("Source bytes changed between validation and copy")

            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                _verify_existing_object(destination, expected_sha256, expected_size, self._config)
                return False
            return True
        except ObjectStoreError:
            raise
        except OSError as error:
            raise ObjectWriteError("Failed to write or publish the source object") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _file_identity(path: Path) -> _FileIdentity:
    file_stat = path.stat()
    return _FileIdentity(
        size_bytes=file_stat.st_size,
        modified_ns=file_stat.st_mtime_ns,
        device=file_stat.st_dev,
        inode=file_stat.st_ino,
    )


def _require_unchanged(path: Path, expected: _FileIdentity) -> None:
    try:
        actual = _file_identity(path)
    except OSError as error:
        raise SourceChangedError("Source became unavailable during import") from error
    if actual != expected:
        raise SourceChangedError("Source metadata changed during import")


def _hash_file(path: Path, *, chunk_size_bytes: int, max_size_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(chunk_size_bytes):
                size_bytes += len(chunk)
                if size_bytes > max_size_bytes:
                    raise SourceSizeLimitError(
                        f"Source exceeded the {max_size_bytes}-byte limit while reading"
                    )
                digest.update(chunk)
    except ObjectStoreError:
        raise
    except OSError as error:
        raise InvalidSourcePathError("Selected source could not be read") from error
    return digest.hexdigest(), size_bytes


def _copy_source(
    source_path: Path,
    output: BinaryIO,
    *,
    chunk_size_bytes: int,
    max_size_bytes: int,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with source_path.open("rb") as source:
        while chunk := source.read(chunk_size_bytes):
            size_bytes += len(chunk)
            if size_bytes > max_size_bytes:
                raise SourceSizeLimitError(
                    f"Source exceeded the {max_size_bytes}-byte limit while copying"
                )
            output.write(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size_bytes


def _verify_existing_object(
    path: Path,
    expected_sha256: str,
    expected_size: int,
    config: ObjectStoreConfig,
) -> None:
    if path.is_symlink() or not path.is_file():
        raise ObjectIntegrityError("Existing object path is not a regular file")
    try:
        actual_sha256, actual_size = _hash_file(
            path,
            chunk_size_bytes=config.chunk_size_bytes,
            max_size_bytes=max(config.max_file_size_bytes, expected_size),
        )
    except ObjectStoreError as error:
        raise ObjectIntegrityError("Existing object could not be verified") from error
    if actual_sha256 != expected_sha256 or actual_size != expected_size:
        raise ObjectIntegrityError("Existing object does not match its content-addressed path")
