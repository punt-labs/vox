"""Background pool fill -- one cancellable sequential generation task."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Self

from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.pool import TrackPool

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["PoolFiller"]

logger = logging.getLogger(__name__)

_FILL_MAX_RETRIES = 3


class PoolFiller:
    """Own the single background task that fills one (vibe, style) pool.

    The task generates tracks one at a time (sequential -- avoids the
    ElevenLabs rate limits concurrent fill hit) until the pool reaches
    ``POOL_SIZE``, then exits. Exactly one fill is ever live: retargeting
    to a different pool cancels the old task first, so credit spend stays
    bounded to the currently-playing pool. The scheduler owns this object
    and drives it synchronously from ``turn_on`` / ``update_vibe`` /
    ``turn_off``.
    """

    __slots__ = ("_first", "_first_name", "_first_ready", "_generator", "_key", "_task")

    _generator: TrackGenerator
    _task: asyncio.Task[None] | None
    _key: tuple[str, str] | None
    _first_ready: asyncio.Event
    _first: Path | BaseException | None
    _first_name: str

    def __new__(cls, generator: TrackGenerator) -> Self:
        self = super().__new__(cls)
        self._generator = generator
        self._task = None
        self._key = None
        self._first_ready = asyncio.Event()
        self._first = None
        self._first_name = ""
        return self

    @property
    def is_running(self) -> bool:
        """Return whether a fill task is currently live."""
        return self._task is not None and not self._task.done()

    def ensure_running(
        self, vibe: tuple[str, str], style: str, first_name: str = ""
    ) -> None:
        """Start or retarget the background fill for the (vibe, style) pool.

        Same pool with a live task: no-op. A different pool: cancel the old
        task and start a fresh one -- unless the new pool is already full, in
        which case nothing generates (it rotates instead). ``first_name``, when
        set, names the very first generated track (``/music on --name X`` for a
        not-yet-saved ``X``); subsequent tracks are auto-named.
        """
        key = (vibe[0], style)
        if self._key == key and self.is_running:
            return
        self._cancel_task()
        self._key = key
        self._first_name = first_name
        self._reset_first()
        prefix = TrackGenerator.pool_prefix(key)
        if TrackPool.from_paths(self._generator.tracks_for(prefix)).is_full:
            return
        self._task = asyncio.create_task(self._fill(vibe, style, prefix))

    def cancel(self) -> None:
        """Cancel the fill task and forget the target -- no orphaned generation."""
        self._cancel_task()
        self._key = None
        self._reset_first()

    async def await_first_track(self) -> Path:
        """Block until the fill delivers the first track, then return it.

        Raise if the fill exhausted its retries before producing any track --
        the caller (an empty pool entering ``generating-first``) then has
        nothing to play and disables music.
        """
        await self._first_ready.wait()
        result = self._first
        if isinstance(result, BaseException):
            raise result
        if result is None:
            msg = "await_first_track signalled ready without a track"
            raise RuntimeError(msg)
        return result

    # -- Private ---------------------------------------------------------------

    async def _fill(self, vibe: tuple[str, str], style: str, prefix: str) -> None:
        """Generate tracks one at a time until the pool is full."""
        retries = 0
        while not TrackPool.from_paths(self._generator.tracks_for(prefix)).is_full:
            path = await self._generate_one(vibe, style)
            if path is None:
                retries += 1
                if retries >= _FILL_MAX_RETRIES:
                    self._fail_first_if_waiting(prefix)
                    return
                await self._backoff(2.0 ** (retries - 1))
                continue
            retries = 0
            self._first_name = ""  # honoured once, on the first track only
            self._deliver_first(path)
        logger.info("Pool %s reached POOL_SIZE; background fill stopping", prefix)

    async def _generate_one(self, vibe: tuple[str, str], style: str) -> Path | None:
        """Generate a single track, or None if generation failed.

        Cancellation propagates (``/music off`` and vibe retarget cancel the
        task); provider/IO failures are logged and reported as None so
        :meth:`_fill` can back off.
        """
        try:
            path, _ = await self._generator.generate(vibe, style, self._first_name)
        except asyncio.CancelledError:
            raise
        except Exception:  # provider / IO boundary (PY-EH-6)
            logger.exception("Background fill generation failed")
            return None
        return path

    def _deliver_first(self, path: Path) -> None:
        """Record the first produced track and wake any waiter."""
        if not self._first_ready.is_set():
            self._first = path
            self._first_ready.set()

    def _fail_first_if_waiting(self, prefix: str) -> None:
        """Signal first-track failure so an empty-pool waiter stops blocking."""
        if not self._first_ready.is_set():
            msg = f"could not generate the first track for {prefix}"
            self._first = RuntimeError(msg)
            self._first_ready.set()

    def _cancel_task(self) -> None:
        """Request cancellation of the live fill task, if any."""
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()

    def _reset_first(self) -> None:
        """Reset the first-track handshake for a new target pool.

        The event object is reused (cleared, not replaced) so a caller already
        awaiting ``await_first_track`` across a retarget waits for the new
        pool's first track instead of blocking on a stale event.
        """
        self._first = None
        self._first_ready.clear()

    @staticmethod
    async def _backoff(seconds: float) -> None:
        """Sleep between fill retries."""
        await asyncio.sleep(seconds)
