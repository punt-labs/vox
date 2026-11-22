"""Platform audio tools for voxd playback, and diagnostics when it fails."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import platform
import subprocess
from typing import TYPE_CHECKING, ClassVar, final

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["AudioContext", "AudioFile"]

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 5.0


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


@final
class AudioFile:
    """Play and measure one audio file with this platform's command-line tools."""

    __slots__ = ()

    @staticmethod
    def player_command(path: Path) -> list[str]:
        """Return the argv for playing ``path`` on this platform.

        No ``-loglevel quiet`` on ffplay -- we want its stream summary and errors.
        """
        if _is_darwin():
            return ["afplay", "-v", "0.8", str(path)]
        return ["ffplay", "-nodisp", "-autoexit", "-volume", "80", str(path)]

    @staticmethod
    async def duration(path: Path) -> float | None:
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
