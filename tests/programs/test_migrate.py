"""Tests for the one-time legacy ``tracks/`` -> ``programs/`` migration (R1, R3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.identifiers import ProgramName
from punt_vox.voxd.programs.migrate import LegacyMigration, MigrationError


def _legacy(tmp_path: Path, *names: str) -> Path:
    """Create a legacy tracks dir populated with named ``.mp3`` files."""
    legacy = tmp_path / "tracks"
    legacy.mkdir()
    for name in names:
        (legacy / f"{name}.mp3").write_bytes(b"ID3 fake audio")
    return legacy


def test_pool_tracks_group_into_one_program(tmp_path: Path) -> None:
    """Tracks sharing a ``<vibe>_<style>`` prefix migrate into one Program."""
    legacy = _legacy(
        tmp_path,
        "ambient_techno_20250101_1200_0",
        "ambient_techno_20250101_1200_1",
        "ambient_techno_20250101_1305_2",
    )
    root = tmp_path / "programs"

    report = LegacyMigration(legacy, root).run()

    assert report.names == ("ambient_techno",)
    assert report.parts == 3
    manifest = FilesystemProgramStore(root).resolve(ProgramName("ambient_techno"))
    assert manifest is not None
    assert [entry.index for entry in manifest.parts] == [1, 2, 3]
    assert manifest.subject.vibe == "ambient"
    assert manifest.subject.style == "techno"


def test_files_are_moved_not_copied(tmp_path: Path) -> None:
    """Migration relocates the audio: legacy gone, NNN.mp3 present under the name."""
    legacy = _legacy(tmp_path, "ambient_techno_20250101_1200_0")
    root = tmp_path / "programs"

    LegacyMigration(legacy, root).run()

    assert list(legacy.glob("*.mp3")) == []  # moved, not left behind
    assert (root / "ambient_techno" / "001.mp3").is_file()
    assert (root / "ambient_techno" / "manifest.json").is_file()


def test_named_track_becomes_single_part_program(tmp_path: Path) -> None:
    """A --name track (no timestamp suffix) migrates to its own Program (R3)."""
    legacy = _legacy(tmp_path, "my_favourite")
    root = tmp_path / "programs"

    report = LegacyMigration(legacy, root).run()

    assert "my_favourite" in report.names
    manifest = FilesystemProgramStore(root).resolve(ProgramName("my_favourite"))
    assert manifest is not None
    assert len(manifest.parts) == 1


def test_refuses_when_programs_already_populated(tmp_path: Path) -> None:
    """A second run refuses rather than double-migrating (R1 idempotency guard)."""
    legacy = _legacy(tmp_path, "ambient_techno_20250101_1200_0")
    root = tmp_path / "programs"
    LegacyMigration(legacy, root).run()
    (legacy / "ambient_techno_20250101_1200_1.mp3").write_bytes(b"more")

    with pytest.raises(MigrationError, match="already populated"):
        LegacyMigration(legacy, root).run()


def test_absent_legacy_dir_migrates_nothing(tmp_path: Path) -> None:
    """An absent tracks/ directory migrates nothing and reports empty."""
    report = LegacyMigration(tmp_path / "tracks", tmp_path / "programs").run()

    assert report.is_empty
    assert report.summary() == "nothing to migrate"


def test_is_available_reflects_state(tmp_path: Path) -> None:
    """is_available is true with legacy audio and no Programs, false after migrating."""
    legacy = _legacy(tmp_path, "ambient_techno_20250101_1200_0")
    root = tmp_path / "programs"
    migration = LegacyMigration(legacy, root)

    assert migration.is_available()
    migration.run()
    assert not LegacyMigration(legacy, root).is_available()


def test_summary_lists_programs(tmp_path: Path) -> None:
    """A non-empty report summarises the counts and names for the CLI."""
    legacy = _legacy(
        tmp_path, "ambient_techno_20250101_1200_0", "jazz_lounge_20250101_1200_0"
    )
    report = LegacyMigration(legacy, tmp_path / "programs").run()

    text = report.summary()
    assert "2 track(s)" in text
    assert "2 program(s)" in text
    assert "ambient_techno" in text
    assert "jazz_lounge" in text
