"""``vox daemon status`` on the local service: one reading of voxd on its port.

voxd is found the way ``stop`` and ``start`` find it -- the installed bind,
port 8421, a token only from ``serve.token``, sent only to a visible voxd on
the port. One :class:`Deadline`, started when the reading starts, bounds the
whole of it: every port and process probe, the listener check, and the health
request each get only what is left.

voxd is asked first, before ``lsof``/``fuser`` attribute the port, through
the port voxd itself published: when this user's own ``serve.port`` names the
service port, the bind is loopback and the port accepts a connection, one
health request goes out with a reserved share of the deadline
(``HEALTH_RESERVE_S``) that the attribution probes cannot consume. An answer
is ``running`` whatever ``lsof`` would have said later: on a loaded Mac
``lsof`` walks every open file and can spend the whole deadline by itself.
With no answer, attribution runs within what is left and decides as before.
The token never goes out through any other door; the gate is stated in
:mod:`punt_vox.service.daemon_probe`. What the probe sees decides the state:

- a voxd answered health, through the published port or as a visible
  holder: ``running``;
- a visible voxd did not answer (timeout, no token, rejected, socket error):
  ``unresponsive``, naming the pids and the cause;
- no visible voxd, but something accepts a connection on the port: ``unknown``
  -- an owner vox cannot see (another user's process) holds it, and no token
  is sent to it;
- no voxd and no listener: ``not_running``, naming any voxd on the port
  ``serve.port`` names, as ``stop`` does;
- a port, process or unit vox cannot inspect, or a deadline spent on vox's
  own probes before voxd could be asked: ``unknown``, naming the probe that
  failed and what the health request before it came to, so the reading
  says which step spent the time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.service.backend import BackendCommandError, PortProbeError
from punt_vox.service.daemon_status import DaemonStatus
from punt_vox.service.deadline import Deadline
from punt_vox.service.health_request import HealthRequest
from punt_vox.service.not_asked import NotAskedError

if TYPE_CHECKING:
    from punt_vox.service.backend import LifecycleProbe, ProcessHandle
    from punt_vox.service.lifecycle_system import LifecycleSystem

__all__ = ["StatusCheck"]


@final
class StatusCheck:
    """Read voxd's state on the service port once, within one deadline."""

    __slots__ = ("_deadline", "_port", "_probe")

    # The share of the deadline the published-port health request is given
    # before any attribution probe runs. lsof/fuser get only the rest.
    HEALTH_RESERVE_S: ClassVar[float] = 1.5

    _deadline: Deadline
    _probe: LifecycleProbe
    _port: int

    def __new__(cls, system: LifecycleSystem, within_s: float) -> Self:
        """Start the reading's deadline: *within_s* seconds from now."""
        self = super().__new__(cls)
        self._deadline = Deadline(system.poll.now, within_s)
        self._probe = system.probe.within(self._deadline)
        self._port = system.port
        return self

    def run(self) -> DaemonStatus:
        """Return the reading; a probe vox could not run is ``unknown``."""
        try:
            return self._read()
        except (PortProbeError, BackendCommandError) as exc:
            return DaemonStatus.unknown(self._spent_on(exc))

    def _read(self) -> DaemonStatus:
        """Ask the published port first; then classify the port by its holders."""
        health = self._probe.published_health(
            min(self.HEALTH_RESERVE_S, self._deadline.remaining())
        )
        if health is not None:
            return DaemonStatus.running(health)
        return self._attribute()

    def _attribute(self) -> DaemonStatus:
        """Classify the port by who holds it, and who answers."""
        probe = self._probe
        holders = probe.voxd_processes()
        if holders:
            return self._ask(holders)
        if probe.port_listening():
            return DaemonStatus.unknown(
                f"something listens on port {self._port} but no visible voxd "
                "process holds it; vox sent it no token"
            )
        return DaemonStatus.not_running(
            f"no voxd process holds port {self._port} and nothing listens on it",
            probe.off_port_voxd(),
        )

    def _ask(self, holders: frozenset[ProcessHandle]) -> DaemonStatus:
        """Ask the visible voxd for health; no answer is ``unresponsive``."""
        left = self._deadline.remaining()
        if left < HealthRequest.MIN_S:
            return DaemonStatus.unknown(
                f"the status deadline was spent probing port {self._port} "
                f"before voxd could be asked ({left:.2f}s left)"
            )
        health = self._probe.health(holders, left)
        if health is not None:
            return DaemonStatus.running(health)
        return self._unanswered(holders)

    def _unanswered(self, holders: frozenset[ProcessHandle]) -> DaemonStatus:
        """Read why the visible voxd gave no answer.

        A holder that left the port before it was asked was never asked:
        nothing is known about the port now, so that is ``unknown``. Any other
        miss -- a timeout, a rejected token -- is ``unresponsive``.
        """
        why = self._probe.health_miss() or "no reply"
        if isinstance(why, NotAskedError):
            return DaemonStatus.unknown(str(why))
        return DaemonStatus.unresponsive((h.pid for h in holders), str(why))

    def _spent_on(self, exc: Exception) -> str:
        """Name the probe that failed and what the health request before it did.

        A probe fails only after the published-port request has run, so the
        reading never blames ``lsof`` alone for a deadline the health step
        may have spent first.
        """
        first = self._probe.health_miss()
        if first is None:
            return str(exc)
        if isinstance(first, NotAskedError):
            return f"{exc}; voxd was not asked first: {first}"
        return f"{exc}; voxd was asked first and gave no answer: {first}"
