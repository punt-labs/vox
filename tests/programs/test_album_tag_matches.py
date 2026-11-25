"""Tests for tag selection's live album reads and fault boundaries."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from punt_vox.voxd.programs.album_tag_matches import AlbumTagMatches
from punt_vox.voxd.programs.album_tags import TagQuery
from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore

from .conftest import seed_album

if TYPE_CHECKING:
    from pathlib import Path


class TestAlbumTagMatches:
    def test_select_rejects_a_manifest_replaced_by_a_directory(
        self, tmp_path: Path
    ) -> None:
        locator = seed_album(tmp_path, 1)
        store = FilesystemProgramStore(tmp_path)
        matches = AlbumTagMatches(store.scan(), TagQuery(style="techno"))
        path = tmp_path / locator / "manifest.json"
        path.unlink()
        path.mkdir()

        with pytest.raises(OSError, match="manifest is not a regular file"):
            matches.select()

    def test_select_skips_an_album_lost_during_open(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        locator = seed_album(tmp_path, 1)
        store = FilesystemProgramStore(tmp_path)
        matches = AlbumTagMatches(store.scan(), TagQuery(style="techno"))

        with patch(
            "punt_vox.voxd.programs.album_manifest_file.os",
            O_RDONLY=os.O_RDONLY,
            O_NOFOLLOW=os.O_NOFOLLOW,
        ) as manifest_os:
            manifest_os.open.side_effect = FileNotFoundError("manifest disappeared")
            assert not matches.select()

        manifest_os.open.assert_called_once_with(
            tmp_path / locator / "manifest.json", os.O_RDONLY | os.O_NOFOLLOW
        )
        assert (
            "punt_vox.voxd.programs.album_tag_matches",
            logging.WARNING,
            f"skipping missing album a3f1c9: no saved album at directory {locator!r}",
        ) in caplog.record_tuples

    @pytest.mark.parametrize("fault", [PermissionError, KeyError])
    def test_select_does_not_treat_other_faults_as_absence(
        self, tmp_path: Path, fault: type[Exception]
    ) -> None:
        seed_album(tmp_path, 1)
        store = FilesystemProgramStore(tmp_path)
        matches = AlbumTagMatches(store.scan(), TagQuery(style="techno"))

        with (
            patch.object(FilesystemProgramStore, "open", side_effect=fault("read")),
            pytest.raises(fault, match="read"),
        ):
            matches.select()
