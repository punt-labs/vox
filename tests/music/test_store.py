"""Tests for FilesystemTrackStore and the in-memory FakeTrackStore."""

from __future__ import annotations

from pathlib import Path

from music.conftest import FakeTrackStore
from punt_vox.voxd.music.store import FilesystemTrackStore, TrackStore

__all__: list[str] = []


class TestProtocolConformance:
    """Both implementations satisfy the TrackStore protocol structurally."""

    def test_filesystem_store_is_a_track_store(self, tmp_path: Path) -> None:
        assert isinstance(FilesystemTrackStore(tmp_path), TrackStore)

    def test_fake_store_is_a_track_store(self) -> None:
        assert isinstance(FakeTrackStore(), TrackStore)


class TestFilesystemTrackStore:
    """FilesystemTrackStore performs the glob/stat/dir work for the domain."""

    def test_tracks_for_matches_prefix_sorted(self, tmp_path: Path) -> None:
        store = FilesystemTrackStore(tmp_path)
        (tmp_path / "calm_jazz_b.mp3").write_bytes(b"x")
        (tmp_path / "calm_jazz_a.mp3").write_bytes(b"x")
        (tmp_path / "happy_techno_a.mp3").write_bytes(b"x")

        assert store.tracks_for("calm_jazz_") == (
            tmp_path / "calm_jazz_a.mp3",
            tmp_path / "calm_jazz_b.mp3",
        )

    def test_tracks_for_missing_dir_is_empty(self, tmp_path: Path) -> None:
        store = FilesystemTrackStore(tmp_path / "missing")
        assert store.tracks_for("calm_jazz_") == ()

    def test_exists(self, tmp_path: Path) -> None:
        store = FilesystemTrackStore(tmp_path)
        (tmp_path / "here.mp3").write_bytes(b"x")

        assert store.exists("here")
        assert not store.exists("gone")

    def test_path_for_builds_mp3_path(self, tmp_path: Path) -> None:
        store = FilesystemTrackStore(tmp_path)
        assert store.path_for("track") == tmp_path / "track.mp3"

    def test_prepare_creates_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "tracks"
        store = FilesystemTrackStore(target)
        store.prepare()
        assert target.is_dir()

    def test_listing_returns_metadata_sorted(self, tmp_path: Path) -> None:
        store = FilesystemTrackStore(tmp_path)
        (tmp_path / "beta.mp3").write_bytes(b"two-bytes-ok")
        (tmp_path / "alpha.mp3").write_bytes(b"a")
        (tmp_path / "notes.txt").write_bytes(b"ignored")

        listing = store.listing()

        assert [m.path.stem for m in listing] == ["alpha", "beta"]
        assert all(m.size_bytes > 0 for m in listing)
        assert all(m.modified > 0 for m in listing)

    def test_listing_empty_dir(self, tmp_path: Path) -> None:
        assert FilesystemTrackStore(tmp_path).listing() == ()

    def test_listing_missing_dir(self, tmp_path: Path) -> None:
        assert FilesystemTrackStore(tmp_path / "nonexistent").listing() == ()

    def test_output_dir_property(self, tmp_path: Path) -> None:
        store = FilesystemTrackStore(tmp_path / "music")
        assert store.output_dir == tmp_path / "music"


class TestFakeTrackStore:
    """The fake mirrors the real store's contract without disk."""

    def test_add_then_tracks_for_and_exists(self) -> None:
        store = FakeTrackStore()
        store.add("calm_jazz_01")
        store.add("calm_jazz_00")
        store.add("happy_techno_00")

        assert store.exists("calm_jazz_00")
        assert not store.exists("nope")
        assert store.tracks_for("calm_jazz_") == (
            store.path_for("calm_jazz_00"),
            store.path_for("calm_jazz_01"),
        )

    def test_listing_sorted_by_stem(self) -> None:
        store = FakeTrackStore()
        store.add("b", size_bytes=2, modified=2.0)
        store.add("a", size_bytes=1, modified=1.0)

        assert [m.path.stem for m in store.listing()] == ["a", "b"]

    def test_prepare_is_noop(self) -> None:
        FakeTrackStore().prepare()  # must not raise
