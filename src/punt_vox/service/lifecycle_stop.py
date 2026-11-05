"""``stop``: ask the service manager, then confirm voxd is really gone.

A stop succeeds only when four things hold at once: the manager reports the
job inactive, no visible voxd holds the port, every process handle captured
before acting -- the manager's main process included -- and every process stop
signalled has exited, and the port accepts no connection. A backend command's
exit code never decides the outcome on its own.

Once the manager is inactive, a voxd still on the port is not the manager's
(an unmanaged voxd), and is terminated through its pinned handle. The
manager's main process is never signalled: while the manager reports one,
the job counts as active and no escalation runs, and immediately before each
signal the manager is asked again, and a process it still owns is spared.

Every report is built from what the settle ticks recorded -- the
:class:`~punt_vox.service.kill_ledger.KillLedger` written as each process was
spared or signalled, and the last tick's account of what was still up. No probe
runs while a report is built, so a probe failure after a signal still yields a
report that names every signalled pid.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.service.backend import (
    BackendCommandError,
    PortProbeError,
    PrivilegeDeniedError,
)
from punt_vox.service.kill_ledger import KillLedger
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
        "_ledger",
        "_main",
        "_notes",
        "_off_port",
        "_port_file_done",
        "_requested",
        "_still_up",
        "_system",
    )

    _system: LifecycleSystem
    _deadline_s: float
    _deadline_at: float
    _captured: frozenset[ProcessHandle]
    _main: frozenset[ProcessHandle]
    _ledger: KillLedger
    _notes: list[str]
    _requested: bool
    _port_file_done: bool
    _still_up: str
    _off_port: str

    def __new__(cls, system: LifecycleSystem, deadline_s: float) -> Self:
        self = super().__new__(cls)
        self._system = system
        self._deadline_s = deadline_s
        self._deadline_at = 0.0
        self._captured = frozenset()
        self._main = frozenset()
        self._ledger = KillLedger()
        self._notes = []
        self._requested = False
        self._port_file_done = False
        # What the last settle tick found still up; the timeout detail.
        self._still_up = "voxd did not stop"
        # The off-port note the confirming tick took; the stopped detail.
        self._off_port = ""
        return self

    def run(self, verb: LifecycleVerb) -> LifecycleReport:
        """Stop voxd and return the confirmed outcome.

        A failing probe or manager query ends the verb as ``backend_failed``,
        saying whether the stop had already been requested and what it had
        already done (processes killed, spared or left unconfirmed, refusals
        overruled).
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
        detail = f"{self._still_up} after {self._deadline_s:.0f}s{self._done()}"
        timeout = self._report(verb, LifecycleOutcome.TIMEOUT, detail)
        return timeout.naming(previous_pid=previous)

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
        """One settle tick: escalate when it can, record what is still up.

        Returns True once the stop is confirmed. Every probe of the stop runs
        here, never while a report is built; what a report needs is written
        to the ledger and ``_still_up`` as the tick learns it.
        """
        if self._system.is_active():
            self._still_up = "voxd.service is still active"
            return False
        up = self._clear_the_port()
        if up:
            self._still_up = f"voxd pid {self._join(up)} is still running"
            return False
        if self._system.probe.port_listening():
            self._still_up = (
                f"port {DEFAULT_PORT} still accepts connections but no visible "
                "voxd process holds it"
            )
            return False
        self._finish_port_file()
        self._off_port = self._off_port_note()
        return True

    def _clear_the_port(self) -> list[int]:
        """Escalate against every voxd left behind; return the pids still up.

        A signalled process seen to exit is marked gone in the ledger here,
        on whichever tick sees it -- the tick of the signal or a later one.
        The manager's main process counts as up until it exits, but is never
        a target.
        """
        holders = self._system.probe.voxd_processes()
        watched = self._captured | self._ledger.unconfirmed()
        lingering = frozenset(h for h in watched if not h.exited())
        self._ledger.confirm(watched - lingering)
        left = holders | lingering
        if left:
            self._escalate(left)
        self._clear_port_file(holders)
        up = {h.pid for h in left if not self._ledger.gone(h)}
        up |= {h.pid for h in self._main if not h.exited()}
        return sorted(up)

    def _escalate(self, targets: frozenset[ProcessHandle]) -> None:
        """Terminate, once each, voxd processes the inactive manager left.

        Each process is judged once: spared or signalled, never both, never
        twice. Immediately before each one the manager is asked again: if it
        became active, nothing more is signalled and the next tick decides
        afresh. A process the manager still owns (a member of its cgroup) is
        spared.
        """
        probe = self._system.probe
        for handle in sorted(targets, key=self._pid_of):
            if self._ledger.judged(handle):
                continue
            if self._system.is_active():
                return
            if probe.service_owned(handle.pid):
                logger.warning("voxd pid %d belongs to the service; spared", handle.pid)
                self._ledger.spare(handle)
                continue
            self._signal(handle)

    def _signal(self, handle: ProcessHandle) -> None:
        """Terminate *handle* within what is left of the stop's deadline.

        The ledger entry is written before the signal goes out, so a failure
        inside ``terminate()`` still leaves the pid named in the report.
        """
        budget = max(0.0, self._deadline_at - self._system.poll.now())
        self._ledger.signalling(handle)
        self._ledger.settle(handle, handle.terminate(budget))

    def _clear_port_file(self, holders: frozenset[ProcessHandle]) -> None:
        """Remove a stale ``serve.port``, at most once per stop.

        Runs only after a signalled voxd is confirmed gone, and only while no
        visible voxd it has not killed holds the port (that voxd's file is
        live). It counts as done once the file is removed or there was nothing
        to remove; a probe that declines because a voxd holds the port leaves
        it to a later tick. After the kill, a failure is a note in the
        detail, not a failed stop.
        """
        if self._port_file_done or not self._ledger.any_gone():
            return
        if any(not self._ledger.gone(h) for h in holders):
            return
        try:
            cleared = self._system.probe.clear_port_file()
        except PortProbeError as exc:
            self._port_file_done = True
            logger.warning("could not clear serve.port: %s", exc)
            self._notes.append(f"could not clear serve.port: {exc}")
            return
        # A voxd that took the port since this tick looked is not spent on:
        # the cleanup waits for a later tick.
        self._port_file_done = cleared.settled

    def _finish_port_file(self) -> None:
        """On the confirming tick, try a declined cleanup once more, then say why.

        The port is free now, so a decline here means a voxd only the
        cleanup's own re-list saw; the stale file is named, not left silent.
        """
        if self._port_file_done or not self._ledger.any_gone():
            return
        self._clear_port_file(frozenset())
        if not self._port_file_done:
            self._notes.append(
                f"a voxd took port {DEFAULT_PORT} after stop confirmed it free; "
                "serve.port left in place"
            )

    @staticmethod
    def _pid_of(handle: ProcessHandle) -> int:
        return handle.pid

    @staticmethod
    def _join(pids: list[int]) -> str:
        """Return *pids* comma-separated, for a report detail."""
        return ", ".join(str(p) for p in pids)

    def _stopped_detail(self) -> str:
        detail = f"voxd.service stopped; port {DEFAULT_PORT} free"
        return detail + self._done() + self._off_port

    def _done(self) -> str:
        """Return what the stop did to voxd processes, and any carried notes.

        Every report names this -- success, timeout or failure -- so a stop
        that signalled something never ends without saying so, and a voxd it
        left alive or could not confirm gone is named with the reason. It
        reads only the ledger and the notes: no probe.
        """
        parts = [*self._ledger.account(), *self._notes]
        return "".join(f"; {part}" for part in parts)

    def _off_port_note(self) -> str:
        """Name any voxd on another port; it is never signalled."""
        others = self._system.probe.off_port_voxd()
        if not others:
            return ""
        pids = self._join(sorted(others))
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
