"""One-time migration of the flat legacy ``tracks/`` layout into named Programs.

Real users have ``~/Music/vox/tracks/*.mp3`` whose "pool" was only ever a
filename prefix ``<vibe>_<style>_<ts>_<n>.mp3`` (vox-us4g: a naming pattern, not
a list). Because this is *user data*, an explicit :class:`LegacyMigration` the
operator runs once via ``vox music migrate`` -- never start-up auto-mutation
(R1) -- groups the flat files by prefix and ``Path.replace``\\ s each into
``programs/<name>/NNN.mp3`` (a rename; org rule: mv, not delete). A ``--name X``
track (no timestamp suffix) becomes its own single-Part Program (R3). Each Part
is recorded only after its file lands, so a crash never leaves a manifest that
over-claims ready Parts (F2). A populated ``programs/`` makes a second run a
safe no-op, not a double migration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Self, final

from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.format import Format
from punt_vox.voxd.programs.identifiers import ProgramName
from punt_vox.voxd.programs.manifest import PartEntry, PlaylistSubject, ProgramManifest
from punt_vox.voxd.programs.part import PartStatus

__all__ = ["LegacyMigration", "MigrationError", "MigrationReport"]

_POOL_SUFFIX = re.compile(r"_\d{8}_\d{4}_\d+$")
"""The ``_<YYYYMMDD>_<HHMM>_<n>`` suffix a generated pool track carries."""


class MigrationError(Exception):
    """The legacy layout cannot be migrated safely (e.g. ``programs/`` is populated)."""


@final
@dataclass(frozen=True, slots=True)
class MigrationReport:
    """What a migration run moved: the Programs created and Parts relocated."""

    names: tuple[str, ...]
    parts: int

    @property
    def programs(self) -> int:
        """Return the number of Programs the run created."""
        return len(self.names)

    @property
    def is_empty(self) -> bool:
        """Return whether the run had nothing to migrate."""
        return not self.names

    def summary(self) -> str:
        """Return a human-readable one-line summary for the CLI."""
        if self.is_empty:
            return "nothing to migrate"
        listed = ", ".join(self.names)
        return (
            f"migrated {self.parts} track(s) into {self.programs} program(s): {listed}"
        )


@final
class LegacyMigration:
    """Move a flat ``tracks/`` directory into named, manifest-backed Programs."""

    __slots__ = ("_legacy_dir", "_store")
    _legacy_dir: Path
    _store: FilesystemProgramStore

    def __new__(cls, legacy_dir: Path, programs_root: Path) -> Self:
        self = super().__new__(cls)
        self._legacy_dir = legacy_dir
        self._store = FilesystemProgramStore(programs_root)
        return self

    def is_available(self) -> bool:
        """Return whether there is legacy audio to migrate and no Programs yet."""
        return bool(self._legacy_files()) and not self._store.list_programs()

    def run(self) -> MigrationReport:
        """Migrate every legacy track into a named Program (see module docstring).

        Refuses on a populated ``programs/`` (R1 idempotency guard); an absent
        ``tracks/`` migrates nothing and reports so.
        """
        if self._store.list_programs():
            msg = "programs/ is already populated; refusing to migrate again"
            raise MigrationError(msg)
        groups = self._grouped_by_program()
        moved = sum(self._migrate_group(name, files) for name, files in groups.items())
        return MigrationReport(names=tuple(sorted(groups)), parts=moved)

    def _migrate_group(self, name: str, files: tuple[Path, ...]) -> int:
        """Move each file into place, then record it -- ready only once on disk (F2).

        Fs/name failures are wrapped as one :class:`MigrationError` for the CLI
        (F3); a crash mid-loop never over-claims ready Parts (vox-ig52).
        """
        try:
            part_store = self._store.create(
                ProgramManifest(
                    name=ProgramName(name),
                    fmt=Format.PLAYLIST,
                    subject=self._subject_for(name),
                    parts=(),
                )
            )
            for index, source in enumerate(files, start=1):
                source.replace(part_store.write_target(index))
                part_store.record(
                    PartEntry(
                        index=index, file=f"{index:03d}.mp3", status=PartStatus.READY
                    )
                )
            return len(files)
        except (OSError, ValueError) as exc:
            msg = f"migration failed: {exc}"
            raise MigrationError(msg) from exc

    def _grouped_by_program(self) -> dict[str, tuple[Path, ...]]:
        """Group legacy tracks by their derived Program name, each sorted."""
        groups: dict[str, list[Path]] = {}
        for track in self._legacy_files():
            groups.setdefault(self._program_name(track.stem), []).append(track)
        return {name: tuple(sorted(files)) for name, files in groups.items()}

    def _legacy_files(self) -> tuple[Path, ...]:
        """Return the legacy ``.mp3`` files, sorted, or empty when none exist."""
        if not self._legacy_dir.is_dir():
            return ()
        return tuple(sorted(self._legacy_dir.glob("*.mp3")))

    @staticmethod
    def _program_name(stem: str) -> str:
        """Derive a Program name: strip the pool suffix, else keep the stem (R3)."""
        return _POOL_SUFFIX.sub("", stem)

    @staticmethod
    def _subject_for(name: str) -> PlaylistSubject:
        """Split a Program name into its (vibe, style) display subject."""
        vibe, _, style = name.partition("_")
        return PlaylistSubject(vibe=vibe, style=style)
