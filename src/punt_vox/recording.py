"""Content-addressed file sink for the ``record`` tool's synthesized audio."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Self, final

__all__ = ["RecordingSink"]


@final
class RecordingSink:
    """Writes synthesized audio to disk and describes the result record.

    An explicit path pins a single-segment write; otherwise each segment lands at
    ``<dir>/<md5(text)[:10]>.mp3``. The sink owns the directory and creates the
    parent on every write, so callers never touch the filesystem layout.
    """

    __slots__ = ("_dir", "_explicit")

    _dir: Path
    _explicit: Path | None

    def __new__(cls, output_dir: Path, explicit_path: Path | None = None) -> Self:
        self = super().__new__(cls)
        self._dir = output_dir
        self._explicit = explicit_path
        return self

    def entry(
        self, text: str, voice: str | None, provider: str | None, mp3_bytes: bytes
    ) -> dict[str, object]:
        """Write *mp3_bytes* and return the result record the reply carries."""
        path = self.write(text, mp3_bytes)
        return {
            "path": str(path),
            "text": text,
            "voice": voice,
            "provider": provider,
            "bytes": len(mp3_bytes),
        }

    def write(self, text: str, mp3_bytes: bytes) -> Path:
        """Write *mp3_bytes* for *text* and return the file path."""
        path = self._explicit if self._explicit is not None else self._hashed_path(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(mp3_bytes)
        return path

    def _hashed_path(self, text: str) -> Path:
        """Return the content-addressed path ``<dir>/<md5(text)[:10]>.mp3``."""
        digest = hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()[:10]
        return self._dir / f"{digest}.mp3"
