"""Track storage: the TrackStore seam and its filesystem implementation.

This is the one music module that touches the disk. The ``TrackStore`` protocol
(defined here, next to its production implementation) is the only interface the
rest of the domain depends on for track storage -- pool enumeration, listing,
existence checks, the write target, and directory preparation. Tests inject an
in-memory fake.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Self, final, runtime_checkable

__all__ = ["FileMeta", "FilesystemTrackStore", "TrackStore"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FileMeta:
    """Filesystem metadata for one stored track (a store->domain value)."""

    path: Path
    size_bytes: int
    modified: float  # Unix timestamp (st_mtime)


@runtime_checkable
class TrackStore(Protocol):
    """Storage seam for generated tracks -- the one place disk is touched.

    Domain code depends on this interface, never on ``pathlib`` directly, so
    tests inject an in-memory fake.
    """

    def tracks_for(self, prefix: str) -> tuple[Path, ...]:
        """Return the saved track paths sharing ``prefix``, sorted."""
        ...

    def listing(self) -> tuple[FileMeta, ...]:
        """Return metadata for every saved track, sorted by path."""
        ...

    def exists(self, stem: str) -> bool:
        """Return whether a track named ``stem`` is already stored."""
        ...

    def path_for(self, stem: str) -> Path:
        """Return the write target path for a track named ``stem``."""
        ...

    def prepare(self) -> None:
        """Ensure the backing storage exists before a write."""
        ...


@final
class FilesystemTrackStore:
    """Store tracks as ``.mp3`` files under a single output directory.

    This is the production :class:`~punt_vox.voxd.music.types.TrackStore`.
    All glob, stat, and directory access lives here; the rest of the music
    domain depends on the protocol and never on ``pathlib``.
    """

    __slots__ = ("_output_dir",)

    _output_dir: Path

    def __new__(cls, output_dir: Path) -> Self:
        self = super().__new__(cls)
        self._output_dir = output_dir
        return self

    @property
    def output_dir(self) -> Path:
        """Return the directory that backs this store."""
        return self._output_dir

    def tracks_for(self, prefix: str) -> tuple[Path, ...]:
        """Return the saved track paths sharing ``prefix``, sorted."""
        return tuple(sorted(self._output_dir.glob(f"{prefix}*.mp3")))

    def listing(self) -> tuple[FileMeta, ...]:
        """Return metadata for every saved track, sorted by path.

        Tracks that vanish between the glob and the stat (a concurrent
        delete) are skipped rather than raising.
        """
        return tuple(
            meta
            for mp3 in sorted(self._output_dir.glob("*.mp3"))
            if (meta := self._meta_for(mp3)) is not None
        )

    def exists(self, stem: str) -> bool:
        """Return whether a track named ``stem`` is already stored."""
        return (self._output_dir / f"{stem}.mp3").exists()

    def path_for(self, stem: str) -> Path:
        """Return the write target path for a track named ``stem``."""
        return self._output_dir / f"{stem}.mp3"

    def prepare(self) -> None:
        """Ensure the backing directory exists before a write."""
        self._output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _meta_for(mp3: Path) -> FileMeta | None:
        """Return the metadata for ``mp3``, or None if it disappeared."""
        with contextlib.suppress(FileNotFoundError):
            stat = mp3.stat()
            return FileMeta(path=mp3, size_bytes=stat.st_size, modified=stat.st_mtime)
        logger.debug("Track disappeared during listing, skipping: %s", mp3)
        return None
