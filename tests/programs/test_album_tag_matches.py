"""Tests for tag selection's live album reads and fault boundaries."""

from __future__ import annotations

import logging
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
    def test_select_skips_an_album_lost_during_open(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        seed_album(tmp_path, 1)
        store = FilesystemProgramStore(tmp_path)
        matches = AlbumTagMatches(store.scan(), TagQuery(style="techno"))

        with patch.object(
            FilesystemProgramStore,
            "open",
            side_effect=FileNotFoundError("manifest disappeared"),
        ):
            assert not matches.select()

        assert (
            "punt_vox.voxd.programs.album_tag_matches",
            logging.WARNING,
            "skipping missing album a3f1c9: manifest disappeared",
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
