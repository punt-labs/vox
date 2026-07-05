"""One-time migration of the flat legacy ``tracks/`` layout into named Programs.

Real users have ``~/Music/vox/tracks/*.mp3`` with no manifest, where the "pool"
was only ever a filename prefix ``<vibe>_<style>_<ts>_<n>.mp3`` (vox-us4g: a
naming pattern, not a list). This is *user data*, so the forward path is an
explicit :class:`LegacyMigration` the operator runs once via ``vox music
migrate`` -- never start-up auto-mutation (decision R1). It groups the flat
files by prefix, ``mv``\\ s each group into ``programs/<name>/NNN.mp3`` with
intrinsic indices, and writes a ``manifest.json`` per group. A ``--name X``
track (no timestamp pattern) becomes its own single-Part Program named ``X``
(decision R3). The move is ``Path.replace`` (a rename, org rule: mv, not delete);
the legacy files are gone only once they have moved. The command refuses if
``programs/`` is already populated, so a second run is a safe no-op, not a
double migration.
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
        """Return whether there is legacy audio to migrate and no Programs yet.

        The daemon uses this to log a one-line hint (it never mutates the disk),
        and the CLI uses it to short-circuit before touching anything.
        """
        return bool(self._legacy_files()) and not self._store.list_programs()

    def run(self) -> MigrationReport:
        """Migrate every legacy track, raising if ``programs/`` is already populated.

        Refusing on a populated root (decision R1) makes a second run a safe
        no-op rather than a double migration -- the check is the idempotency
        guard. An absent or empty ``tracks/`` migrates nothing and reports so.
        """
        if self._store.list_programs():
            msg = "programs/ is already populated; refusing to migrate again"
            raise MigrationError(msg)
        groups = self._grouped_by_program()
        moved = sum(self._migrate_group(name, files) for name, files in groups.items())
        return MigrationReport(names=tuple(sorted(groups)), parts=moved)

    def _migrate_group(self, name: str, files: tuple[Path, ...]) -> int:
        """Create Program ``name`` from ``files`` and move each into ``NNN.mp3``."""
        entries = tuple(
            PartEntry(index=index, file=f"{index:03d}.mp3", status=PartStatus.READY)
            for index in range(1, len(files) + 1)
        )
        manifest = ProgramManifest(
            name=ProgramName(name),
            fmt=Format.PLAYLIST,
            subject=self._subject_for(name),
            parts=entries,
        )
        part_store = self._store.create(manifest)
        for index, source in enumerate(files, start=1):
            source.replace(part_store.write_target(index))
        return len(files)

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
        """Derive a Program name from a track stem.

        A pool track ``<vibe>_<style>_<ts>_<n>`` yields ``<vibe>_<style>``; a
        ``--name X`` track (no timestamp suffix) yields its whole stem, so it
        becomes its own single-Part Program (decision R3).
        """
        return _POOL_SUFFIX.sub("", stem)

    @staticmethod
    def _subject_for(name: str) -> PlaylistSubject:
        """Split a Program name into its (vibe, style) display subject."""
        vibe, _, style = name.partition("_")
        return PlaylistSubject(vibe=vibe, style=style)
