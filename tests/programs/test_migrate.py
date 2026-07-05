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


def _ready_count(root: Path, name: str) -> int:
    """Return how many ready Parts the on-disk manifest for ``name`` claims."""
    manifest = FilesystemProgramStore(root).resolve(ProgramName(name))
    assert manifest is not None
    return len(manifest.ready_parts())


def test_crash_mid_move_never_over_claims_ready_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError mid-migration leaves a manifest that never over-claims (F2).

    The manifest must never report more ready Parts than exist on disk: each
    Part is recorded only after its file lands. A crash while moving the second
    track leaves exactly one recorded, playable Part and one file.
    """
    legacy = _legacy(
        tmp_path,
        "ambient_techno_20250101_1200_0",
        "ambient_techno_20250101_1200_1",
        "ambient_techno_20250101_1200_2",
    )
    root = tmp_path / "programs"
    real_replace = Path.replace

    def flaky_replace(self: Path, target: Path) -> Path:
        if target.name == "002.mp3":  # fail while moving the second track
            msg = "disk full"
            raise OSError(msg)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    with pytest.raises(MigrationError, match="migration failed"):
        LegacyMigration(legacy, root).run()

    program_dir = root / "ambient_techno"
    files_on_disk = len(list(program_dir.glob("[0-9]*.mp3")))
    assert _ready_count(root, "ambient_techno") <= files_on_disk
    assert _ready_count(root, "ambient_techno") == 1  # only the first Part landed


def test_crash_mid_migration_leaves_playable_recovered_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every ready Part the aborted manifest claims points at a file on disk (F2)."""
    legacy = _legacy(
        tmp_path,
        "ambient_techno_20250101_1200_0",
        "ambient_techno_20250101_1200_1",
    )
    root = tmp_path / "programs"
    real_replace = Path.replace

    def flaky_replace(self: Path, target: Path) -> Path:
        if target.name == "002.mp3":
            msg = "permission denied"
            raise OSError(msg)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    with pytest.raises(MigrationError):
        LegacyMigration(legacy, root).run()

    manifest = FilesystemProgramStore(root).resolve(ProgramName("ambient_techno"))
    assert manifest is not None
    for part in manifest.ready_parts():
        assert (root / "ambient_techno" / part.identity).is_file()


def test_os_error_is_wrapped_as_migration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw filesystem error surfaces as a clean MigrationError (F3)."""
    legacy = _legacy(tmp_path, "ambient_techno_20250101_1200_0")
    root = tmp_path / "programs"

    def boom(self: Path, target: Path) -> Path:
        msg = "cross-device link"
        raise OSError(msg)

    monkeypatch.setattr(Path, "replace", boom)

    with pytest.raises(MigrationError, match="migration failed"):
        LegacyMigration(legacy, root).run()


def test_bad_derived_name_is_wrapped_as_migration_error(tmp_path: Path) -> None:
    """A stem that reduces to an empty Program name is a clean MigrationError (F3)."""
    legacy = _legacy(tmp_path, "_20250101_1200_5")  # whole stem is the pool suffix
    root = tmp_path / "programs"

    with pytest.raises(MigrationError, match="migration failed"):
        LegacyMigration(legacy, root).run()
