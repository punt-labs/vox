"""``start``: bring voxd up through the service manager and verify who serves.

A start succeeds only when a gated health request answers, the answering pid
is the manager's main process, and its version is the installed wheel's.
Rows are checked in a fixed order: the probe, not installed, a port conflict
(before any token is sent), a running or activating job, then a cold start.

``require_fresh`` is set only by ``restart``: there, a running daemon on a
stale version is ``version_mismatch``; a standalone ``start`` reports it as
``already_running`` with a warning, because voxd being up is its whole job.
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
    from punt_vox.service.lifecycle_report import LifecycleReport, LifecycleVerb
    from punt_vox.service.lifecycle_system import LifecycleSystem, Observation
    from punt_vox.types_health import HealthStatus

__all__ = ["StartVerb"]


@final
class StartVerb:
    """One run of ``start``: observe, request if cold, wait, decide."""

    __slots__ = ("_deadline_s", "_fresh", "_system", "_verb")

    _system: LifecycleSystem
    _deadline_s: float
    _verb: LifecycleVerb
    _fresh: bool

    def __new__(
        cls, system: LifecycleSystem, deadline_s: float, *, require_fresh: bool
    ) -> Self:
        self = super().__new__(cls)
        self._system = system
        self._deadline_s = deadline_s
        self._verb = "start"
        self._fresh = require_fresh
        return self

    def run(self) -> LifecycleReport:
        """Start voxd and return the verified outcome."""
        try:
            return self._run()
        except PortProbeError as exc:
            return self._report(LifecycleOutcome.BACKEND_FAILED, str(exc))

    def _run(self) -> LifecycleReport:
        obs = self._system.observe()
        if not obs.installed:
            return self._report(
                LifecycleOutcome.NOT_INSTALLED,
                "voxd.service is not installed; run `vox daemon install`",
            )
        cold = not obs.active
        if cold:
            if obs.procs or obs.listening:
                return self._report(LifecycleOutcome.PORT_CONFLICT, self._holder(obs))
            refused = self._request()
            if refused is not None:
                return refused
        return self._decide(self._wait_up(), cold=cold)

    def _request(self) -> LifecycleReport | None:
        """Ask the manager to start; return a report only if it refused.

        A refusal counts only if the job is still inactive afterwards: when
        another actor started it in the same instant, the start carries on.
        """
        try:
            self._system.backend.request_start()
        except PrivilegeDeniedError as exc:
            if not self._system.is_active():
                return self._report(LifecycleOutcome.PERMISSION_DENIED, str(exc))
        except BackendCommandError as exc:
            if not self._system.is_active():
                return self._report(LifecycleOutcome.BACKEND_FAILED, str(exc))
        return None

    def _wait_up(self) -> HealthStatus | None:
        """Poll gated health until a visible voxd answers or the deadline passes.

        ``None`` is the documented answer when nothing answered in time.
        """
        probe = self._system.probe
        return self._system.poll.first(
            lambda: probe.health(probe.voxd_processes()), self._deadline_s
        )

    def _decide(self, status: HealthStatus | None, *, cold: bool) -> LifecycleReport:
        """Classify the health answer: timeout, foreign, stale, or fresh."""
        if status is None:
            detail = (
                f"no health answer on port {DEFAULT_PORT} within "
                f"{self._deadline_s:.0f}s"
            )
            return self._report(LifecycleOutcome.TIMEOUT, detail)
        mainpid = self._system.backend.main_pid()
        if mainpid is None or status.pid != mainpid:
            runs = "runs no process" if mainpid is None else f"runs pid {mainpid}"
            detail = (
                f"port {DEFAULT_PORT} answered by pid {status.pid}, "
                f"service manager {runs}"
            )
            return self._report(LifecycleOutcome.BACKEND_FAILED, detail).serving(status)
        return self._versioned(status, cold=cold)

    def _versioned(self, status: HealthStatus, *, cold: bool) -> LifecycleReport:
        """Classify the manager's own daemon by the version it runs."""
        version = self._system.version
        if status.daemon_version == version:
            outcome = (
                LifecycleOutcome.STARTED if cold else LifecycleOutcome.ALREADY_RUNNING
            )
            return self._report(outcome, "").serving(status)
        detail = f"voxd runs version {status.daemon_version}, the wheel is {version}"
        if cold or self._fresh:
            return self._report(LifecycleOutcome.VERSION_MISMATCH, detail).serving(
                status
            )
        detail += "; run `vox daemon restart` to load it"
        return self._report(LifecycleOutcome.ALREADY_RUNNING, detail).serving(status)

    def _report(self, outcome: LifecycleOutcome, detail: str) -> LifecycleReport:
        return self._system.report(self._verb, outcome, detail)

    @staticmethod
    def _holder(obs: Observation) -> str:
        """Return the port-conflict detail: who holds the port, how to clear it."""
        if obs.procs:
            who = (
                f"voxd pid {obs.holder_pids()}, which the service manager does not run"
            )
        else:
            who = "a process vox cannot see"
        return (
            f"port {DEFAULT_PORT} is held by {who}; run `vox daemon stop` to clear "
            "a visible voxd"
        )
