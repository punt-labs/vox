"""``stop``: ask the service manager, then confirm voxd is really gone.

A stop succeeds only when four things hold at once: the manager reports the
job inactive, no visible voxd holds the port, every process handle captured
before acting -- the manager's main process included -- has exited, and the
port accepts no connection. A backend command's exit code never decides the
outcome on its own.

Once the manager is inactive, a voxd still on the port is not the manager's
(an unmanaged voxd), and is terminated through its pinned handle. The
manager's main process is never signalled: while the manager reports one,
the job counts as active and no escalation runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.service.backend import (
    BackendCommandError,
    PortProbeError,
    PrivilegeDeniedError,
)
from punt_vox.service.lifecycle_report import LifecycleOutcome
from punt_vox.service.process import DEFAULT_PORT

if TYPE_CHECKING:
    from punt_vox.service.backend import ProcessHandle
    from punt_vox.service.lifecycle_report import LifecycleReport, LifecycleVerb
    from punt_vox.service.lifecycle_system import LifecycleSystem, Observation

__all__ = ["StopVerb"]


@final
class StopVerb:
    """One run of ``stop``: observe, request, wait, escalate, confirm."""

    __slots__ = (
        "_captured",
        "_deadline_s",
        "_doomed",
        "_killed",
        "_main",
        "_system",
    )

    _system: LifecycleSystem
    _deadline_s: float
    _captured: frozenset[ProcessHandle]
    _main: frozenset[ProcessHandle]
    _killed: list[int]
    _doomed: set[ProcessHandle]

    def __new__(cls, system: LifecycleSystem, deadline_s: float) -> Self:
        self = super().__new__(cls)
        self._system = system
        self._deadline_s = deadline_s
        self._captured = frozenset()
        self._main = frozenset()
        self._killed = []
        self._doomed = set()
        return self

    def run(self, verb: LifecycleVerb) -> LifecycleReport:
        """Stop voxd and return the confirmed outcome."""
        try:
            return self._run(verb)
        except PortProbeError as exc:
            return self._system.report(verb, LifecycleOutcome.BACKEND_FAILED, str(exc))

    def _run(self, verb: LifecycleVerb) -> LifecycleReport:
        obs = self._system.observe()
        if not obs.installed:
            detail = self._untouched(obs)
            return self._report(verb, LifecycleOutcome.NOT_INSTALLED, detail)
        if obs.quiet:
            return self._report(
                verb, LifecycleOutcome.ALREADY_STOPPED, "voxd.service is not running"
            )
        previous = obs.mainpid or min((p.pid for p in obs.procs), default=None)
        self._capture(obs)
        refused = self._request(verb)
        if refused is not None:
            return refused.naming(previous_pid=previous)
        if self._system.poll.until(self._settled, self._deadline_s):
            done = self._report(verb, LifecycleOutcome.STOPPED, self._stopped_detail())
            return done.naming(previous_pid=previous)
        return self._timeout(verb).naming(previous_pid=previous)

    def _capture(self, obs: Observation) -> None:
        """Pin the port holders and the manager's main process before acting."""
        self._captured = obs.procs
        main = None
        if obs.mainpid is not None:
            main = self._system.probe.open_handle(obs.mainpid)
        self._main = frozenset() if main is None else frozenset({main})

    def _request(self, verb: LifecycleVerb) -> LifecycleReport | None:
        """Ask the manager to stop; return a report only if it refused.

        A refusal counts only if the job is still active afterwards: when
        another actor stopped it in the same instant, the stop carries on.
        """
        try:
            self._system.backend.request_stop()
        except PrivilegeDeniedError as exc:
            if self._system.is_active():
                return self._report(verb, LifecycleOutcome.PERMISSION_DENIED, str(exc))
        except BackendCommandError as exc:
            if self._system.is_active():
                return self._report(verb, LifecycleOutcome.BACKEND_FAILED, str(exc))
        return None

    def _settled(self) -> bool:
        """Return True once the stop is confirmed; escalate when it can."""
        if self._system.is_active():
            return False
        holders = self._system.probe.voxd_processes()
        lingering = frozenset(h for h in self._captured if not h.exited())
        targets = holders | lingering
        if targets:
            self._terminate(targets)
            return False
        if any(not h.exited() for h in self._main):
            return False
        return not self._system.probe.port_listening()

    def _terminate(self, targets: frozenset[ProcessHandle]) -> None:
        """Terminate, once each, voxd processes the inactive manager left.

        A handle that survives its own ``terminate()`` is not signalled again;
        it keeps the stop from confirming and is named at the deadline.
        """
        for handle in targets - self._doomed:
            self._doomed.add(handle)
            self._killed.append(handle.pid)
            handle.terminate()

    def _stopped_detail(self) -> str:
        detail = f"voxd.service stopped; port {DEFAULT_PORT} free"
        if self._killed:
            pids = ", ".join(str(p) for p in self._killed)
            detail += f"; killed unmanaged voxd pid {pids}"
        return detail + self._off_port_note()

    def _timeout(self, verb: LifecycleVerb) -> LifecycleReport:
        """Name what is still up at the deadline."""
        if self._system.is_active():
            what = "voxd.service is still active"
        else:
            alive = {h.pid for h in self._captured | self._main if not h.exited()}
            alive |= {h.pid for h in self._system.probe.voxd_processes()}
            if alive:
                pids = ", ".join(str(p) for p in sorted(alive))
                what = f"voxd pid {pids} is still running"
            else:
                what = (
                    f"port {DEFAULT_PORT} still accepts connections but no "
                    "visible voxd process holds it"
                )
        detail = f"{what} after {self._deadline_s:.0f}s"
        return self._report(verb, LifecycleOutcome.TIMEOUT, detail)

    def _off_port_note(self) -> str:
        """Name any voxd on another port; it is never signalled."""
        others = self._system.probe.off_port_voxd()
        if not others:
            return ""
        pids = ", ".join(str(p) for p in sorted(others))
        return f"; voxd pid {pids} on another port was not touched"

    @staticmethod
    def _untouched(obs: Observation) -> str:
        """Return the not-installed detail, naming any voxd left alone."""
        detail = "voxd.service is not installed; run `vox daemon install`"
        if obs.procs:
            held = f"voxd pid {obs.holder_pids()} on port {DEFAULT_PORT}"
            detail += f"; {held} was not touched"
        return detail

    def _report(
        self, verb: LifecycleVerb, outcome: LifecycleOutcome, detail: str
    ) -> LifecycleReport:
        return self._system.report(verb, outcome, detail)
