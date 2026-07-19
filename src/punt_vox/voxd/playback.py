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
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Self

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Audio environment variables we capture for every playback. These determine
# whether ffplay can reach PulseAudio/PipeWire and dbus at the moment of the
# call, which is exactly the failure mode we saw on Linux in v4.0.3.
_AUDIO_ENV_KEYS: tuple[str, ...] = (
    "XDG_RUNTIME_DIR",
    "PULSE_SERVER",
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "HOME",
    "USER",
)

# Playback under 50ms is a "success" that almost certainly played nothing.
_SUSPICIOUS_ELAPSED_S = 0.05

_PLAYBACK_TIMEOUT_DEFAULT_S = 120.0
_PLAYBACK_TIMEOUT_PADDING_S = 10.0
_PROBE_TIMEOUT_S = 5.0

# Cap on the stderr blob we keep per playback. ffplay without -loglevel
# quiet can emit kilobytes of progress lines; we want enough for triage
# without unbounded growth in memory or log files.
_MAX_STDERR_LEN = 2000


# ---------------------------------------------------------------------------
# PlaybackItem dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlaybackResult:
    """Outcome of a single audio playback."""

    path: Path
    rc: int
    elapsed_s: float
    stderr: str
    ts: float

    def to_health_dict(self) -> dict[str, object]:
        """Serialize for the health endpoint JSON payload."""
        return {
            "file": str(self.path),
            "rc": self.rc,
            "elapsed_s": self.elapsed_s,
            "stderr": self.stderr,
            "ts": self.ts,
        }


@dataclass(frozen=True, slots=True)
class PlaybackItem:
    """An item in the playback queue."""

    path: Path
    request_id: str
    notify: asyncio.Event


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def _truncate_stderr(text: str) -> str:
    """Return ``text`` clipped to ``_MAX_STDERR_LEN`` with head + tail kept."""
    if len(text) <= _MAX_STDERR_LEN:
        return text
    half = _MAX_STDERR_LEN // 2
    dropped = len(text) - _MAX_STDERR_LEN
    return f"{text[:half]}\n... [truncated {dropped} bytes] ...\n{text[-half:]}"


def _monotonic() -> float:
    """Indirection for ``time.monotonic`` so tests can stub playback timing.

    Patching ``time.monotonic`` directly would also affect asyncio internals.
    """
    return time.monotonic()


def _snapshot_env(keys: tuple[str, ...]) -> dict[str, str]:
    """Return a dict of env var values, using <unset> for missing keys."""
    return {k: os.environ.get(k, "<unset>") for k in keys}


def _is_darwin() -> bool:
    """Return True on macOS.

    Wrapped in a function so mypy doesn't narrow ``sys.platform`` to a
    single value at the call site, which would mark the non-matching
    branch as unreachable for cross-platform development.
    """
    return platform.system() == "Darwin"


def _player_binary_name() -> str:
    """Return the platform player binary name."""
    return "afplay" if _is_darwin() else "ffplay"


def _player_binary_path() -> str | None:  # pyright: ignore[reportUnusedFunction]
    """Return the resolved path to the platform player binary, or None."""
    return shutil.which(_player_binary_name())


def _player_command(path: Path) -> list[str]:
    """Return the argv for playing ``path`` on this platform.

    No ``-loglevel quiet`` on ffplay -- we want its stream summary and errors.
    """
    if _is_darwin():
        return ["afplay", str(path)]
    return ["ffplay", "-nodisp", "-autoexit", str(path)]


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

    __slots__ = ("_last_result", "_mutex", "_queue")

    _last_result: PlaybackResult | None
    _mutex: asyncio.Lock
    _queue: asyncio.Queue[PlaybackItem]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._queue = asyncio.Queue()
        self._mutex = asyncio.Lock()
        self._last_result = None
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

    def set_last_result(self, value: PlaybackResult | None) -> None:
        """Set the last playback result. Used by delegation and tests."""
        self._last_result = value

    async def play_audio(self, path: Path) -> None:
        """Play an audio file and record a rich result in ``last_result``.

        Coordinates file validation, timeout computation, subprocess lifecycle,
        and result logging. Each concern is handled by a focused private method.
        """
        cmd = _player_command(path)
        env_snapshot = _snapshot_env(_AUDIO_ENV_KEYS)

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
        self._record_result(path=path, rc=rc, elapsed=elapsed, stderr=stderr_text)
        self._log_result(path, rc, elapsed, size, stderr_text, cmd, env_snapshot)

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
        return rc, elapsed, _truncate_stderr(raw_stderr)

    def _log_result(
        self,
        path: Path,
        rc: int,
        elapsed: float,
        size: int,
        stderr_text: str,
        cmd: list[str],
        env_snapshot: dict[str, str],
    ) -> None:
        """Interpret a playback result and log at the appropriate level."""
        if rc != 0:
            logger.error(
                "Playback FAILED: rc=%d elapsed=%.3fs file=%s size=%d "
                "cmd=%s audio_env=%s stderr=%r",
                rc,
                elapsed,
                path.name,
                size,
                cmd,
                env_snapshot,
                stderr_text,
            )
            return

        if elapsed < _SUSPICIOUS_ELAPSED_S:
            logger.warning(
                "Playback SUSPICIOUS: rc=0 but elapsed=%.4fs (<%.2fs) file=%s "
                "size=%d audio_env=%s stderr=%r -- probably played nothing",
                elapsed,
                _SUSPICIOUS_ELAPSED_S,
                path.name,
                size,
                env_snapshot,
                stderr_text,
            )
            return

        if stderr_text:
            logger.debug(
                "Playback ok: elapsed=%.3fs file=%s size=%d stderr=%r",
                elapsed,
                path.name,
                size,
                stderr_text,
            )
        else:
            logger.debug(
                "Playback ok: elapsed=%.3fs file=%s size=%d",
                elapsed,
                path.name,
                size,
            )

    async def consumer(self) -> None:
        """Single consumer: play audio sequentially from the queue.

        Holds ``self._mutex`` for the duration of each item so the
        direct-play path can't produce overlapping audio from another
        coroutine.
        """
        while True:
            item = await self._queue.get()
            logger.debug("Playback start: %s", item.path.name)
            async with self._mutex:
                await self.play_audio(item.path)
            logger.debug("Playback done: %s", item.path.name)
            item.notify.set()
            self._queue.task_done()

    # -- Private helpers -----------------------------------------------------

    def _record_result(
        self,
        *,
        path: Path,
        rc: int,
        elapsed: float,
        stderr: str,
    ) -> None:
        """Update last_result with a freshly-observed playback result."""
        self._last_result = PlaybackResult(
            path=path,
            rc=rc,
            elapsed_s=round(elapsed, 4),
            stderr=stderr,
            ts=time.time(),
        )
