"""Unit tests for bounded multipart staging cleanup."""

from io import BytesIO
from pathlib import Path

import pytest

from lifecase.evidence.upload import UploadStagingError, stage_upload
from lifecase.storage.object_store import SourceSizeLimitError


def test_stages_safe_basename_and_discards_temporary_copy(tmp_path: Path) -> None:
    original = b"synthetic upload bytes"
    staged = stage_upload(
        BytesIO(original),
        submitted_name=r"C:\fakepath\..\receipt.txt",
        upload_root=tmp_path,
        max_size_bytes=1024,
        chunk_size_bytes=4,
    )

    assert staged.display_name == "receipt.txt"
    assert staged.path.parent == tmp_path
    assert staged.path.read_bytes() == original

    staged.discard()
    assert list(tmp_path.iterdir()) == []


def test_oversized_or_invalid_upload_leaves_no_partial_file(tmp_path: Path) -> None:
    with pytest.raises(SourceSizeLimitError):
        stage_upload(
            BytesIO(b"12345"),
            submitted_name="large.txt",
            upload_root=tmp_path,
            max_size_bytes=4,
            chunk_size_bytes=2,
        )
    with pytest.raises(UploadStagingError):
        stage_upload(
            BytesIO(b"data"),
            submitted_name=None,
            upload_root=tmp_path,
            max_size_bytes=4,
        )

    assert list(tmp_path.iterdir()) == []
