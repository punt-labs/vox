"""Tests for bounded album manifest reads."""

from __future__ import annotations

from typing import TYPE_CHECKING

from punt_vox.voxd.programs.album_manifest_file import (
    _MAX_MANIFEST_BYTES,
    AlbumManifestFile,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_manifest_at_the_ceiling_is_read_in_full(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"x" * _MAX_MANIFEST_BYTES)
    assert len(AlbumManifestFile(manifest).read_text()) == _MAX_MANIFEST_BYTES
