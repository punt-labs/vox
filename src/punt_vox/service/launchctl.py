"""launchctl GUI-domain control for the voxd LaunchAgent.

Owns every ``launchctl`` invocation for one agent job -- ``bootout``,
``bootstrap``, ``kickstart``, and, through the composed
:class:`~punt_vox.service.launchctl_query.LaunchctlQuery`, the ``print``
registration probe and the ``list`` loaded-status probe -- so the
plist-authoring backend (``LaunchdBackend``) and the ``vox daemon restart``
orchestrator share one race-free implementation and no caller shells out to
``launchctl`` directly.

The race this class exists to close: ``launchctl bootout`` is asynchronous.
launchctl returns before launchd finishes tearing the job's registration out
of the GUI domain. A ``bootstrap`` issued inside that window collides with the
half-removed registration and fails with ``Bootstrap failed: 5: Input/output
error`` (exit 5), leaving voxd DOWN. Running the restart a second time succeeds
only because the stale registration has cleared by then. ``bootstrap`` here
first waits for the job to leave the domain (``launchctl print`` reporting
absent), and on a residual exit-5 waits again and retries exactly once. If the
job never clears, or bootstrap keeps failing, it raises ``LaunchctlError`` so
the caller surfaces a non-zero exit rather than a false "restarted".
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.service.clock import Poll, SystemClock
from punt_vox.service.launchctl_query import LaunchctlError, LaunchctlQuery

if TYPE_CHECKING:
    import subprocess

    from punt_vox.service.query_answer import QueryAnswer

logger = logging.getLogger(__name__)

# LaunchctlError is what every method here raises, so it is part of this
# module's interface; it is defined beside the queries that raise it first.
__all__ = ["LaunchctlAgent", "LaunchctlError"]


@final
class LaunchctlAgent:
    """Control one LaunchAgent job in the caller's ``gui/<uid>`` domain."""

    __slots__ = ("_label", "_plist", "_query")

    _label: str
    _plist: str
    _query: LaunchctlQuery

    # bootout is async; poll `launchctl print` until the job leaves the domain
    # so a following bootstrap does not race the teardown (exit 5).
    _UNREGISTER_TIMEOUT_S: ClassVar[float] = 5.0
    _UNREGISTER_POLL_S: ClassVar[float] = 0.1
    # launchctl "Bootstrap failed: 5: Input/output error" -- the domain still
    # holds a registration for the label being bootstrapped.
    _IO_ERROR: ClassVar[int] = 5

    def __new__(cls, label: str, plist: str) -> Self:
        self = super().__new__(cls)
        self._label = label
        self._plist = plist
        self._query = LaunchctlQuery(label, self._service_target())
        return self

    # -- domain targets ------------------------------------------------------

    @staticmethod
    def _domain() -> str:
        """Return the launchd GUI domain target for the current user."""
        return f"gui/{os.getuid()}"

    def _service_target(self) -> str:
        """Return the ``gui/<uid>/<label>`` service target for this job."""
        return f"{self._domain()}/{self._label}"

    # -- queries -------------------------------------------------------------

    def is_registered(self) -> bool:
        """Return True if launchd still has this job in the GUI domain.

        ``launchctl print <target>`` exits 0 while the job is registered and
        113 ("could not find service") once it is gone. Any other exit is a
        failed query and raises ``LaunchctlError``: it is never read as
        "absent".
        """
        return self._query.is_registered()

    def pid(self) -> int | None:
        """Return the job's running pid; ``None`` when it has no process."""
        return self._query.pid()

    def is_loaded(self) -> bool:
        """Return True if launchd currently has this job loaded.

        Distinct from ``is_registered()``: that probes the GUI-domain service
        target to serialise the bootout/bootstrap race, whereas this is the
        external "is voxd up?" status query keyed on the bare label.
        """
        return self._query.is_loaded()

    def running_within(self, within_s: float) -> QueryAnswer:
        """Ask launchctl, inside *within_s*, whether the job runs.

        Running means loaded, or registered with a process -- what
        ``LaunchdBackend.is_active`` reads. A query the budget cuts short
        confirms nothing and is named in the answer; a query that fails
        outright still raises ``LaunchctlError``.
        """
        return self._query.running_within(within_s)

    def wait_until_unregistered(self) -> QueryAnswer:
        """Block until the job leaves the GUI domain, bounded.

        The answer holds once ``launchctl print`` reports the job absent. If
        the bound elapses first it does not hold, and it names the ``print``
        when the last one did not answer in time -- so a caller can tell a
        hung launchctl from a job that really stayed registered. Each print
        is given only what is left of the bound, so a slow launchctl cannot
        carry the wait past it.
        """
        poll = Poll(SystemClock(), self._UNREGISTER_POLL_S)
        return poll.answer_within(self._query.left_within, self._UNREGISTER_TIMEOUT_S)

    def _lingering(self, answer: QueryAnswer, when: str) -> str:
        """Say why the job was not confirmed gone *when*: hung, or still there."""
        bound = f"{self._UNREGISTER_TIMEOUT_S:g}s"
        if answer.timed_out:
            return (
                f"{answer.unanswered} did not answer within the {bound} wait "
                f"{when}; cannot confirm {self._label} left {self._domain()}"
            )
        return f"{self._label} was still registered in {self._domain()} {bound} {when}"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run one ``launchctl`` command, bounded; raise if it cannot run."""
        return self._query.run(*args)

    # -- lifecycle -----------------------------------------------------------

    def bootout(self) -> None:
        """Boot the job out of the GUI domain and wait for it to unregister.

        Idempotent for a job that is not loaded: bootout exits non-zero and is
        logged at debug. For a job that *was* loaded, a non-zero exit raises
        ``LaunchctlError`` -- the job may still be running, and the caller must
        not read that as stopped. A job not confirmed gone afterwards is only
        a warning here: ``stop`` confirms the outcome in its own settle loop.
        """
        answer = self._boot_out()
        if not answer.holds:
            logger.warning(
                "%s; a bootstrap may race the teardown",
                self._lingering(answer, "after bootout"),
            )

    def bootout_confirmed(self) -> None:
        """Boot the job out and raise ``LaunchctlError`` unless it is gone.

        For ``uninstall``, which removes the plist next: a job still
        registered -- or a launchctl that never answered -- must fail the
        uninstall, not end in "uninstalled".
        """
        answer = self._boot_out()
        if not answer.holds:
            raise LaunchctlError(self._lingering(answer, "after bootout"))

    def _boot_out(self) -> QueryAnswer:
        """Run bootout and the unregister wait; return the wait's answer."""
        was_loaded = self.is_registered()
        result = self._run("bootout", self._service_target())
        if result.returncode != 0 and was_loaded:
            msg = (
                f"launchctl bootout of {self._label} failed "
                f"(exit {result.returncode}): {result.stderr.strip()}"
            )
            raise LaunchctlError(msg)
        if result.returncode == 0:
            logger.info("Booted out %s", self._label)
        else:
            logger.debug("%s was not loaded; nothing to boot out", self._label)
        return self.wait_until_unregistered()

    def bootstrap(self) -> None:
        """Bootstrap the job, idempotent against a stale registration.

        Waits for the domain to be clear before bootstrapping; on a residual
        exit-5 (stale registration) waits again and retries exactly once.
        Raises ``LaunchctlError`` if the job never clears or bootstrap keeps
        failing -- the caller surfaces that as a non-zero exit.
        """
        cleared = self.wait_until_unregistered()
        if not cleared.holds:
            lingering = self._lingering(cleared, "before bootstrap")
            raise LaunchctlError(
                f"{self._label} did not leave {self._domain()}: {lingering}; "
                "refusing to bootstrap onto a stale registration"
            )
        result = self._bootstrap_once()
        if result.returncode == 0:
            return
        if result.returncode == self._IO_ERROR:
            logger.warning(
                "bootstrap of %s hit exit 5 (stale registration); waiting for "
                "unregister and retrying once",
                self._label,
            )
            cleared = self.wait_until_unregistered()
            if not cleared.holds:
                lingering = self._lingering(cleared, "after exit 5")
                raise LaunchctlError(
                    f"{self._label} bootstrap hit exit 5 and the job did not "
                    f"clear: {lingering}"
                )
            result = self._bootstrap_once()
            if result.returncode == 0:
                return
        raise LaunchctlError(
            f"launchctl bootstrap of {self._label} failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )

    def _bootstrap_once(self) -> subprocess.CompletedProcess[str]:
        """Run a single ``launchctl bootstrap`` and capture its result."""
        return self._run("bootstrap", self._domain(), self._plist)

    def kickstart(self) -> None:
        """Kickstart (restart) the bootstrapped job.

        Raises ``LaunchctlError`` on failure so ``start()`` reports a single
        error type to callers.
        """
        result = self._run("kickstart", "-k", self._service_target())
        if result.returncode != 0:
            raise LaunchctlError(
                f"launchctl kickstart of {self._label} failed "
                f"(exit {result.returncode}): {result.stderr.strip()}"
            )

    def start(self) -> None:
        """Bring the job up: bootstrap (race-free) then kickstart.

        Used by both ``vox daemon install`` and ``vox daemon restart`` so the
        two paths share one race-free bring-up. Raises ``LaunchctlError`` on
        any failure.
        """
        self.bootstrap()
        self.kickstart()
