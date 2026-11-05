"""Observe voxd on its port: the processes holding it and who answers health.

Only the service port (``DEFAULT_PORT``) counts toward the lifecycle. A voxd on
another port -- a foreground ``voxd --port 9000`` that left a stale
``serve.port`` -- is named so the operator knows about it, and is never
signalled.

The probe fails closed: when ``fuser``, ``lsof`` or ``ps`` cannot run,
``PortProbeError`` propagates; it is never read as "nothing there".
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.service.clock import Poll, SystemClock
from punt_vox.service.health import DaemonNotUpError, HealthTarget
from punt_vox.service.process import DEFAULT_PORT, ProcessManager

if TYPE_CHECKING:
    from punt_vox.service.backend import ProcessHandle
    from punt_vox.types_health import HealthStatus

__all__ = ["DaemonProbe"]


@final
class DaemonProbe:
    """The real :class:`~punt_vox.service.backend.LifecycleProbe`."""

    __slots__ = ("_poll", "_process_mgr", "_target")

    _CONNECT_TIMEOUT_S: ClassVar[float] = 0.5

    _process_mgr: ProcessManager
    _target: HealthTarget
    _poll: Poll

    def __new__(
        cls, process_mgr: ProcessManager, target: HealthTarget, poll: Poll
    ) -> Self:
        self = super().__new__(cls)
        self._process_mgr = process_mgr
        self._target = target
        self._poll = poll
        return self

    @classmethod
    def for_bind(cls, bind: str) -> Self:
        """Return the probe for a daemon the service manager runs on *bind*."""
        return cls(ProcessManager(), HealthTarget(bind), Poll(SystemClock()))

    @property
    def target(self) -> HealthTarget:
        """Return the health target the probe sends its gated request to."""
        return self._target

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
        return frozenset(p for p in pids if mgr.is_vox_daemon_process(p))

    def open_handle(self, pid: int) -> ProcessHandle | None:
        """Return a handle on *pid*; ``None`` when it is gone or is not voxd."""
        return self._process_mgr.open_voxd(pid)

    def port_listening(self) -> bool:
        """Return True when anything accepts a TCP connection on the port.

        A plain connect with no payload and no token: it sees a listener even
        when its owner is another user, whom ``fuser``/``lsof`` cannot see.
        """
        address = (self._target.host, self._target.port)
        try:
            with socket.create_connection(address, timeout=self._CONNECT_TIMEOUT_S):
                return True
        except OSError:
            return False

    def health(self, holders: frozenset[ProcessHandle]) -> HealthStatus | None:
        """Return voxd's health; send the token only to a visible voxd.

        With no visible voxd holding the port, no connection is made. ``None``
        is the documented answer for "no health reply".
        """
        if not holders:
            return None
        try:
            return self._target.client().health()
        except (DaemonNotUpError, VoxdConnectionError, VoxdProtocolError, OSError):
            return None

    def wait_up(self, deadline_s: float) -> HealthStatus | None:
        """Poll gated health until voxd answers; ``None`` if it never does."""
        return self._poll.first(lambda: self.health(self.voxd_processes()), deadline_s)
