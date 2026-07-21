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

        The audio is copied to a sibling temp in the destination directory and
        atomically renamed onto the final path -- a rename within one
        filesystem, so the destination is either the complete file or untouched,
        never a partial write. ``cached`` sources (cache-hit files) are preserved;
        ephemeral fresh-synthesis sources are removed after a successful land.

        Once the rename commits, the write is done: the byte count is taken from
        the temp *before* the rename, and the ephemeral-source cleanup is
        best-effort. A failure after the commit point must not turn a completed
        write into a reported failure.
        """
        dest = self._destination(text)
        dest.parent.mkdir(parents=True, exist_ok=True)

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

        # Post-commit: cleanup failures never fail the completed write.
        if not cached:
            with contextlib.suppress(OSError):
                source.unlink(missing_ok=True)
        return RecordWrite(path=dest, byte_count=byte_count)

    def _destination(self, text: str) -> Path:
        """Resolve the final output path from the explicit path or the dir name."""
        if self._explicit is not None:
            return self._explicit
        return self._dir / generate_filename(text)
