"""Playback queue and audio player for voxd."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import platform
import subprocess
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Self, final

from punt_vox.voxd.pcm_output import OutputStreamFactory, sounddevice_output_stream
from punt_vox.voxd.pcm_stream import PcmStreamSink
from punt_vox.voxd.playback_result import PlaybackResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Diagnostic env vars captured in the Linux audio context.  The first five --
# XDG_RUNTIME_DIR, PULSE_SERVER, DBUS_SESSION_BUS_ADDRESS, DISPLAY, WAYLAND_DISPLAY --
# are audio-backend vars ffplay needs to reach PulseAudio/PipeWire and dbus;
# missing values are the v4.0.3 failure mode.  HOME and USER identify which
# user's session the process is running in -- the question that CoreAudio
# session membership turned out to hinge on for the macOS side of this
# diagnostic, and equally worth capturing here.
_LINUX_AUDIO_ENV_KEYS: tuple[str, ...] = (
    "XDG_RUNTIME_DIR",
    "PULSE_SERVER",
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "HOME",
    "USER",
)

_PLAYBACK_TIMEOUT_DEFAULT_S = 120.0
_PLAYBACK_TIMEOUT_PADDING_S = 10.0
_PROBE_TIMEOUT_S = 5.0


# ---------------------------------------------------------------------------
# PlaybackItem dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlaybackItem:
    """An item in the playback queue.

    ``outcome``, when set, is resolved by the consumer with this item's
    :class:`PlaybackResult` the moment its playback finishes -- letting a
    user-invoked ``play`` await the real host-side result rather than returning
    at enqueue. Fire-and-forget callers (say/chime/music) leave it ``None``.
    """

    path: Path
    request_id: str
    notify: asyncio.Event
    outcome: asyncio.Future[PlaybackResult] | None = None


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def _monotonic() -> float:
    """Indirection for ``time.monotonic`` so tests can stub playback timing.

    Patching ``time.monotonic`` directly would also affect asyncio internals.
    """
    return time.monotonic()


def _is_darwin() -> bool:
    """Return True on macOS.

    Wrapped in a function so mypy doesn't narrow ``sys.platform`` to a
    single value at the call site, which would mark the non-matching
    branch as unreachable for cross-platform development.
    """
    return platform.system() == "Darwin"


@final
class AudioContext:
    """Capture platform-specific diagnostics for playback attempts.

    Linux captures the socket variables needed for PulseAudio/PipeWire.
    macOS captures the launchd manager, parent pid, and session id: a Background
    context lacks CoreAudio membership and can make afplay hang or fail.
    The manager result is cached because it cannot change without a re-bootstrap.
    """

    __slots__ = ()

    _MGR_CACHE: ClassVar[str | None] = None
    _MGR_TIMEOUT_S: ClassVar[float] = 2.0

    def snapshot(self) -> dict[str, str]:
        """Return a diagnostic dict for the current platform."""
        if _is_darwin():
            return self._macos_snapshot()
        return self._linux_snapshot()

    def _linux_snapshot(self) -> dict[str, str]:
        """Return audio-backend env vars with ``<unset>`` for missing keys."""
        return {k: os.environ.get(k, "<unset>") for k in _LINUX_AUDIO_ENV_KEYS}

    def _macos_snapshot(self) -> dict[str, str]:
        """Return launchd manager + pid/sid facts that diagnose CoreAudio access.

        ``mgr=Aqua`` is the healthy value (LaunchAgent in the graphical login
        session).  ``mgr=Background`` or ``mgr=System`` means the process is
        outside the Aqua session -- CoreAudio queue starts will fail
        intermittently.  ``ppid`` is expected to be 1 (launchd) when running
        under a proper LaunchAgent; anything else is an orphaned spawn.
        ``sid`` (POSIX session id) rounds out the process-context evidence.
        """
        return {
            "mgr": self._launchctl_manager(),
            "ppid": str(os.getppid()),
            "sid": str(os.getsid(0)),
        }

    def _launchctl_manager(self) -> str:
        """Return ``launchctl managername`` output, cached for the process.

        Cache only on success.  ``"unknown"`` is the absence of an answer, not
        an answer -- caching it would turn one transient launchctl hiccup into
        permanent silence, the same "signal that stops reporting" failure mode
        this diagnostic exists to explain.  A failed probe retries on the next
        playback failure at a cost of at most one extra 2s probe.
        """
        cached = type(self)._MGR_CACHE
        if cached is not None:
            return cached
        name = self._probe_launchctl_manager()
        if name != "unknown":
            type(self)._MGR_CACHE = name
        return name

    @staticmethod
    def _probe_launchctl_manager() -> str:
        """Run ``launchctl managername`` once; return ``unknown`` on failure.

        A missing or misbehaving launchctl must not raise from a diagnostic
        path -- swallow the failure and record ``unknown`` so the log line
        still shows the other session facts.
        """
        try:
            # Absolute path so a launchd-inherited PATH cannot substitute a
            # different binary for ``launchctl`` -- macOS ships it at a stable
            # location, and voxd runs long-lived from launchd where the PATH is
            # not the interactive shell PATH.
            result = subprocess.run(
                ["/bin/launchctl", "managername"],
                capture_output=True,
                text=True,
                timeout=AudioContext._MGR_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        name = result.stdout.strip()
        return name or "unknown"


def _player_command(path: Path) -> list[str]:
    """Return the argv for playing ``path`` on this platform.

    No ``-loglevel quiet`` on ffplay -- we want its stream summary and errors.
    """
    if _is_darwin():
        return ["afplay", "-v", "0.8", str(path)]
    return ["ffplay", "-nodisp", "-autoexit", "-volume", "80", str(path)]


async def _probe_duration(path: Path) -> float | None:
    """Return the duration in seconds of an audio file, or None on failure.

    Uses ffprobe with a 5-second timeout. Returns None if ffprobe is not
    installed, the file is unreadable, or the output is not a valid float.
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        return None
    try:
        stdout_bytes, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_PROBE_TIMEOUT_S
        )
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        return None
    try:
        duration = float((stdout_bytes or b"").strip())
    except ValueError:
        return None
    logger.debug("Probed duration for %s: %.3fs", path.name, duration)
    return duration


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
    async def stream_pcm(self) -> AsyncGenerator[PcmStreamSink]:
        """Own the audio output for one streamed utterance.

        Holds the playback mutex from open to close, so queued speech, chimes
        and direct play wait behind the stream and resume after it, exactly
        as they wait behind a file. The device is released on exit whether
        the utterance played out, was flushed, or the caller raised.
        """
        async with self._mutex:
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
        cmd = _player_command(path)
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
        recorded = self._record_result(
            path=path, rc=rc, elapsed=elapsed, stderr=stderr_text
        )
        recorded.log_outcome(size=size, cmd=cmd, env_snapshot=env_snapshot)

    def _validate_file(self, path: Path) -> int | None:
        """Return file size in bytes, or None after recording an error."""
        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.error("Playback aborted: cannot stat %s: %s", path, exc)
            self._record_result(
                path=path, rc=-1, elapsed=0.0, stderr=f"stat failed: {exc}"
            )
            return None

        if size == 0:
            logger.error(
                "Playback aborted: 0-byte audio file %s -- synthesis bug upstream",
                path,
            )
            self._record_result(path=path, rc=-1, elapsed=0.0, stderr="0-byte file")
            return None

        return size

    async def _compute_timeout(self, path: Path) -> float:
        """Probe audio duration and return a padded timeout in seconds."""
        duration = await _probe_duration(path)
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
            self._record_result(
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
            self._record_result(
                path=path, rc=-1, elapsed=elapsed, stderr=f"OSError: {exc}"
            )
            return None

        try:
            _, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            elapsed = _monotonic() - start
            logger.error(
                "Playback FAILED: timed out after %.1fs for %s audio_env=%s",
                timeout,
                path.name,
                env_snapshot,
            )
            # The player can exit between the timeout firing and the kill, so a
            # ProcessLookupError here is benign -- suppress it to keep
            # play_audio's never-raise contract.
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            self._record_result(
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
        while True:
            item = await self._queue.get()
            logger.debug("Playback start: %s", item.path.name)
            # One bad item must never leave the outcome future or notify unset:
            # that would hang the awaiting play client for its full deadline and,
            # because this consumer is an unsupervised task, kill it -- wedging
            # every later play AND synthesize while the daemon still looks up.
            # play_audio is written never to raise; the try/finally is the
            # backstop for the case it ever does.
            try:
                async with self._mutex:
                    await self.play_audio(item.path)
                logger.debug("Playback done: %s", item.path.name)
                # Resolve the outcome from the result play_audio just recorded,
                # here -- synchronously, before the next item is dequeued -- so
                # an awaiting play handler sees its own item's result, never a
                # later item's (the queue is single-consumer and serialized).
                self._resolve_outcome(item)
            except Exception:
                logger.exception("Playback consumer error for %s", item.path.name)
                self._fail_outcome(item, "playback failed unexpectedly")
            finally:
                # Catch-all in the finally so cancellation (daemon shutdown) or
                # any non-Exception BaseException still resolves the outcome --
                # PlayHandler awaits that future with no timeout, so a pending
                # future would hang the request forever and leak the task. A
                # no-op when the success/error path already resolved it (guards
                # done()).
                self._fail_outcome(item, "playback interrupted (daemon shutting down)")
                item.notify.set()
                self._queue.task_done()

    def _resolve_outcome(self, item: PlaybackItem) -> None:
        """Complete *item*'s outcome future, if any, with the recorded result.

        ``play_audio`` always records a result (success, spawn failure, timeout,
        or bad file), so ``_last_result`` is set here; the ``None`` guard is
        defensive so a missing result surfaces as a failure rather than a hang.
        """
        future = item.outcome
        if future is None or future.done():
            return
        result = self._last_result
        if result is None:
            result = self._failure_result(item.path, "no playback result recorded")
        future.set_result(result)

    def _fail_outcome(self, item: PlaybackItem, reason: str) -> None:
        """Resolve *item*'s outcome with a failure result after a consumer crash."""
        future = item.outcome
        if future is None or future.done():
            return
        future.set_result(self._failure_result(item.path, reason))

    @staticmethod
    def _failure_result(path: Path, reason: str) -> PlaybackResult:
        """Build a failing :class:`PlaybackResult` (rc=-1) carrying *reason*."""
        return PlaybackResult(
            path=path, rc=-1, elapsed_s=0.0, stderr=reason, ts=time.time()
        )

    # -- Private helpers -----------------------------------------------------

    def _record_result(
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
