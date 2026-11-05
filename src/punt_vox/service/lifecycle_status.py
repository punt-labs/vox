"""``vox daemon status`` on the local service: one reading of voxd on its port.

voxd is found the way ``stop`` and ``start`` find it -- the installed bind,
port 8421, a token only from ``serve.token``, sent only to a visible voxd on
the port. One :class:`Deadline`, started when the reading starts, bounds the
whole of it: every port and process probe, the listener check, and the health
request each get only what is left. What the probe sees decides the state:

- a visible voxd answered health: ``running``;
- a visible voxd did not answer (timeout, no token, rejected, socket error):
  ``unresponsive``, naming the pids and the cause;
- no visible voxd, but something accepts a connection on the port: ``unknown``
  -- an owner vox cannot see (another user's process) holds it, and no token
  is sent to it;
- no voxd and no listener: ``not_running``, naming any voxd on the port
  ``serve.port`` names, as ``stop`` does;
- a port, process or unit vox cannot inspect, or a deadline spent before
  voxd could be asked: ``unknown``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.service.backend import BackendCommandError, PortProbeError
from punt_vox.service.daemon_status import DaemonStatus
from punt_vox.service.deadline import Deadline
from punt_vox.service.process import DEFAULT_PORT

if TYPE_CHECKING:
    from punt_vox.service.backend import LifecycleProbe, ProcessHandle
    from punt_vox.service.lifecycle_system import LifecycleSystem

__all__ = ["StatusCheck"]


@final
class StatusCheck:
    """Read voxd's state on the service port once, within one deadline."""

    __slots__ = ("_deadline", "_probe")

    _deadline: Deadline
    _probe: LifecycleProbe

    def __new__(cls, system: LifecycleSystem, within_s: float) -> Self:
        """Start the reading's deadline: *within_s* seconds from now."""
        self = super().__new__(cls)
        self._deadline = Deadline(system.poll.now, within_s)
        self._probe = system.probe.within(self._deadline)
        return self

    def run(self) -> DaemonStatus:
        """Return the reading; a probe vox could not run is ``unknown``."""
        try:
            return self._read()
        except (PortProbeError, BackendCommandError) as exc:
            return DaemonStatus.unknown(str(exc))

    def _read(self) -> DaemonStatus:
        """Classify the port by who holds it, and who answers."""
        probe = self._probe
        holders = probe.voxd_processes()
        if holders:
            return self._ask(holders)
        if probe.port_listening():
            return DaemonStatus.unknown(
                f"something listens on port {DEFAULT_PORT} but no visible voxd "
                "process holds it; vox sent it no token"
            )
        return DaemonStatus.not_running(
            f"no voxd process holds port {DEFAULT_PORT} and nothing listens on it",
            probe.off_port_voxd(),
        )

    def _ask(self, holders: frozenset[ProcessHandle]) -> DaemonStatus:
        """Ask the visible voxd for health; no answer is ``unresponsive``."""
        probe = self._probe
        left = self._deadline.cap(self._deadline.remaining(), "health request")
        health = probe.health(holders, left)
        if health is not None:
            return DaemonStatus.running(health)
        why = probe.health_miss() or "no reply"
        return DaemonStatus.unresponsive((h.pid for h in holders), str(why))
