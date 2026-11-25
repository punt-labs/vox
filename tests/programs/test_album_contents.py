"""Tests for the ``music get`` value objects: ``PartFile`` and ``AlbumContents``.

Each ``PartFile`` measures itself from disk after bare-name-validating its
identity within the album directory, and ``AlbumContents.from_album`` assembles
the get manifest from a catalog album. The hostile-identity cases prove a
corrupt manifest entry can never stat a file outside the album directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from punt_vox.voxd.containment import ContainmentRoot
from punt_vox.voxd.programs.album_contents import AlbumContents, PartFile
from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.part import Part

from .conftest import seed_album

_LABEL = "part name"
# Non-empty identities (``Part`` rejects the empty string) that must be refused
# as bare in-root names before any ``stat`` touches the filesystem.
_HOSTILE_IDENTITIES = ["../../../etc/passwd", "a/b.mp3", "..", "n\x00.mp3"]


class TestPartFileMeasured:
    """``PartFile.measured`` sizes a contained part and refuses an escaping one."""

    def test_measures_a_contained_part(self, tmp_path: Path) -> None:
        (tmp_path / "001.mp3").write_bytes(b"audio")
        root = ContainmentRoot(tmp_path, _LABEL)
        part_file = PartFile.measured(root, Part("001.mp3", 1))
        assert part_file.name == "001.mp3"
        assert part_file.byte_count == 5

    @pytest.mark.parametrize("identity", _HOSTILE_IDENTITIES)
    def test_rejects_hostile_identity(self, tmp_path: Path, identity: str) -> None:
        root = ContainmentRoot(tmp_path, _LABEL)
        with pytest.raises(ValueError):
            PartFile.measured(root, Part(identity, 1))

    def test_rejects_symlink_part_without_following(self, tmp_path: Path) -> None:
        """A part that is a symlink is refused -- its target's size never leaks."""
        secret = tmp_path / "secret.bin"
        secret.write_bytes(b"x" * 4096)
        (tmp_path / "001.mp3").symlink_to(secret)
        root = ContainmentRoot(tmp_path, _LABEL)
        with pytest.raises(ValueError, match="not a regular file"):
            PartFile.measured(root, Part("001.mp3", 1))


class TestAlbumContentsFromAlbum:
    """``AlbumContents.from_album`` builds the get manifest from a catalog album."""

    def test_builds_from_a_seeded_album(self, tmp_path: Path) -> None:
        root = tmp_path / "programs"
        locator = seed_album(root, 1, 2, name="pads", album_id="a3f1c9")
        album = FilesystemProgramStore(root).scan()[0]

        contents = AlbumContents.from_album(
            album, ContainmentRoot(root / album.locator, _LABEL)
        )

        assert contents.name == locator
        assert [part.name for part in contents.parts] == ["001.mp3", "002.mp3"]
        assert all(part.byte_count == 5 for part in contents.parts)

    def test_skips_a_deleted_part_and_names_it_missing(self, tmp_path: Path) -> None:
        """A ready part deleted out of band is reported missing; survivors export."""
        root = tmp_path / "programs"
        seed_album(root, 1, 2, name="pads", album_id="a3f1c9")
        album = FilesystemProgramStore(root).scan()[0]
        (root / album.locator / "002.mp3").unlink()

        contents = AlbumContents.from_album(
            album, ContainmentRoot(root / album.locator, _LABEL)
        )

        assert [part.name for part in contents.parts] == ["001.mp3"]
        assert contents.missing == ("002.mp3",)

    def test_every_part_deleted_leaves_no_parts_and_names_them_all(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "programs"
        seed_album(root, 1, 2, name="pads", album_id="a3f1c9")
        album = FilesystemProgramStore(root).scan()[0]
        for name in ("001.mp3", "002.mp3"):
            (root / album.locator / name).unlink()

        contents = AlbumContents.from_album(
            album, ContainmentRoot(root / album.locator, _LABEL)
        )

        assert contents.parts == ()
        assert contents.missing == ("001.mp3", "002.mp3")

    def test_a_read_fault_other_than_absence_still_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only a vanished file is skipped: a permission fault is not masked."""
        root = tmp_path / "programs"
        seed_album(root, 1, 2, name="pads", album_id="a3f1c9")
        album = FilesystemProgramStore(root).scan()[0]
        denied = root / album.locator / "002.mp3"
        real_lstat = Path.lstat

        def lstat(path: Path) -> os.stat_result:
            if path == denied:
                raise PermissionError(13, "Permission denied", str(path))
            return real_lstat(path)

        monkeypatch.setattr(Path, "lstat", lstat)

        with pytest.raises(PermissionError):
            AlbumContents.from_album(
                album, ContainmentRoot(root / album.locator, _LABEL)
            )

    def test_wire_frame_lists_parts_and_missing(self) -> None:
        contents = AlbumContents(
            "pads-a3f1c9", (PartFile("001.mp3", 5),), missing=("002.mp3",)
        )

        assert contents.wire() == {
            "type": "manifest",
            "album": "pads-a3f1c9",
            "parts": [{"part": "001.mp3", "bytes": 5}],
            "missing": [{"part": "002.mp3"}],
        }
