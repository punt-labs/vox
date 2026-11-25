"""Tests for the daemon's off-loop export manifest reader."""

from __future__ import annotations

from threading import get_ident
from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.containment import ContainmentRoot
from punt_vox.voxd.programs.album_export_reader import AlbumExportReader
from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.store_errors import AlbumNotFoundError

from .conftest import seed_album

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.programs.album_id import AlbumId
    from punt_vox.voxd.programs.catalog import Album


async def test_catalogued_album_with_missing_manifest_is_still_a_fault(
    tmp_path: Path,
) -> None:
    locator = seed_album(tmp_path, 1)
    album = FilesystemProgramStore(tmp_path).scan()[0]
    album_dir = tmp_path / locator
    (album_dir / "manifest.json").unlink()
    loop_thread = get_ident()

    def require_album(album_id: AlbumId) -> Album:
        assert get_ident() == loop_thread
        assert album_id == album.id
        return album

    with pytest.raises(AlbumNotFoundError, match="no saved album"):
        await AlbumExportReader.read(
            album, ContainmentRoot(album_dir, "part name"), require_album
        )


async def test_rejects_an_album_replaced_under_the_same_id(tmp_path: Path) -> None:
    locator = seed_album(tmp_path, 1)
    store = FilesystemProgramStore(tmp_path)
    album = store.scan()[0]
    replacement = store.scan()[0]
    assert album.id == replacement.id
    assert album is not replacement

    def require_album(album_id: AlbumId) -> Album:
        assert album_id == replacement.id
        return replacement

    with pytest.raises(ValueError, match="no album named"):
        await AlbumExportReader.read(
            album, ContainmentRoot(tmp_path / locator, "part name"), require_album
        )
