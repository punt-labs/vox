"""``vox daemon status`` on the local service: one reading of voxd on its port.

voxd is found the way ``stop`` and ``start`` find it -- the installed bind,
port 8421, a token only from ``serve.token``, sent only to a visible voxd on
the port. One :class:`Deadline`, started when the reading starts, bounds the
whole of it: every port and process probe, the listener check, and the health
request each get only what is left.

voxd is asked first, before ``lsof``/``fuser`` attribute the port, through
what voxd itself published: one health request goes out with a reserved
share of the deadline (``HEALTH_RESERVE_S``) that the attribution probes
cannot consume, under the gate :mod:`punt_vox.service.published_health`
states (this user's own ``serve.port`` and ``serve.pid``, a live voxd of
this user that holds the port, loopback, a listener). An answer is
``running`` whatever ``lsof`` would have said later: on a loaded Mac ``lsof``
walks every open file and can spend the whole deadline by itself. With no
answer, attribution runs within what is left and decides as before. The
token never goes out through any other door. What the probe sees decides
the state:

- a voxd answered health, through the published port or as a visible
  holder: ``running``;
- a visible voxd did not answer (timeout, no token, rejected, socket error):
  ``unresponsive``, naming the pids and the cause;
- no visible voxd, but something accepts a connection on the port: ``unknown``
  -- an owner vox cannot see (another user's process) holds it; the reading
  says whether the published door already sent it the token;
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
from punt_vox.service.health import DaemonNotUpError
from punt_vox.service.health_request import HealthRequest
from punt_vox.service.not_asked import NotAskedError

if TYPE_CHECKING:
    from punt_vox.service.backend import LifecycleProbe, ProcessHandle
    from punt_vox.service.lifecycle_system import LifecycleSystem

__all__ = ["FirstAsk", "StatusCheck"]


@final
class FirstAsk:
    """What the published-port request came to; every later reading names it.

    Kept the moment that request returns nothing, because the attributed
    request that may follow overwrites the probe's miss. Before any request
    it is empty and adds nothing.
    """

    __slots__ = ("_miss",)

    _miss: Exception | None  # None: no published-port request has been made

    def __new__(cls, miss: Exception | None) -> Self:
        """Create the record of the first ask: *miss* is why it got no answer."""
        self = super().__new__(cls)
        self._miss = miss
        return self

    @property
    def sent(self) -> bool:
        """Return True when a token-bearing request went out and got no answer."""
        return not isinstance(self._miss, NotAskedError | None)

    def clause(self) -> str:
        """Return the clause a later unknown reading appends; empty before any ask."""
        if self._miss is None:
            return ""
        said = (
            "was not asked first"
            if isinstance(self._miss, NotAskedError)
            else "was asked first and gave no answer"
        )
        return f"; voxd {said}: {self._miss}"

    def listener(self, seen: str) -> str:
        """Return what reached a listener no visible voxd holds, after *seen*.

        The published door may already have sent the token there and got no
        answer; the reading then says so rather than "no token", which would
        be false.
        """
        if self.sent:
            return (
                f"{seen}; the token went to the published port and got no answer: "
                f"{self._miss}"
            )
        return f"{seen}; vox sent it no token{self.clause()}"


@final
class StatusCheck:
    """Read voxd's state on the service port once, within one deadline."""

    __slots__ = ("_deadline", "_first", "_port", "_probe")

    # The share of the deadline the published-port health request is given
    # before any attribution probe runs. lsof/fuser get only the rest.
    HEALTH_RESERVE_S: ClassVar[float] = 1.5

    _deadline: Deadline
    _probe: LifecycleProbe
    _port: int
    _first: FirstAsk

    def __new__(cls, system: LifecycleSystem, within_s: float) -> Self:
        """Start the reading's deadline: *within_s* seconds from now."""
        self = super().__new__(cls)
        self._deadline = Deadline(system.poll.now, within_s)
        self._probe = system.probe.within(self._deadline)
        self._port = system.port
        self._first = FirstAsk(None)
        return self

    def run(self) -> DaemonStatus:
        """Return the reading; a probe vox could not run is ``unknown``."""
        try:
            return self._read()
        except (PortProbeError, BackendCommandError) as exc:
            return DaemonStatus.unknown(self._after_first_ask(str(exc)))

    def _read(self) -> DaemonStatus:
        """Ask the published port first; then classify the port by its holders."""
        probe = self._probe
        health = probe.published_health(
            min(self.HEALTH_RESERVE_S, self._deadline.remaining())
        )
        if health is not None:
            return DaemonStatus.running(health)
        self._first = FirstAsk(probe.health_miss())
        return self._attribute()

    def _attribute(self) -> DaemonStatus:
        """Classify the port by who holds it, and who answers."""
        probe = self._probe
        holders = probe.voxd_processes()
        if holders:
            return self._ask(holders)
        if probe.port_listening():
            seen = (
                f"something listens on port {self._port} but no visible voxd "
                "process holds it"
            )
            return DaemonStatus.unknown(self._first.listener(seen))
        return DaemonStatus.not_running(
            f"no voxd process holds port {self._port} and nothing listens on it",
            probe.off_port_voxd(),
        )

    def _ask(self, holders: frozenset[ProcessHandle]) -> DaemonStatus:
        """Ask the visible voxd for health; no answer is ``unresponsive``."""
        left = self._deadline.remaining()
        if left < HealthRequest.MIN_S:
            return DaemonStatus.unknown(
                self._after_first_ask(
                    f"the status deadline was spent probing port {self._port} "
                    f"before voxd could be asked ({left:.2f}s left)"
                )
            )
        health = self._probe.health(holders, left)
        if health is not None:
            return DaemonStatus.running(health)
        return self._unanswered(holders)

    def _unanswered(self, holders: frozenset[ProcessHandle]) -> DaemonStatus:
        """Read why the visible voxd gave no answer.

        A holder that left the port before it was asked was never asked:
        nothing is known about the port now, so that is ``unknown``. Any other
        miss -- a timeout, a rejected token, a ``serve.token`` voxd has not
        published -- is ``unresponsive``: the process is there and holds the
        port, so a visible voxd is never read as unknown for want of its
        token; the cause names the file.
        """
        why = self._probe.health_miss() or "no reply"
        if self._never_asked(why):
            return DaemonStatus.unknown(self._after_first_ask(str(why)))
        return DaemonStatus.unresponsive((h.pid for h in holders), str(why))

    @staticmethod
    def _never_asked(why: Exception | str) -> bool:
        """Return True when *why* says no request reached the visible holder.

        A missing token is the one not-asked miss that still reads as
        unresponsive: the holder is there and the file is the cause.
        """
        return isinstance(why, NotAskedError) and not isinstance(why, DaemonNotUpError)

    def _after_first_ask(self, what: str) -> str:
        """Return *what* with what the published-port request before it came to.

        Every ``unknown`` reading after that request names it, so a deadline
        the health step spent, or a token that never went out, is never
        hidden behind the probe that failed later.
        """
        return what + self._first.clause()
