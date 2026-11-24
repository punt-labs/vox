"""Path-free diagnostics for the daemon's album output directories."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from typing import TYPE_CHECKING, Literal, Self, final

from punt_vox.dirs import _resolve_music_dir  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 1.0


@final
class OutputHealth:
    """Probe the actual root wired into the daemon's album store."""

    __slots__ = ("_pending", "_root")
    _pending: asyncio.Task[dict[str, object]] | None
    _root: Path

    def __new__(cls, root: Path) -> Self:
        """Create a probe for the album store at *root*."""
        self = super().__new__(cls)
        self._pending = None
        self._root = root
        return self

    def probe(self) -> dict[str, object]:
        """Return output-directory verdicts without sending paths to clients."""
        return {
            "output_dir": self._output_status(),
            "music_dir_missing": self._music_dir_missing(),
        }

    async def probe_within_budget(self) -> dict[str, object]:
        """Bound the wait and reuse the pending probe if storage is still busy."""
        if self._pending is None or self._pending.done():
            self._pending = asyncio.create_task(asyncio.to_thread(self.probe))
        try:
            return await asyncio.wait_for(
                asyncio.shield(self._pending), timeout=_PROBE_TIMEOUT_S
            )
        except TimeoutError:
            logger.warning("Timed out checking the album output directories")
            return {}

    def _music_dir_missing(self) -> bool:
        try:
            music = _resolve_music_dir()  # pyright: ignore[reportPrivateUsage]
            return (
                self._root.resolve().is_relative_to(music.resolve())
                and self._existing_ancestor(music) != music
            )
        except (OSError, RuntimeError):
            logger.warning("Could not check the music directory", exc_info=True)
            return False

    def _output_status(self) -> Literal["absent", "not_writable", "writable"]:
        try:
            directory = self._existing_ancestor(self._root)
            with tempfile.TemporaryFile(buffering=0, dir=directory) as probe:
                probe.write(b"ok")
        except OSError:
            logger.warning(
                "Could not write to the album output directory", exc_info=True
            )
            return "not_writable"
        return "writable" if directory == self._root else "absent"

    @staticmethod
    def _existing_ancestor(path: Path) -> Path:
        for candidate in (path, *path.parents):
            try:
                candidate.stat()
            except FileNotFoundError:
                if candidate.is_symlink():
                    raise
            else:
                return candidate
        raise FileNotFoundError(path)
