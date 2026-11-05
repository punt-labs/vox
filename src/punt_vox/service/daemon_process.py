"""Handles on one voxd process, so neither identity nor exit is read by bare pid.

Two handles, one per platform capability:

- :class:`PidfdProcess` (Linux 5.3+) opens ``pidfd_open(pid)`` before it
  checks the command line. The descriptor pins that exact process, so every
  signal sent through it reaches the process that was identified, and a
  recycled pid cannot be reached through it.
- :class:`StartTimeProcess` (macOS, or a Linux kernel without pidfd) records
  the pid's start time and command line and re-reads both immediately before
  each signal. A residual window of a few milliseconds remains between that
  re-read and ``os.kill``; the platform offers no way to close it.

:class:`DaemonProcess` picks the handle; callers never branch on the platform.
"""

from __future__ import annotations

import errno
import logging
import os
import select
import signal
import sys
import weakref
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.service.backend import PortProbeError
from punt_vox.service.clock import Poll, SystemClock
from punt_vox.service.signal_escalation import SignalEscalation

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.service.backend import Clock, ProcessHandle
    from punt_vox.service.process_identity import ProcessIdentity
    from punt_vox.service.termination import Termination

logger = logging.getLogger(__name__)

__all__ = ["DaemonProcess", "PidfdProcess", "StartTimeProcess"]


type IdentityReader = Callable[[int], ProcessIdentity | None]
"""Read a pid's identity; ``None`` when the pid is gone."""


@final
class PidfdProcess:
    """A voxd process pinned by a Linux pidfd."""

    __slots__ = ("__weakref__", "_fd", "_pid", "_seen")

    _fd: int
    _pid: int
    _seen: ProcessIdentity

    @classmethod
    def open(cls, pid: int, identity: IdentityReader) -> Self | None:
        """Pin *pid*; return ``None`` when it is gone or is not voxd.

        Raises ``OSError`` (``ENOSYS``) when the kernel or the interpreter has
        no pidfd support, so the caller can fall back to the start-time handle.
        """
        if sys.platform != "linux":
            raise OSError(errno.ENOSYS, "pidfd_open needs Linux")
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return None
        except AttributeError as exc:
            # An interpreter built against a libc without pidfd_open lacks the
            # function even on a new kernel: the same fallback as ENOSYS.
            raise OSError(errno.ENOSYS, "this Python has no os.pidfd_open") from exc
        self = super().__new__(cls)
        self._fd = fd
        self._pid = pid
        weakref.finalize(self, os.close, fd)
        seen = identity(pid)
        # The pidfd was opened first: if the process is still alive now, the
        # identity just read belongs to the pinned process, not to a successor
        # that inherited its number.
        if seen is None or self.exited() or not seen.is_voxd():
            return None
        self._seen = seen
        return self

    def __eq__(self, other: object) -> bool:
        """Equal when both pin the same process: same pid, same identity."""
        if not isinstance(other, PidfdProcess):
            return NotImplemented
        return (self._pid, self._seen) == (other._pid, other._seen)

    def __hash__(self) -> int:
        return hash((PidfdProcess, self._pid, self._seen))

    @property
    def pid(self) -> int:
        """Return the pinned process id."""
        return self._pid

    def exited(self) -> bool:
        """Return True once the pinned process has exited."""
        return self._wait(0.0)

    def terminate(self, within_s: float) -> Termination:
        """SIGTERM through the pidfd, SIGKILL after the grace; say what came of it.

        The whole call takes at most *within_s* seconds.
        """
        return SignalEscalation(self._pid, self._send, self._wait).run(within_s)

    def _send(self, sig: signal.Signals) -> bool:
        """Signal the pinned process; return False when permission is denied.

        Any other failure raises ``PortProbeError``: the caller reports it
        with what it had already done, never as a traceback.
        """
        if sys.platform != "linux":
            raise OSError(errno.ENOSYS, "pidfd_send_signal needs Linux")
        try:
            signal.pidfd_send_signal(self._fd, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            logger.warning("No permission to signal voxd pid %d", self._pid)
            return False
        except OSError as exc:
            msg = f"cannot signal voxd pid {self._pid}: {exc}"
            raise PortProbeError(msg) from exc
        return True

    def _wait(self, timeout: float) -> bool:
        """Return True when the pidfd turns readable (the process exited)."""
        try:
            readable, _, _ = select.select([self._fd], [], [], timeout)
        except OSError as exc:
            msg = f"cannot wait on voxd pid {self._pid}: {exc}"
            raise PortProbeError(msg) from exc
        return bool(readable)


@final
class StartTimeProcess:
    """A voxd process identified by pid, start time and command line."""

    __slots__ = ("_identity", "_pid", "_poll", "_seen")

    _POLL_S: ClassVar[float] = 0.1

    _pid: int
    _seen: ProcessIdentity
    _identity: IdentityReader
    _poll: Poll

    @classmethod
    def open(cls, pid: int, identity: IdentityReader, clock: Clock) -> Self | None:
        """Record *pid*'s identity; return ``None`` when it is gone or not voxd."""
        seen = identity(pid)
        if seen is None or not seen.is_voxd():
            return None
        self = super().__new__(cls)
        self._pid = pid
        self._seen = seen
        self._identity = identity
        self._poll = Poll(clock, cls._POLL_S)
        return self

    @property
    def pid(self) -> int:
        """Return the recorded process id."""
        return self._pid

    def __eq__(self, other: object) -> bool:
        """Equal when both identify the same process: pid and identity."""
        if not isinstance(other, StartTimeProcess):
            return NotImplemented
        return (self._pid, self._seen) == (other._pid, other._seen)

    def __hash__(self) -> int:
        return hash((StartTimeProcess, self._pid, self._seen))

    def exited(self) -> bool:
        """Return True when the pid is gone or now names a different process."""
        return self._identity(self._pid) != self._seen

    def terminate(self, within_s: float) -> Termination:
        """SIGTERM, then SIGKILL after the grace, each after a re-check.

        The whole call takes at most *within_s* seconds.
        """
        return SignalEscalation(self._pid, self._send, self._wait).run(within_s)

    def _wait(self, timeout: float) -> bool:
        """Poll the identity until the process is gone or *timeout* passes."""
        return self._poll.until(self.exited, timeout)

    def _send(self, sig: signal.Signals) -> bool:
        """Signal the pid only if it is still the identified process.

        Returns False when permission is denied; any other failure raises
        ``PortProbeError``.
        """
        if self.exited():
            return True
        try:
            os.kill(self._pid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            logger.warning("No permission to signal voxd pid %d", self._pid)
            return False
        except OSError as exc:
            msg = f"cannot signal voxd pid {self._pid}: {exc}"
            raise PortProbeError(msg) from exc
        return True


@final
class DaemonProcess:
    """Open the strongest handle this platform offers on a voxd pid."""

    __slots__ = ("_clock", "_identity")

    _clock: Clock
    _identity: IdentityReader

    def __new__(cls, clock: Clock, identity: IdentityReader) -> Self:
        self = super().__new__(cls)
        self._clock = clock
        self._identity = identity
        return self

    @classmethod
    def on_this_host(cls, identity: IdentityReader) -> Self:
        """Return the opener that waits on real time."""
        return cls(SystemClock(), identity)

    def open(self, pid: int) -> ProcessHandle | None:
        """Return a handle on *pid*; ``None`` when it is gone or is not voxd."""
        if sys.platform == "linux":
            try:
                return PidfdProcess.open(pid, self._identity)
            except OSError as exc:
                if exc.errno != errno.ENOSYS:
                    msg = f"cannot pin voxd pid {pid}: {exc}"
                    raise PortProbeError(msg) from exc
        return StartTimeProcess.open(pid, self._identity, self._clock)
