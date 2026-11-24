"""Playback queue and audio player for voxd."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from punt_vox.voxd.audio_context import AudioContext, AudioFile
from punt_vox.voxd.pcm_output import OutputStreamFactory, sounddevice_output_stream
from punt_vox.voxd.pcm_stream import PcmStreamSink
from punt_vox.voxd.playback_result import PlaybackResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PLAYBACK_TIMEOUT_DEFAULT_S = 120.0
_PLAYBACK_TIMEOUT_PADDING_S = 10.0
_PLAYBACK_INTERRUPTED = "playback interrupted (daemon shutting down)"


@dataclass(frozen=True, slots=True)
class PlaybackItem:
    """A queued file and the resources to release when playback settles.

    ``outcome`` lets ``play`` await the host-side result. Fire-and-forget
    callers (say/chime) leave it unset. ``on_failure`` runs before waiters
    are notified, even if the enqueueing client has disconnected.
    """

    path: Path
    request_id: str
    notify: asyncio.Event
    outcome: asyncio.Future[PlaybackResult] | None = None
    discard: Callable[[], None] | None = None
    on_failure: Callable[[], None] | None = None

    def finish(self, result: PlaybackResult | str) -> None:
        """Release the item and complete any waiters."""
        try:
            self._resolve(result)
        finally:
            try:
                if self.discard is not None:
                    self.discard()
            finally:
                self.notify.set()

    def _resolve(self, result: PlaybackResult | str) -> None:
        """Publish a playback outcome or an interruption reason."""
        if isinstance(result, str):
            result = PlaybackResult(self.path, -1, 0.0, result, time.time())
        if not result.ok and self.on_failure is not None:
            self.on_failure()
        if self.outcome is not None and not self.outcome.done():
            self.outcome.set_result(result)


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def _monotonic() -> float:
    """Indirection for ``time.monotonic`` so tests can stub playback timing.

    Patching ``time.monotonic`` directly would also affect asyncio internals.
    """
    return time.monotonic()


# ---------------------------------------------------------------------------
# PlaybackQueue class
# ---------------------------------------------------------------------------


class PlaybackQueue:
    """Own the playback queue, mutex, and last-result state for voxd."""

    __slots__ = ("_last_result", "_mutex", "_output_stream", "_queue")

    _last_result: PlaybackResult | None
    _mutex: asyncio.Lock
    _output_stream: OutputStreamFactory
    _queue: asyncio.Queue[PlaybackItem]

    def __new__(
        cls, output_stream: OutputStreamFactory = sounddevice_output_stream
    ) -> Self:
        """Create a playback queue that opens PCM streams with *output_stream*."""
        self = super().__new__(cls)
        self._queue = asyncio.Queue()
        self._mutex = asyncio.Lock()
        self._last_result = None
        self._output_stream = output_stream
        return self

    # -- Properties ----------------------------------------------------------

    @property
    def last_result(self) -> PlaybackResult | None:
        """Return the most recent playback result, or None."""
        return self._last_result

    @property
    def queue_size(self) -> int:
        """Return the number of items waiting in the queue."""
        return self._queue.qsize()

    @property
    def mutex(self) -> asyncio.Lock:
        """Return the playback mutex for external serialization."""
        return self._mutex

    # -- Public methods ------------------------------------------------------

    async def enqueue(self, item: PlaybackItem) -> None:
        """Add an item to the playback queue."""
        await self._queue.put(item)

    @contextlib.asynccontextmanager
    async def stream_pcm(
        self, *, locked: bool = False
    ) -> AsyncGenerator[PcmStreamSink]:
        """Own the audio output for one streamed utterance.

        Holds the playback mutex from open to close, so queued speech, chimes
        and direct play wait behind the stream and resume after it, exactly
        as they wait behind a file. ``locked`` means the caller already holds
        the mutex: a voice call keeps it across replies and opens one stream
        per reply. The device is released on exit whether the utterance
        played out, was flushed, or the caller raised.
        """
        guard: contextlib.AbstractAsyncContextManager[object] = (
            contextlib.nullcontext() if locked else self._mutex
        )
        async with guard:
            sink = PcmStreamSink(asyncio.get_running_loop())
            await sink.open(self._output_stream)
            try:
                yield sink
            finally:
                await sink.close()

    def set_last_result(self, value: PlaybackResult | None) -> None:
        """Set the last playback result. Used by delegation and tests."""
        self._last_result = value

    async def play_audio(self, path: Path) -> None:
        """Play an audio file and record a rich result in ``last_result``.

        Coordinates file validation, timeout computation, subprocess lifecycle,
        and result logging. Each concern is handled by a focused private method.
        """
        cmd = AudioFile.player_command(path)
        env_snapshot = AudioContext().snapshot()

        size = self._validate_file(path)
        if size is None:
            return

        timeout = await self._compute_timeout(path)

        logger.debug(
            "Playback spawn: cmd=%s size=%d audio_env=%s timeout=%.1fs",
            cmd,
            size,
            env_snapshot,
            timeout,
        )

        result = await self._spawn_and_wait(cmd, timeout, env_snapshot, path)
        if result is None:
            return

        rc, elapsed, stderr_text = result
        recorded = self.record_result(
            path=path, rc=rc, elapsed=elapsed, stderr=stderr_text
        )
        recorded.log_outcome(size=size, cmd=cmd, env_snapshot=env_snapshot)

    def _validate_file(self, path: Path) -> int | None:
        """Return file size in bytes, or None after recording an error."""
        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.error("Playback aborted: cannot stat %s: %s", path, exc)
            self.record_result(
                path=path, rc=-1, elapsed=0.0, stderr=f"stat failed: {exc}"
            )
            return None

        if size == 0:
            logger.error(
                "Playback aborted: 0-byte audio file %s -- synthesis bug upstream",
                path,
            )
            self.record_result(path=path, rc=-1, elapsed=0.0, stderr="0-byte file")
            return None

        return size

    async def _compute_timeout(self, path: Path) -> float:
        """Probe audio duration and return a padded timeout in seconds."""
        duration = await AudioFile.duration(path)
        if duration is not None and math.isfinite(duration) and duration > 0:
            return max(
                duration + _PLAYBACK_TIMEOUT_PADDING_S,
                _PLAYBACK_TIMEOUT_DEFAULT_S,
            )
        return _PLAYBACK_TIMEOUT_DEFAULT_S

    async def _spawn_and_wait(
        self,
        cmd: list[str],
        timeout: float,
        env_snapshot: dict[str, str],
        path: Path,
    ) -> tuple[int, float, str] | None:
        """Spawn the player subprocess and wait for completion.

        Return ``(rc, elapsed, stderr)`` on normal completion, or ``None``
        after recording an error result for spawn/timeout failures.
        """
        start = _monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            elapsed = _monotonic() - start
            logger.error(
                "Playback FAILED: binary not found: %s (%s) cmd=%s audio_env=%s",
                cmd[0],
                exc,
                cmd,
                env_snapshot,
            )
            self.record_result(
                path=path,
                rc=-1,
                elapsed=elapsed,
                stderr=f"FileNotFoundError: {exc}",
            )
            return None
        except OSError as exc:
            elapsed = _monotonic() - start
            logger.error(
                "Playback FAILED: OSError spawning %s: %s audio_env=%s",
                cmd[0],
                exc,
                env_snapshot,
            )
            self.record_result(
                path=path, rc=-1, elapsed=elapsed, stderr=f"OSError: {exc}"
            )
            return None

        try:
            _, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except (TimeoutError, asyncio.CancelledError) as exc:
            # Stop the player before queue cleanup releases its source file.
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            elapsed = _monotonic() - start
            logger.error(
                "Playback FAILED: timed out after %.1fs for %s audio_env=%s",
                timeout,
                path.name,
                env_snapshot,
            )
            self.record_result(
                path=path,
                rc=-1,
                elapsed=elapsed,
                stderr=f"timeout after {timeout:.1f}s",
            )
            return None

        elapsed = _monotonic() - start
        rc = proc.returncode if proc.returncode is not None else -1
        raw_stderr = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
        return rc, elapsed, PlaybackResult.truncate_stderr(raw_stderr)

    async def consumer(self) -> None:
        """Single consumer: play audio sequentially from the queue.

        Holds ``self._mutex`` for the duration of each item so the
        direct-play path can't produce overlapping audio from another
        coroutine.
        """
        try:
            while True:
                await self._play_item(await self._queue.get())
        finally:
            while not self._queue.empty():
                self._finish_item(self._queue.get_nowait())

    async def _play_item(self, item: PlaybackItem) -> None:
        """Resolve this item's outcome before dequeuing the next one."""
        logger.debug("Playback start: %s", item.path.name)
        result: PlaybackResult | str = _PLAYBACK_INTERRUPTED
        try:
            async with self._mutex:
                await self.play_audio(item.path)
                result = self._last_result or "no playback result recorded"
            logger.debug("Playback done: %s", item.path.name)
        except Exception:
            logger.exception("Playback consumer error for %s", item.path.name)
            result = "playback failed unexpectedly"
        finally:
            self._finish_item(item, result)

    def _finish_item(
        self,
        item: PlaybackItem,
        result: PlaybackResult | str = _PLAYBACK_INTERRUPTED,
    ) -> None:
        """Settle and release an item even if playback did not finish."""
        try:
            item.finish(result)
        finally:
            self._queue.task_done()

    def record_result(
        self,
        *,
        path: Path,
        rc: int,
        elapsed: float,
        stderr: str,
    ) -> PlaybackResult:
        """Store and return a freshly-observed playback result as ``last_result``."""
        self._last_result = PlaybackResult(
            path=path,
            rc=rc,
            elapsed_s=round(elapsed, 4),
            stderr=stderr,
            ts=time.time(),
        )
        return self._last_result

    # -- Private helpers -----------------------------------------------------
