"""Observe voxd on its port: the processes holding it and who answers health.

Only the service port (``DEFAULT_PORT``) counts toward the lifecycle. A voxd on
another port -- a foreground ``voxd --port 9000`` that left a stale
``serve.port`` -- is named so the operator knows about it, and is never
signalled.

The probe fails closed: when ``fuser``, ``lsof`` or ``ps`` cannot run, or a
connection fails for any reason but a refusal, ``PortProbeError`` propagates;
it is never read as "nothing there".

Two doors send the token, and each has its own gate:

- :meth:`DaemonProbe.health` sends it to a voxd ``lsof``/``fuser`` attributed
  to the port, re-attributed immediately before the request.
- :meth:`DaemonProbe.published_health` sends it before any attribution, so a
  slow ``lsof`` cannot hide a voxd that answers. Its gate
  (:class:`~punt_vox.service.published_health.PublishedHealth`) is the file
  voxd itself publishes: ``serve.port`` must name the service port, be owned
  by the calling user (the run directory is mode 0700, so another user
  cannot plant it), the target host must be loopback, and the port must
  accept a connection. The token goes out through no other door, and not
  when any part of that gate fails.
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.service.attributed_health import AttributedHealth
from punt_vox.service.backend import PortProbeError
from punt_vox.service.clock import Poll, SystemClock
from punt_vox.service.deadline import Deadline
from punt_vox.service.health import HealthTarget
from punt_vox.service.health_request import HealthRequest
from punt_vox.service.port_file_cleanup import PortFileCleanup
from punt_vox.service.process import DEFAULT_PORT, ProcessManager
from punt_vox.service.published_health import PublishedHealth
from punt_vox.service.service_cgroup import ServiceCgroup

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.service.backend import ProcessHandle, ServiceBackend
    from punt_vox.types_health import HealthStatus

__all__ = ["DaemonProbe"]


@final
class DaemonProbe:
    """The real :class:`~punt_vox.service.backend.LifecycleProbe`."""

    __slots__ = ("_deadline", "_miss", "_poll", "_process_mgr", "_target")

    _CONNECT_TIMEOUT_S: ClassVar[float] = 0.5

    _process_mgr: ProcessManager
    _target: Callable[[], HealthTarget]
    _poll: Poll
    _miss: Exception | None
    _deadline: Deadline

    def __new__(
        cls,
        process_mgr: ProcessManager,
        target: Callable[[], HealthTarget],
        poll: Poll,
    ) -> Self:
        """Create a daemon probe."""
        # *target* is resolved on use, not here: the bind is read from the
        # installed unit, and a read failure belongs inside the verb's report.
        self = super().__new__(cls)
        self._process_mgr = process_mgr
        self._target = target
        self._poll = poll
        self._miss = None
        self._deadline = Deadline.never()
        return self

    @classmethod
    def for_backend(cls, backend: ServiceBackend) -> Self:
        """Return the probe for the daemon *backend* runs, on its installed bind."""
        return cls(
            ProcessManager(),
            lambda: HealthTarget(backend.installed_bind()),
            Poll(SystemClock()),
        )

    def within(self, deadline: Deadline) -> DaemonProbe:
        """Return this probe with every check bounded by *deadline*.

        Each port, process and connect check finishes by *deadline*; one
        started after it raises ``PortProbeError``. The health request takes
        its bound from the caller.
        """
        bounded = DaemonProbe(
            self._process_mgr.within(deadline), self._target, self._poll
        )
        bounded._deadline = deadline
        return bounded

    @property
    def target(self) -> HealthTarget:
        """Return the health target the probe sends its gated request to."""
        return self._target()

    @property
    def poll(self) -> Poll:
        """Return the bounded poll this probe waits with."""
        return self._poll

    def voxd_processes(self) -> frozenset[ProcessHandle]:
        """Return pinned handles on the visible voxd processes on the port."""
        mgr = self._process_mgr
        handles = (mgr.open_voxd(pid) for pid in mgr.find_pid_on_port(DEFAULT_PORT))
        return frozenset(h for h in handles if h is not None)

    def off_port_voxd(self) -> frozenset[int]:
        """Return voxd pids on the port ``serve.port`` names, if not the service's."""
        mgr = self._process_mgr
        port = mgr.read_port_file()
        if port is None or port == DEFAULT_PORT:
            return frozenset()
        pids = mgr.find_pid_on_port(port)
        return frozenset(filter(mgr.is_vox_daemon_process, pids))

    def open_handle(self, pid: int) -> ProcessHandle | None:
        """Return a handle on *pid*; ``None`` when it is gone or is not voxd."""
        return self._process_mgr.open_voxd(pid)

    def service_owned(self, pid: int) -> bool:
        """Return True when systemd counts *pid* in voxd.service's cgroup.

        ``False`` when the process is gone or the host has no ``/proc``
        (macOS, where launchd keeps no cgroup). Any other read failure raises.

        Raises:
            PortProbeError: If process or port ownership cannot be determined safely.
        """
        return ServiceCgroup.counts(pid)

    def clear_port_file(self) -> PortFileCleanup:
        """Remove a stale ``serve.port`` that names the service port.

        Declined (``HELD``) while a visible voxd holds the port, since the
        file may be that voxd's. Removed when it names the service port or
        does not parse (a crash leftover). Returns what it did; raises
        ``PortProbeError`` when the file cannot be read or removed.
        """
        if self.voxd_processes():
            return PortFileCleanup.HELD
        return self._process_mgr.discard_port_file(DEFAULT_PORT)

    def port_listening(self) -> bool:
        """Return True when anything accepts a TCP connection on the port.

        A plain connect with no payload and no token: it sees a listener even
        when its owner is another user, whom ``fuser``/``lsof`` cannot see.
        Only a refusal means "nothing listens"; a timeout, reset or
        unreachable host raises ``PortProbeError``.

        Raises:
            PortProbeError: If process or port ownership cannot be determined safely.
        """
        return self._listening_at(self._target(), self._deadline)

    def _listening_at(self, target: HealthTarget, deadline: Deadline) -> bool:
        """Connect once to *target* within *deadline*; see :meth:`port_listening`."""
        address = (target.host, target.port)
        timeout = deadline.cap(
            self._CONNECT_TIMEOUT_S, f"cannot connect to {address[0]}:{address[1]}"
        )
        try:
            with socket.create_connection(address, timeout=timeout):
                return True
        except ConnectionRefusedError:
            return False
        except OSError as exc:
            msg = f"cannot connect to {address[0]}:{address[1]}: {exc}"
            raise PortProbeError(msg) from exc

    def published_health(self, within_s: float) -> HealthStatus | None:
        """Return voxd's health through the port it published, else ``None``.

        The gate is :class:`PublishedHealth`'s, stated in the module docstring
        above. The bound starts here: reading the installed bind, the connect
        check and the request all spend from *within_s*.
        """
        request = PublishedHealth(
            self._process_mgr.port_file,
            self._target,
            self._listening_at,
            Deadline(self._poll.now, within_s),
        )
        health = request.ask()
        self._miss = request.miss
        return health

    def health(
        self, holders: frozenset[ProcessHandle], within_s: float
    ) -> HealthStatus | None:
        """Return voxd's health; send the token only to a visible voxd.

        The gate is :class:`AttributedHealth`'s: with no visible voxd holding
        the port, no connection is made, and the holders are attributed again
        immediately before the token goes out. The request, connect included,
        is cut off after *within_s* seconds, so a hung voxd cannot hold a wait
        past its deadline; the re-check spends from that bound first. ``None``
        is the documented answer for "no health reply"; :meth:`health_miss`
        says why.
        """
        request = AttributedHealth(
            holders,
            self.voxd_processes,
            self._target,
            Deadline(self._poll.now, within_s),
        )
        health = request.ask()
        self._miss = request.miss
        return health

    def health_miss(self) -> Exception | None:
        """Return the last health failure; ``None`` when none has failed."""
        return self._miss

    def wait_up(self, deadline_s: float) -> HealthStatus | None:
        """Poll gated health until voxd answers; ``None`` if it never does.

        Each request is bounded by what is left of *deadline_s*. The last
        sliver, below a request's floor, is not spent on a request that could
        only time out: the last real miss stays as :meth:`health_miss`.
        """
        return self._poll.first(self._ask_with, deadline_s)

    def _ask_with(self, left: float) -> HealthStatus | None:
        """One poll step: ask the visible voxd when *left* is worth a request."""
        if left < HealthRequest.MIN_S:
            return None
        return self.health(self.voxd_processes(), left)
