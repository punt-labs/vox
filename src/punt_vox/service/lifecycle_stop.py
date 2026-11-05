"""``stop``: ask the service manager, then confirm voxd is really gone.

A stop succeeds only when four things hold at once: the manager reports the
job inactive, no visible voxd holds the port, every process handle captured
before acting -- the manager's main process included -- has exited, and the
port accepts no connection. A backend command's exit code never decides the
outcome on its own.

Once the manager is inactive, a voxd still on the port is not the manager's
(an unmanaged voxd), and is terminated through its pinned handle. The
manager's main process is never signalled: while the manager reports one,
the job counts as active and no escalation runs, and immediately before any
signal the manager is asked again, and a process it still owns is spared.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

__all__ = ["StopVerb"]


@final
class StopVerb:
    """One run of ``stop``: observe, request, wait, escalate, confirm."""

    __slots__ = (
        "_captured",
        "_deadline_at",
        "_deadline_s",
        "_doomed",
        "_killed",
        "_main",
        "_notes",
        "_requested",
        "_spared",
        "_system",
    )

    _system: LifecycleSystem
    _deadline_s: float
    _deadline_at: float
    _captured: frozenset[ProcessHandle]
    _main: frozenset[ProcessHandle]
    _killed: list[int]
    _doomed: set[ProcessHandle]
    _notes: list[str]
    _requested: bool
    _spared: set[int]

    def __new__(cls, system: LifecycleSystem, deadline_s: float) -> Self:
        self = super().__new__(cls)
        self._system = system
        self._deadline_s = deadline_s
        self._deadline_at = 0.0
        self._captured = frozenset()
        self._main = frozenset()
        self._killed = []
        self._doomed = set()
        self._notes = []
        self._requested = False
        self._spared = set()
        return self

    def run(self, verb: LifecycleVerb) -> LifecycleReport:
        """Stop voxd and return the confirmed outcome.

        A failing probe or manager query ends the verb as ``backend_failed``,
        saying whether the stop had already been requested and what it had
        already done (processes killed or spared, refusals overruled).
        """
        try:
            return self._run(verb)
        except (PortProbeError, BackendCommandError) as exc:
            detail = f"after requesting stop: {exc}" if self._requested else str(exc)
            detail += self._done()
            return self._report(verb, LifecycleOutcome.BACKEND_FAILED, detail)

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
        # An inactive manager has nothing to stop: only an unmanaged voxd or a
        # holder vox cannot see is left, and neither needs a privileged call.
        refused = self._request(verb) if obs.active else None
        if refused is not None:
            return refused.naming(previous_pid=previous)
        # The kill budget runs from here: a slow request (a sudo prompt) must
        # not leave a lingering voxd SIGKILLed with no SIGTERM grace.
        self._deadline_at = self._system.poll.now() + self._deadline_s
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
        another actor stopped it in the same instant, the stop carries on and
        the refusal is kept for the report.
        """
        self._requested = True
        try:
            self._system.backend.request_stop()
        except PrivilegeDeniedError as exc:
            if self._system.is_active():
                return self._report(verb, LifecycleOutcome.PERMISSION_DENIED, str(exc))
            self._overruled(exc)
        except BackendCommandError as exc:
            if self._system.is_active():
                return self._report(verb, LifecycleOutcome.BACKEND_FAILED, str(exc))
            self._overruled(exc)
        return None

    def _overruled(self, exc: Exception) -> None:
        """Record a refusal the re-observation contradicted."""
        logger.warning("stop request failed, but the unit went down: %s", exc)
        self._notes.append(f"the stop request failed ({exc}) but the unit went down")

    def _settled(self) -> bool:
        """Return True once the stop is confirmed; escalate when it can."""
        if self._system.is_active():
            return False
        holders = self._system.probe.voxd_processes()
        lingering = frozenset(h for h in self._captured if not h.exited())
        if holders or lingering:
            self._escalate((holders | lingering) - self._doomed)
            return False
        if any(not h.exited() for h in self._main):
            return False
        return not self._system.probe.port_listening()

    def _escalate(self, targets: frozenset[ProcessHandle]) -> None:
        """Terminate, once each, voxd processes the inactive manager left.

        The manager is asked again right before any signal: if it became
        active, nothing is signalled and the next tick decides afresh. A
        process the manager still owns (its main pid, or a member of its
        cgroup) is never signalled. A handle that survives ``terminate()`` is
        not signalled again; it keeps the stop from confirming.
        """
        if not targets or self._system.is_active():
            return
        probe = self._system.probe
        for handle in sorted(targets, key=self._pid_of):
            if probe.service_owned(handle.pid):
                logger.warning("voxd pid %d belongs to the service; spared", handle.pid)
                self._spared.add(handle.pid)
                continue
            self._doomed.add(handle)
            budget = max(0.0, self._deadline_at - self._system.poll.now())
            if handle.terminate(budget):
                self._killed.append(handle.pid)
        if self._killed:
            self._clear_port_file()

    def _clear_port_file(self) -> None:
        """Remove a stale ``serve.port``; after a kill, a failure only warns."""
        try:
            self._system.probe.clear_port_file()
        except PortProbeError as exc:
            logger.warning("could not clear serve.port: %s", exc)
            self._notes.append(f"could not clear serve.port: {exc}")

    @staticmethod
    def _pid_of(handle: ProcessHandle) -> int:
        return handle.pid

    @staticmethod
    def _join(pids: list[int]) -> str:
        """Return *pids* comma-separated, for a report detail."""
        return ", ".join(str(p) for p in pids)

    def _stopped_detail(self) -> str:
        detail = f"voxd.service stopped; port {DEFAULT_PORT} free"
        return detail + self._done() + self._off_port_note()

    def _done(self) -> str:
        """Return what the stop did to voxd processes, and any carried notes.

        Every report names this -- success, timeout or failure -- so a stop
        that killed something never ends without saying so, and a voxd it
        deliberately left alive is named with the reason.
        """
        parts: list[str] = []
        if self._killed:
            parts.append(f"killed unmanaged voxd pid {self._join(self._killed)}")
        parts.extend(
            f"voxd pid {pid} spared: voxd.service still owns it"
            for pid in sorted(self._spared)
        )
        survivors = sorted(h.pid for h in self._doomed if not h.exited())
        parts.extend(
            f"voxd pid {pid} did not exit after SIGTERM/SIGKILL "
            "(permission denied, or it ignored the signals)"
            for pid in survivors
        )
        parts.extend(self._notes)
        return "".join(f"; {part}" for part in parts)

    def _timeout(self, verb: LifecycleVerb) -> LifecycleReport:
        """Name what is still up at the deadline."""
        if self._system.is_active():
            what = "voxd.service is still active"
        else:
            alive = {h.pid for h in self._captured | self._main if not h.exited()}
            alive |= {h.pid for h in self._system.probe.voxd_processes()}
            if alive:
                what = f"voxd pid {self._join(sorted(alive))} is still running"
            else:
                what = (
                    f"port {DEFAULT_PORT} still accepts connections but no "
                    "visible voxd process holds it"
                )
        detail = f"{what} after {self._deadline_s:.0f}s{self._done()}"
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
