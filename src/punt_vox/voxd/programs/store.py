"""The persistence seam: the ``ProgramStore`` and ``PartStore`` protocols.

All Program and Part persistence lives behind these two protocols (PY-IC-9,
PY-DP-11). The domain and the fill loop depend only on these interfaces, never
on ``pathlib`` or ``json`` directly, so every domain and loop test runs
filesystem-free against an in-memory fake. The production implementations live
in :mod:`punt_vox.voxd.programs.filesystem_store`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from punt_vox.voxd.programs.identifiers import ProgramName
from punt_vox.voxd.programs.manifest import PartEntry, ProgramManifest
from punt_vox.voxd.programs.part import Part

__all__ = ["PartStore", "ProgramStore"]


@runtime_checkable
class PartStore(Protocol):
    """One Program's Parts on disk, scoped to a single directory."""

    def ready_parts(self) -> tuple[Part, ...]:
        """Return the ready Parts, ordered by intrinsic index."""
        ...

    def next_index(self) -> int:
        """Return the 1-based index the next recorded Part will take."""
        ...

    def write_target(self, index: int) -> Path:
        """Return where the audio for a new Part at ``index`` should be written."""
        ...

    def record(self, entry: PartEntry) -> None:
        """Append ``entry`` to the manifest and persist it durably."""
        ...

    def manifest(self) -> ProgramManifest:
        """Return the current manifest."""
        ...

    def prepare(self) -> None:
        """Ensure the backing storage exists before a write."""
        ...


@runtime_checkable
class ProgramStore(Protocol):
    """The set of persisted Programs -- the only place the programs root is read."""

    def list_programs(self) -> tuple[ProgramManifest, ...]:
        """Return every saved Program's manifest, ordered by name."""
        ...

    def resolve(self, name: ProgramName) -> ProgramManifest | None:
        """Return the manifest for ``name``, or ``None`` if none is saved.

        Absence-by-name is the documented contract (PY-EH-8): a caller asking
        "is there a Program called X?" gets ``None`` for no, not an exception.
        """
        ...

    def open(self, name: ProgramName) -> PartStore:
        """Return the PartStore for an existing Program, raising if absent."""
        ...

    def create(self, manifest: ProgramManifest) -> PartStore:
        """Create a new Program from ``manifest`` and return its PartStore."""
        ...
