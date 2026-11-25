"""The service-manager seam the daemon lifecycle drives, and its typed errors.

A types module: the Protocols here are the contracts ``DaemonLifecycle``
depends on, and the exceptions are the failures it classifies. Nothing here
runs a command or touches a process, so importing it pulls in no backend.

Every failure a backend command can produce surfaces as one of three typed
errors. The lifecycle never reads a command's exit code as the outcome by
itself: after any of these it observes the service again and carries on when
the postcondition already holds.

:class:`ListenAddress` is the one value here: the local addresses a listener
must carry to serve the host a health request reaches, which the probe's
holder check matches on both platforms. Pure computation, no I/O.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
from typing import TYPE_CHECKING, Protocol, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from punt_vox.service.deadline import Deadline
    from punt_vox.service.port_file_cleanup import PortFileCleanup
    from punt_vox.service.query_answer import QueryAnswer
    from punt_vox.service.termination import Termination
    from punt_vox.types_health import HealthStatus

__all__ = [
    "KILL_GRACE_FLOOR_S",
    "BackendCommandError",
    "Clock",
    "CommandRunner",
    "InstallableBackend",
    "LifecycleProbe",
    "ListenAddress",
    "PortProbeError",
    "PrivilegeDeniedError",
    "ProcessHandle",
    "ServiceBackend",
    "TerminalCheck",
]


class ServiceBackend(Protocol):
    """What the lifecycle asks of a service manager (systemd or launchd)."""

    def is_installed(self) -> bool:
        """Return True when the unit file or plist is present."""
        ...

    def is_active(self) -> bool:
        """Return True while the manager runs, starts, or is stopping the job."""
        ...

    def main_pid(self) -> int | None:
        """Return the manager's main process id.

        ``None`` is the documented answer when the manager reports no process.
        """
        ...

    def installed_bind(self) -> str:
        """Return the ``VOXD_BIND`` written into the unit; ``""`` when none."""
        ...

    def request_stop(self) -> None:
        """Ask the manager to stop the job; raise a typed error on failure."""
        ...

    def request_start(self) -> None:
        """Ask the manager to start the job; raise a typed error on failure."""
        ...


class InstallableBackend(ServiceBackend, Protocol):
    """A service backend that can also write and remove its unit."""

    @property
    def service_path(self) -> str:
        """Return the unit file or plist path this backend installs."""
        ...

    def is_active_within(self, within_s: float) -> QueryAnswer:
        """Ask the manager, within *within_s*, whether the job is active.

        The query takes at most *within_s*, for a bounded wait. One that runs
        out of time confirms nothing: the answer does not hold and names the
        command that did not answer. A failed query raises.
        """
        ...

    def install(self, user: str) -> None:
        """Write the unit for *user* and bring the job up."""
        ...

    def uninstall(self) -> None:
        """Bring the job down and remove the unit."""
        ...


@final
class ListenAddress:
    """The local addresses a listener on *port* may have to serve *host*.

    A health request goes to the unit's polled host, so a LISTEN socket on the
    port at another address is not the one it reaches: voxd on 127.0.0.1 and a
    foreign process on [::1] are different listeners. The exact address, the
    family's wildcard (which serves it), and, for an IPv4 host, the v4-mapped
    form a dual-stack socket shows, are the forms that count.
    """

    __slots__ = ("_host", "_port")

    _host: ipaddress.IPv4Address | ipaddress.IPv6Address
    _port: int

    def __new__(cls, host: str, port: int) -> Self:
        """Create the address for *host* (an IP literal) on *port*."""
        self = super().__new__(cls)
        self._host = ipaddress.ip_address(host)
        self._port = port
        return self

    @property
    def port(self) -> int:
        """Return the port."""
        return self._port

    def lsof_matches(self, family: str, name: str) -> bool:
        """Return True when an ``lsof -Fnt`` socket (*family*, *name*) serves the host.

        *family* is lsof's type field, ``IPv4`` or ``IPv6``. A wildcard
        listener counts only in the host's own family: uvicorn sets
        ``IPV6_V6ONLY``, so ``[::]`` does not serve 127.0.0.1, and ``0.0.0.0``
        never serves ``::1``. An IPv4 host is also served by its v4-mapped
        form on an IPv6 socket.
        """
        port = self._port
        if self._host.version == 6:
            return name == f"[{self._host}]:{port}" or (
                family == "IPv6" and name == f"*:{port}"
            )
        return name in {f"{self._host}:{port}", f"[::ffff:{self._host}]:{port}"} or (
            family == "IPv4" and name == f"*:{port}"
        )

    def proc_forms(self, table: str) -> frozenset[str]:
        """Return the ``local_address`` hex forms that match in ``/proc/net/<table>``.

        A wildcard counts only in the host's own family (``IPV6_V6ONLY``:
        ``[::]`` does not serve an IPv4 host); an IPv4 host is also served by
        its v4-mapped form in ``tcp6``. The wildcard is written as the
        integer zero address, never as a literal: nothing here binds.
        """
        hexport = f"{self._port:04X}"
        if self._host.version == 6:
            if table == "tcp":
                return frozenset()
            hosts6 = (self._host, ipaddress.IPv6Address(0))
            return frozenset(f"{self._hex6(h)}:{hexport}" for h in hosts6)
        if table == "tcp":
            hosts = (self._host, ipaddress.IPv4Address(0))
            return frozenset(f"{self._hex4(h)}:{hexport}" for h in hosts)
        mapped = ipaddress.IPv6Address(f"::ffff:{self._host}")
        return frozenset({f"{self._hex6(mapped)}:{hexport}"})

    @staticmethod
    def _hex4(host: ipaddress.IPv4Address) -> str:
        """Return *host* as ``/proc/net/tcp`` prints it: one little-endian word."""
        (word,) = struct.unpack("<I", socket.inet_pton(socket.AF_INET, str(host)))
        return f"{word:08X}"

    @staticmethod
    def _hex6(host: ipaddress.IPv6Address) -> str:
        """Return *host* as ``/proc/net/tcp6`` prints it: four little-endian words."""
        words = struct.unpack("<IIII", socket.inet_pton(socket.AF_INET6, str(host)))
        return "".join(f"{word:08X}" for word in words)


KILL_GRACE_FLOOR_S = 1.0
"""The shortest SIGTERM grace worth a SIGKILL after it.

A ``terminate()`` budget below this sends SIGTERM only: SIGKILL is never sent
without a real chance to exit cleanly first.
"""


class ProcessHandle(Protocol):
    """A handle pinned to one voxd process, never to a bare, reusable pid."""

    @property
    def pid(self) -> int:
        """Return the process id the handle was opened on."""
        ...

    def exited(self) -> bool:
        """Return True once the pinned process has exited."""
        ...

    def terminate(self, within_s: float) -> Termination:
        """SIGTERM, then SIGKILL after a grace; return what that came to.

        Takes at most *within_s* seconds, so a caller's deadline holds. Below
        ``KILL_GRACE_FLOOR_S`` it sends SIGTERM only. A signal failure other
        than "gone" or "permission denied" raises ``PortProbeError``.
        """
        ...


class LifecycleProbe(Protocol):
    """What the lifecycle observes about voxd on its port."""

    def within(self, deadline: Deadline) -> LifecycleProbe:
        """Return this probe with every port and process check bounded.

        Each check finishes by *deadline*; one started after it raises
        ``PortProbeError``. The health request takes its own ``within_s``.
        """
        ...

    def voxd_processes(self) -> frozenset[ProcessHandle]:
        """Return handles on the visible voxd processes holding the port."""
        ...

    def off_port_voxd(self) -> frozenset[int]:
        """Return pids of voxd processes on a port other than the service's."""
        ...

    def open_handle(self, pid: int) -> ProcessHandle | None:
        """Return a handle on *pid*.

        ``None`` is the documented answer when *pid* is gone or is not voxd.
        """
        ...

    def service_owned(self, pid: int) -> bool:
        """Return True when the service manager still counts *pid* as its own."""
        ...

    def clear_port_file(self) -> PortFileCleanup:
        """Remove a stale ``serve.port`` naming the service port; say what it did.

        Declined while a visible voxd holds the port; an unparseable file is
        stale and removed. Raises ``PortProbeError`` when it cannot be removed.
        """
        ...

    def port_listening(self) -> bool:
        """Return True when anything accepts a TCP connection on the port."""
        ...

    def published_health(self, within_s: float) -> HealthStatus | None:
        """Return voxd's health through the port it published, else ``None``.

        Sent before the port is attributed, and only when this user's own
        ``serve.port`` names the service port and its ``serve.pid`` a live
        voxd of this user that still holds the listener, the target is
        loopback, and something accepts a connection there. Takes at most
        *within_s* seconds. ``None`` is the documented answer for "not asked" and "no
        reply"; :meth:`health_miss` says why.
        """
        ...

    def health(
        self, holders: frozenset[ProcessHandle], within_s: float
    ) -> HealthStatus | None:
        """Return voxd's health, sent only when *holders* is non-empty.

        The request, connect included, takes at most *within_s* seconds.
        ``None`` is the documented answer for "no health reply".
        """
        ...

    def wait_up(self, deadline_s: float) -> HealthStatus | None:
        """Poll gated health until a visible voxd answers or *deadline_s* passes.

        Each request is bounded by what is left of the deadline, so a hung
        voxd cannot hold the wait past it. ``None`` is the documented answer
        when nothing answered in time; :meth:`health_miss` says why.
        """
        ...

    def health_miss(self) -> Exception | None:
        """Return why the last health request got no answer.

        ``None`` is the documented answer when no request has failed.
        """
        ...


class Clock(Protocol):
    """Monotonic time and sleep, so a poll can run on a fake clock."""

    def monotonic(self) -> float:
        """Return seconds on a monotonic clock."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block for *seconds*."""
        ...


class CommandRunner(Protocol):
    """Run one argv and report its exit code and captured stderr."""

    def __call__(self, argv: Sequence[str], *, capture: bool) -> tuple[int, str]:
        """Run *argv*; capture stderr when *capture*, else let it reach the tty."""
        ...


type TerminalCheck = Callable[[], bool]
"""Answer whether the process has a controlling terminal to prompt on."""


@final
class BackendCommandError(RuntimeError):
    """A service-manager command failed: its argv, exit code and stderr tail.

    Built through :meth:`of`, so the message is rendered once and the command
    and exit code stay available to the caller that classifies the failure.
    """

    __slots__ = ("_argv", "_code")

    _argv: tuple[str, ...]
    _code: int

    @classmethod
    def of(cls, argv: Sequence[str], code: int, detail: str) -> Self:
        """Return the error for *argv* exiting *code*, with *detail* appended."""
        self = cls(f"{' '.join(argv)} exited {code}: {detail}")
        self._argv = tuple(argv)
        self._code = code
        return self

    @property
    def code(self) -> int:
        """Return the command's exit code."""
        return self._code

    @property
    def argv(self) -> tuple[str, ...]:
        """Return the command that failed."""
        return self._argv


@final
class PrivilegeDeniedError(PermissionError):
    """The command needed a privilege vox could not get without a prompt."""

    __slots__ = ()


@final
class PortProbeError(RuntimeError):
    """``fuser``, ``lsof`` or ``ps`` was missing, failed, or timed out.

    A broken probe never reads as "nothing on the port".
    """

    __slots__ = ()
