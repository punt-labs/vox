"""Background pool fill -- one cancellable sequential generation task."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.pool import TrackPool

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.music.prompts import PromptSet

__all__ = ["FillTarget", "PoolFiller"]

logger = logging.getLogger(__name__)

_FILL_MAX_RETRIES = 3


@dataclass(frozen=True, slots=True)
class FillTarget:
    """The pool a background fill targets and the prompt it generates with.

    ``prefix`` is the pool identity: it drives track enumeration, the fullness
    check, and the generated track names, so the fill grows exactly the pool the
    loop is playing (findings #1/#7). ``vibe`` and ``style`` drive the provider
    prompt. Two targets sharing a prefix but differing in vibe *tags* are
    distinct, so a tag-only change restarts the fill and subsequent tracks use
    the current tags rather than the stale ones captured in the old task's
    closure (finding #3) -- they still land in the same pool.
    """

    prefix: str
    vibe: tuple[str, str]
    style: str
    prompts: PromptSet


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

    __slots__ = (
        "_first",
        "_first_name",
        "_first_ready",
        "_gen_lock",
        "_generator",
        "_inflight",
        "_target",
        "_task",
    )

    _generator: TrackGenerator
    _task: asyncio.Task[None] | None
    _target: FillTarget | None
    _first_ready: asyncio.Event
    _first: Path | BaseException | None
    _first_name: str
    _gen_lock: asyncio.Lock
    _inflight: asyncio.Task[tuple[Path, str]] | None

    def __new__(cls, generator: TrackGenerator) -> Self:
        self = super().__new__(cls)
        self._generator = generator
        self._task = None
        self._target = None
        self._first_ready = asyncio.Event()
        self._first = None
        self._first_name = ""
        self._gen_lock = asyncio.Lock()
        self._inflight = None
        return self

    @property
    def is_running(self) -> bool:
        """Return whether a fill task is currently live."""
        return self._task is not None and not self._task.done()

    def ensure_running(self, target: FillTarget, first_name: str = "") -> None:
        """Start or retarget the background fill for ``target``.

        Same target (prefix, vibe, and style all equal) with a live task: no-op.
        Any difference -- including a vibe *tags* change that leaves the prefix
        untouched -- cancels the old task and starts a fresh one, unless the pool
        is already full, in which case nothing generates (it rotates instead).
        ``first_name``, when set, names the very first generated track
        (``/music on --name X`` for a not-yet-saved ``X``); the rest are
        auto-named within ``target.prefix``.
        """
        if self._target == target and self.is_running:
            return
        self._cancel_task()
        self._target = target
        self._first_name = first_name
        self._reset_first()
        if TrackPool.from_paths(self._generator.tracks_for(target.prefix)).is_full:
            return
        self._task = asyncio.create_task(self._fill(target))

    def cancel(self) -> None:
        """Cancel the fill task and forget the target -- no orphaned generation."""
        self._cancel_task()
        self._target = None
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

    async def _fill(self, target: FillTarget) -> None:
        """Generate tracks one at a time until the pool is full."""
        retries = 0
        while not TrackPool.from_paths(
            self._generator.tracks_for(target.prefix)
        ).is_full:
            path = await self._generate_one(target)
            if path is None:
                retries += 1
                if retries >= _FILL_MAX_RETRIES:
                    self._fail_first_if_waiting(target.prefix)
                    return
                await self._backoff(2.0 ** (retries - 1))
                continue
            retries = 0
            self._first_name = ""  # honoured once, on the first track only
            self._deliver_first(path)
        logger.info(
            "Pool %s reached POOL_SIZE; background fill stopping", target.prefix
        )

    async def _generate_one(self, target: FillTarget) -> Path | None:
        """Generate a single track into ``target.prefix``, or None on failure.

        The name is chosen here -- the first track honours ``first_name``, the
        rest are auto-named within ``target.prefix`` so every track lands in the
        pool the loop is playing, not the session's (vibe, style) pool (findings
        #1/#7). The provider call runs in a detached task
        (:meth:`_protected_generate`) that holds ``_gen_lock`` for its whole
        lifetime. We await it through ``asyncio.shield`` so cancelling the fill
        loop (``/music off`` and vibe retarget) cancels *this* await without
        cascading into the detached generation -- the underlying
        ``asyncio.to_thread`` cannot be stopped, so we let it finish while it
        keeps the lock, guaranteeing no second provider call runs concurrently.
        The abandoned generation's output is discarded by
        :meth:`_abandon_inflight`. Provider/IO failures are logged and reported
        as None so :meth:`_fill` can back off.
        """
        name = self._first_name or self._generator.auto_track_name(target.prefix)
        inflight = asyncio.ensure_future(self._protected_generate(target, name))
        self._inflight = inflight
        try:
            path, _ = await asyncio.shield(inflight)
        except asyncio.CancelledError:
            raise
        except Exception:  # provider / IO boundary (PY-EH-6)
            logger.exception("Background fill generation failed")
            return None
        finally:
            if self._inflight is inflight:
                self._inflight = None
        return path

    async def _protected_generate(
        self, target: FillTarget, track_name: str
    ) -> tuple[Path, str]:
        """Run one provider generation while holding the single-flight lock.

        The lock is held across the whole generation -- including the
        uncancellable ``asyncio.to_thread`` inside the provider -- so no second
        provider call can begin until this one's thread has finished. This
        coroutine is never cancelled directly (the fill loop is), so the lock
        releases only on true completion, bounding concurrent provider calls to
        exactly one across an off/retarget boundary.
        """
        async with self._gen_lock:
            return await self._generator.generate(
                target.vibe, target.style, track_name, target.prompts
            )

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
        """Cancel the live fill task and abandon any in-flight generation."""
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
        self._abandon_inflight()

    def _abandon_inflight(self) -> None:
        """Detach the in-flight generation so its output is discarded on settle.

        ``asyncio.to_thread`` cannot be cancelled: the provider call and file
        write run to completion regardless. Rather than leave the produced
        track orphaned in a pool the caller has left, we detach the generation
        and unlink whatever file it wrote once it settles. It keeps holding
        ``_gen_lock`` until then, so no second provider call starts meanwhile.
        """
        inflight = self._inflight
        self._inflight = None
        if inflight is None:
            return
        if inflight.done():
            self._discard_generated(inflight)
        else:
            inflight.add_done_callback(self._discard_generated)

    @staticmethod
    def _discard_generated(task: asyncio.Task[tuple[Path, str]]) -> None:
        """Unlink the track an abandoned generation wrote (task done callback)."""
        if task.cancelled() or task.exception() is not None:
            return
        path, _ = task.result()
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        logger.info("Discarded orphaned track from a cancelled fill: %s", path)

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
