"""``vox daemon stop|start|restart|status``: the confirmed daemon lifecycle.

Each changing verb returns a :class:`LifecycleReport`; the CLI only emits it
and exits with its code. ``status`` returns a :class:`DaemonStatus` read
through the same probe.
``restart`` is ``stop`` then ``start(require_fresh=True)`` and adds no stop
or start logic of its own: ``start`` runs only after ``stop`` confirmed every
previous process exited.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.service.lifecycle_report import LifecycleOutcome
from punt_vox.service.lifecycle_start import StartVerb
from punt_vox.service.lifecycle_status import StatusCheck
from punt_vox.service.lifecycle_stop import StopVerb
from punt_vox.service.lifecycle_system import LifecycleSystem

if TYPE_CHECKING:
    from punt_vox.service.daemon_status import DaemonStatus
    from punt_vox.service.lifecycle_report import LifecycleReport

__all__ = ["DaemonLifecycle"]

_STOP_OK = frozenset({LifecycleOutcome.STOPPED, LifecycleOutcome.ALREADY_STOPPED})


@final
class DaemonLifecycle:
    """Stop, start, and restart voxd, each verified against the real process."""

    __slots__ = ("_system",)

    STOP_DEADLINE_S: ClassVar[float] = 10.0
    START_DEADLINE_S: ClassVar[float] = 10.0
    # The whole of ``status`` -- port and process probes, the listener
    # check, and one health request -- finishes within this: a snapshot, not
    # a wait for voxd.
    STATUS_DEADLINE_S: ClassVar[float] = 5.0

    _system: LifecycleSystem

    def __new__(cls, system: LifecycleSystem) -> Self:
        self = super().__new__(cls)
        self._system = system
        return self

    @classmethod
    def for_this_host(cls) -> Self:
        """Return the lifecycle for this host's service manager.

        Raises ``LifecycleRefusedError`` as root or on an unsupported OS,
        before any backend is built.
        """
        return cls(LifecycleSystem.for_this_host())

    def status(self) -> DaemonStatus:
        """Return one reading of the voxd holding the service port.

        Found the way ``stop`` and ``start`` find it: the installed bind, port
        8421, a token only from ``serve.token``, sent only to a visible voxd
        on the port -- never ``serve.port``, never ``VOXD_*`` from the shell.
        Never raises for what it finds: a port or unit vox cannot read is an
        ``unknown`` reading.
        """
        return StatusCheck(self._system, self.STATUS_DEADLINE_S).run()

    def stop(self) -> LifecycleReport:
        """Stop voxd and confirm it is gone."""
        return StopVerb(self._system, self.STOP_DEADLINE_S).run("stop")

    def start(self) -> LifecycleReport:
        """Start voxd and confirm the manager's process serves the wheel."""
        return self._start(require_fresh=False)

    def restart(self) -> LifecycleReport:
        """Stop, then start only once every previous process has exited."""
        stopped = self.stop()
        if stopped.outcome not in _STOP_OK:
            return stopped.as_verb("restart")
        started = self._start(require_fresh=True)
        if started.outcome is LifecycleOutcome.ALREADY_RUNNING:
            # A third party started voxd after the confirmed stop. Inside
            # restart the start half already rejected a stale or foreign
            # daemon, so this is the manager's process on the wheel's code,
            # and never the previous one: stop confirmed every handle exited.
            started = started.as_outcome(LifecycleOutcome.STARTED)
        if started.outcome is LifecycleOutcome.STARTED:
            return started.restarted_after(stopped)
        return started.as_verb("restart")

    def _start(self, *, require_fresh: bool) -> LifecycleReport:
        verb = StartVerb(
            self._system, self.START_DEADLINE_S, require_fresh=require_fresh
        )
        return verb.run()
