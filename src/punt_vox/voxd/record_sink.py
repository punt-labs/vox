"""Daemon-side sink that places a synthesized recording at the caller's path.

``record`` returns a file, not bytes over the wire. The daemon owns every other
audio file on disk (cache, tracks, ephemeral temps); it owns the record output
too. The sink takes the synthesized source file plus the caller's destination
and lands the audio there **atomically** -- a copy into a sibling temp followed
by ``os.replace``, so a crash or synthesis error mid-write leaves no partial
file at the destination.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Self, final

from punt_vox.types import generate_filename

__all__ = ["RecordSink", "RecordWrite"]


@dataclass(frozen=True, slots=True)
class RecordWrite:
    """The landed recording: its final path and byte count.

    ``byte_count`` is the size the daemon wrote, echoed to the client so the
    caller can assert the on-disk file matches (byte-correct delivery).
    """

    path: Path
    byte_count: int


@final
class RecordSink:
    """Place a synthesized recording at an explicit path or a hashed name.

    An explicit path pins the single output file; otherwise each recording lands
    at ``generate_filename(text)`` under the directory -- the canonical
    content-addressed name every other MP3 shares.
    """

    __slots__ = ("_dir", "_explicit")

    _dir: Path
    _explicit: Path | None

    def __new__(cls, output_dir: Path, explicit_path: Path | None = None) -> Self:
        self = super().__new__(cls)
        self._dir = output_dir
        self._explicit = explicit_path
        return self

    def place(self, *, source: Path, text: str, cached: bool) -> RecordWrite:
        """Land *source* at the destination atomically; return path + bytes.

        A fresh (non-cached) source is already a complete file, so it is moved
        with an atomic rename -- no byte copy -- falling back to the copy path on
        ``OSError`` (e.g. ``EXDEV`` when the synth temp dir and the destination
        are on different filesystems). ``cached`` sources are always copied,
        never moved, so the cache entry survives. Either way the destination is
        replaced atomically, so it is the complete file or untouched, never a
        partial write.
        """
        dest = self._destination(text)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if not cached:
            moved = self._move(source, dest)
            if moved is not None:
                return moved
        return self._copy(source, dest, cached=cached)

    @staticmethod
    def _move(source: Path, dest: Path) -> RecordWrite | None:
        """Atomically rename *source* onto *dest*, or None if it can't (EXDEV).

        Returns ``None`` (never partially writes) so the caller falls back to the
        cross-filesystem copy path.
        """
        try:
            source.replace(dest)
        except OSError:
            return None
        # An ephemeral source may not be private; the copy path's mkstemp temp is
        # 0600, so match that here to keep the recording private.
        dest.chmod(0o600)
        return RecordWrite(path=dest, byte_count=dest.stat().st_size)

    @staticmethod
    def _copy(source: Path, dest: Path, *, cached: bool) -> RecordWrite:
        """Copy *source* to a sibling temp then atomically rename onto *dest*.

        The byte count is taken from the temp *before* the rename (the commit
        point); the ephemeral-source cleanup afterwards is best-effort, so a
        failure past the commit never turns a completed write into a failure.
        """
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".mp3.tmp")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            shutil.copyfile(source, tmp)
            byte_count = tmp.stat().st_size
            tmp.replace(dest)  # commit point -- the write is complete after this
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

        if not cached:
            with contextlib.suppress(OSError):
                source.unlink(missing_ok=True)
        return RecordWrite(path=dest, byte_count=byte_count)

    def _destination(self, text: str) -> Path:
        """Resolve the final output path from the explicit path or the dir name."""
        if self._explicit is not None:
            return self._explicit
        return self._dir / generate_filename(text)
