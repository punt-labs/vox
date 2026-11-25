"""Doctor checks answered by the running daemon: its microphone and allowlist.

Both run inside voxd, not in the doctor process, because a call does: the
daemon's process is the one that needs the microphone grant and the one
that reads the worker allowlist. The doctor only frames the request and
turns the reply into a line.
"""

from __future__ import annotations

import asyncio
from pathlib import PurePath
from typing import Self, cast, final

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdProtocolError,
    VoxdRefusedError,
)
from punt_vox.daemon_control import DaemonControl
from punt_vox.doctor_result import OK, WARN, CheckResult
from punt_vox.types_mic import MicProbeReport

__all__ = ["MicCaptureCheck", "WorkerReposCheck"]

_MAC_SILENCE_FIX = (
    "the daemon hears silence; grant the daemon's process the microphone in "
    "System Settings, Privacy & Security, Microphone, or run voxd from a "
    "terminal that holds the grant"
)
_SILENCE_FIX = (
    "the daemon captured silence: check the device and the audio server's "
    "permissions for voxd's user"
)


@final
class _DaemonAnswer:
    """One short-lived daemon request, with connection failures as text."""

    __slots__ = ("_family", "_operation", "_timeout")
    _family: str
    _operation: str
    _timeout: float

    def __new__(cls, family: str, operation: str, *, timeout: float) -> Self:
        """Bind the RPC the check asks for."""
        self = super().__new__(cls)
        self._family = family
        self._operation = operation
        self._timeout = timeout
        return self

    def fetch(self) -> object:
        """Return the daemon's result.

        Raises:
            VoxdConnectionError: If the daemon is unreachable.
            VoxdProtocolError: If the daemon refuses or answers something else.
        """
        return asyncio.run(
            DaemonControl(self._family).request(self._operation, timeout=self._timeout)
        )

    @staticmethod
    def reason(exc: VoxdConnectionError | VoxdProtocolError) -> str:
        """Return the one-line reason a doctor row shows for *exc*.

        A daemon that does not know the message type is serving code older
        than this doctor; the fix is a restart, so the row says so.
        """
        if isinstance(exc, VoxdRefusedError):
            return "daemon not running"
        if str(exc).startswith("unknown message type"):
            return (
                f"{exc}; the running daemon predates this check, run vox daemon restart"
            )
        return str(exc)


@final
class MicCaptureCheck:
    """Ask voxd to open its default input device for one second and listen."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create the microphone capture check."""
        return super().__new__(cls)

    def run(self) -> CheckResult:
        """Report signal, silence, no audio, an unopenable device, or no daemon."""
        answer = _DaemonAnswer("mic", "probe", timeout=15)
        try:
            report = MicProbeReport.from_wire(answer.fetch())
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            return self._warn(answer.reason(exc))
        except ValueError as exc:
            return self._warn(str(exc))
        heard = (
            f"{report.device}, {report.sample_rate_hz} Hz, peak {report.peak}"
            f" over {report.samples} samples"
        )
        if report.overflows:
            heard += f", {report.overflows} overflows, audio dropped"
        if not report.delivered:
            return self._warn(f"{heard} — the device delivered no audio in one second")
        if report.signal:
            return CheckResult.of(OK, f"Microphone (voxd): {heard}", required=False)
        return self._warn(f"{heard} — {self._silence_fix(report.platform)}")

    @staticmethod
    def _silence_fix(platform: str) -> str:
        """Return the silence advice for the daemon's *platform*, not the CLI's.

        The doctor may run on another machine than voxd; the device, and the
        grant that exists on macOS only, are where the daemon is.
        """
        return _MAC_SILENCE_FIX if platform == "darwin" else _SILENCE_FIX

    @staticmethod
    def _warn(reason: str) -> CheckResult:
        return CheckResult.of(WARN, f"Microphone (voxd): {reason}", required=False)


@final
class WorkerReposCheck:
    """Read the worker repository allowlist the daemon enforces."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create the worker repositories check."""
        return super().__new__(cls)

    def run(self) -> CheckResult:
        """Report the allowlisted repositories, or how to add one."""
        answer = _DaemonAnswer("worker", "repos", timeout=10)
        try:
            repos = self._paths(answer.fetch())
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            return self._warn(answer.reason(exc))
        except ValueError as exc:
            return self._warn(str(exc))
        if not repos:
            return self._warn(
                "worker_repos is empty: no repository is allowlisted;"
                " run vox worker allow <path>"
            )
        names = ", ".join(PurePath(repo).name or repo for repo in repos)
        return CheckResult.of(
            OK,
            f"Worker repositories: {len(repos)} allowlisted ({names})",
            required=False,
        )

    @staticmethod
    def _paths(raw: object) -> tuple[str, ...]:
        """Narrow the wire reply to its path strings.

        Raises:
            ValueError: If the daemon answered anything but a list of strings.
        """
        if not isinstance(raw, list):
            raise ValueError("worker_repos reply is not a list of paths")
        # Wire boundary: JSON yields object until each item is narrowed here.
        items = cast("list[object]", raw)
        if not all(isinstance(item, str) for item in items):
            raise ValueError("worker_repos reply is not a list of paths")
        return tuple(str(item) for item in items)

    @staticmethod
    def _warn(reason: str) -> CheckResult:
        return CheckResult.of(WARN, f"Worker repositories: {reason}", required=False)
