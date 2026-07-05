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
from pathlib import Path
from typing import Self, final

from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.format import Format
from punt_vox.voxd.programs.identifiers import ProgramName
from punt_vox.voxd.programs.manifest import PartEntry, PlaylistSubject, ProgramManifest
from punt_vox.voxd.programs.migration_report import MigrationReport
from punt_vox.voxd.programs.part import PartStatus

__all__ = ["LegacyMigration", "MigrationError", "MigrationReport"]

_POOL_SUFFIX = re.compile(r"_\d{8}_\d{4}_\d+$")
"""The ``_<YYYYMMDD>_<HHMM>_<n>`` suffix a generated pool track carries."""


class MigrationError(Exception):
    """The legacy layout cannot be migrated safely (e.g. ``programs/`` is populated)."""


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
        A filesystem error (``OSError``) or an unusable derived name
        (``ValueError``) is wrapped as a :class:`MigrationError` so the one
        command boundary reports every failure uniformly (finding F3).
        """
        if self._store.list_programs():
            msg = "programs/ is already populated; refusing to migrate again"
            raise MigrationError(msg)
        groups = self._grouped_by_program()
        return MigrationReport(
            names=tuple(sorted(groups)), parts=self._migrate_all(groups)
        )

    def _migrate_all(self, groups: dict[str, tuple[Path, ...]]) -> int:
        """Migrate every group, wrapping fs/name failures as MigrationError (F3).

        ``OSError`` (a bad move: full disk, permission, cross-device) and
        ``ValueError`` (a stem that reduces to an empty Program name) become a
        clean :class:`MigrationError` so the CLI reports every failure uniformly.
        """
        try:
            return sum(
                self._migrate_group(name, files) for name, files in groups.items()
            )
        except (OSError, ValueError) as exc:
            msg = f"migration failed: {exc}"
            raise MigrationError(msg) from exc

    def _migrate_group(self, name: str, files: tuple[Path, ...]) -> int:
        """Create Program ``name`` and move each file into place before recording it.

        Crash-safety (finding F2): every Part is recorded *after* its audio is
        moved onto disk, so the manifest never claims more ready Parts than
        exist. A crash mid-migration leaves a manifest that under-claims (a safe
        subset the operator can inspect) -- never one that over-claims, which
        would misreport readiness and later reference a missing file (vox-ig52).
        """
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
                PartEntry(index=index, file=f"{index:03d}.mp3", status=PartStatus.READY)
            )
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
