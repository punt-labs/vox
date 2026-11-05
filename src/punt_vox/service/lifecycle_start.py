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
    from punt_vox.service.lifecycle_report import LifecycleReport, LifecycleVerb
    from punt_vox.service.lifecycle_system import LifecycleSystem, Observation
    from punt_vox.types_health import HealthStatus

logger = logging.getLogger(__name__)

__all__ = ["StartVerb"]


@final
class StartVerb:
    """One run of ``start``: observe, request if cold, wait, decide."""

    __slots__ = ("_deadline_s", "_fresh", "_notes", "_requested", "_system", "_verb")

    _system: LifecycleSystem
    _deadline_s: float
    _verb: LifecycleVerb
    _fresh: bool
    _notes: list[str]
    _requested: bool

    def __new__(
        cls, system: LifecycleSystem, deadline_s: float, *, require_fresh: bool
    ) -> Self:
        self = super().__new__(cls)
        self._system = system
        self._deadline_s = deadline_s
        self._verb = "start"
        self._fresh = require_fresh
        self._notes = []
        self._requested = False
        return self

    def run(self) -> LifecycleReport:
        """Start voxd and return the verified outcome.

        A failing probe or manager query ends the verb as ``backend_failed``,
        saying whether the start had already been requested.
        """
        try:
            return self._run()
        except (PortProbeError, BackendCommandError) as exc:
            detail = f"after requesting start: {exc}" if self._requested else str(exc)
            return self._report(LifecycleOutcome.BACKEND_FAILED, detail)

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
        return self._decide(self._system.probe.wait_up(self._deadline_s), cold=cold)

    def _request(self) -> LifecycleReport | None:
        """Ask the manager to start; return a report only if it refused.

        A refusal counts only if the job is still inactive afterwards: when
        another actor started it in the same instant, the start carries on and
        the refusal is kept for the report.
        """
        self._requested = True
        try:
            self._system.backend.request_start()
        except PrivilegeDeniedError as exc:
            if not self._system.is_active():
                return self._report(LifecycleOutcome.PERMISSION_DENIED, str(exc))
            self._overruled(exc)
        except BackendCommandError as exc:
            if not self._system.is_active():
                return self._report(LifecycleOutcome.BACKEND_FAILED, str(exc))
            self._overruled(exc)
        return None

    def _overruled(self, exc: Exception) -> None:
        """Record a refusal the re-observation contradicted."""
        logger.warning("start request failed, but the unit came up: %s", exc)
        self._notes.append(f"the start request failed ({exc}) but the unit came up")

    def _decide(self, status: HealthStatus | None, *, cold: bool) -> LifecycleReport:
        """Classify the health answer: timeout, foreign, stale, or fresh."""
        if status is None:
            miss = self._system.probe.health_miss()
            cause = f" (last: {miss})" if miss is not None else ""
            detail = (
                f"no health answer on port {DEFAULT_PORT} within "
                f"{self._deadline_s:.0f}s{cause}"
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
        """Return the report, with any overruled refusal appended."""
        carried = (
            "; ".join([detail, *self._notes]) if detail else "; ".join(self._notes)
        )
        return self._system.report(self._verb, outcome, carried)

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
