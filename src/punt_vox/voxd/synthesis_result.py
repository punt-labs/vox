"""Result value type for the synthesis pipeline."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Self

__all__ = ["SynthesisOutcome"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SynthesisOutcome:
    """Consumer-owned temporary audio and whether it was served from cache.

    ``cached`` is ``True`` when the request was served from an existing
    on-disk cache entry (no TTS call) and ``False`` when audio was
    synthesized fresh. The daemon forwards this fact to the client so a
    caller can confirm a cache hit occurred.
    """

    path: Path
    cached: bool

    @classmethod
    async def from_cache(cls, cached_path: Path) -> Self | None:
        """Snapshot audio off the event loop, releasing abandoned results."""
        work = asyncio.get_running_loop().run_in_executor(
            None, cls._copy_cached, cached_path
        )
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            work.add_done_callback(cls._discard_unclaimed)
            raise

    @classmethod
    def _copy_cached(cls, cached_path: Path) -> Self | None:
        """Snapshot a cache hit, or treat eviction before open as a miss."""
        try:
            source = cached_path.open("rb")
        except FileNotFoundError:
            return None
        with source, tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as dest:
            outcome = cls(path=Path(dest.name), cached=True)
            try:
                shutil.copyfileobj(source, dest)
                dest.flush()
            except BaseException:
                outcome.discard()
                raise
        return outcome

    def discard(self) -> None:
        """Remove unneeded audio without interrupting playback or recording."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove synthesized audio %s: %s", self.path, exc)

    def discard_after[T](self, work: asyncio.Future[T]) -> None:
        """Release audio when a worker is done, even if its caller cancelled."""
        self.discard()
        if not work.cancelled() and (exc := work.exception()) is not None:
            logger.warning("Abandoned recording write failed: %s", exc)

    @staticmethod
    def _discard_unclaimed[T: SynthesisOutcome](work: asyncio.Future[T | None]) -> None:
        """Collect the result of a cache copy whose requester was cancelled."""
        if work.cancelled():
            return
        if (exc := work.exception()) is not None:
            logger.warning("Abandoned cache copy failed: %s", exc)
        elif (outcome := work.result()) is not None:
            outcome.discard()
