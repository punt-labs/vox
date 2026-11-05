"""launchctl GUI-domain control for the voxd LaunchAgent.

Owns every ``launchctl`` invocation for one agent job -- ``bootout``,
``bootstrap``, ``kickstart``, the ``print`` registration probe, and the
``list`` loaded-status probe -- so the plist-authoring backend
(``LaunchdBackend``) and the ``vox daemon restart`` orchestrator share one
race-free implementation and no caller shells out to ``launchctl`` directly.

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
import re
import subprocess
import time
from typing import ClassVar, Self, final

logger = logging.getLogger(__name__)

__all__ = ["LaunchctlAgent", "LaunchctlError"]


@final
class LaunchctlError(RuntimeError):
    """A ``launchctl`` operation failed unrecoverably.

    Raised when the job never leaves the GUI domain within the bound, or when
    ``bootstrap`` still fails after the single exit-5 retry. The caller
    translates this into a non-zero exit with a clear message.
    """

    __slots__ = ()


@final
class LaunchctlAgent:
    """Control one LaunchAgent job in the caller's ``gui/<uid>`` domain."""

    __slots__ = ("_label", "_plist")

    _label: str
    _plist: str

    # bootout is async; poll `launchctl print` until the job leaves the domain
    # so a following bootstrap does not race the teardown (exit 5).
    _UNREGISTER_TIMEOUT_S: ClassVar[float] = 5.0
    _UNREGISTER_POLL_S: ClassVar[float] = 0.1
    # launchctl "Bootstrap failed: 5: Input/output error" -- the domain still
    # holds a registration for the label being bootstrapped.
    _IO_ERROR: ClassVar[int] = 5
    # launchctl print/list: "Could not find service" -- the only exit that
    # means the job is absent; every other non-zero exit is a failed query.
    _NOT_FOUND: ClassVar[int] = 113
    # A launchctl command that hangs must not hang the verb past its deadline.
    _COMMAND_TIMEOUT_S: ClassVar[float] = 10.0
    # The running pid in `launchctl print` output: a "pid = N" line.
    _PID_LINE: ClassVar[re.Pattern[str]] = re.compile(r"^\s*pid = (\S+)\s*$", re.M)

    def __new__(cls, label: str, plist: str) -> Self:
        self = super().__new__(cls)
        self._label = label
        self._plist = plist
        return self

    # -- domain targets ------------------------------------------------------

    @staticmethod
    def _domain() -> str:
        """Return the launchd GUI domain target for the current user."""
        return f"gui/{os.getuid()}"

    def _service_target(self) -> str:
        """Return the ``gui/<uid>/<label>`` service target for this job."""
        return f"{self._domain()}/{self._label}"

    # -- registration probe --------------------------------------------------

    def is_registered(self) -> bool:
        """Return True if launchd still has this job in the GUI domain.

        ``launchctl print <target>`` exits 0 while the job is registered and
        113 ("could not find service") once it is gone. Any other exit is a
        failed query and raises ``LaunchctlError``: it is never read as
        "absent".
        """
        return self._present(self._print())

    def pid(self) -> int | None:
        """Return the job's running pid, read from ``launchctl print``.

        ``None`` is the documented answer when the job is not registered or is
        registered with no running process. A failed query, or a ``pid = ...``
        line that is not a number, raises ``LaunchctlError`` rather than
        reading as "no process".
        """
        result = self._print()
        if not self._present(result):
            return None
        found = self._PID_LINE.search(result.stdout)
        if found is None:
            return None
        if not found.group(1).isdecimal():
            msg = f"launchctl print {self._label}: malformed pid {found.group(1)!r}"
            raise LaunchctlError(msg)
        return int(found.group(1))

    def _print(self) -> subprocess.CompletedProcess[str]:
        """Run ``launchctl print`` on this job's service target."""
        return self._run("print", self._service_target())

    def is_loaded(self) -> bool:
        """Return True if launchd currently has this job loaded.

        ``launchctl list <label>`` exits 0 while the job is loaded and 113
        once it is gone; any other exit raises. Distinct from
        ``is_registered()``: that probes the GUI-domain service target to
        serialise the bootout/bootstrap race, whereas this is the external
        "is voxd up?" status query keyed on the bare label.
        """
        return self._present(self._run("list", self._label))

    def _present(self, result: subprocess.CompletedProcess[str]) -> bool:
        """Classify a print/list query: 0 present, 113 absent, else raise."""
        if result.returncode == 0:
            return True
        if result.returncode == self._NOT_FOUND:
            return False
        msg = (
            f"launchctl query of {self._label} failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
        raise LaunchctlError(msg)

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run one ``launchctl`` command, bounded; raise if it cannot run."""
        argv = ["launchctl", *args]
        try:
            return subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._COMMAND_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            msg = f"{' '.join(argv)} could not run: {exc}"
            raise LaunchctlError(msg) from exc

    def wait_until_unregistered(self) -> bool:
        """Block until the job leaves the GUI domain, bounded. Return success.

        Returns True once ``is_registered()`` reports absent, or False if the
        job is still registered when the bound elapses. The bound is real work,
        not a blind sleep: it polls the actual registration state and returns
        the instant the job clears.
        """
        deadline = time.monotonic() + self._UNREGISTER_TIMEOUT_S
        while time.monotonic() < deadline:
            if not self.is_registered():
                return True
            time.sleep(self._UNREGISTER_POLL_S)
        return not self.is_registered()

    # -- lifecycle -----------------------------------------------------------

    def bootout(self) -> None:
        """Boot the job out of the GUI domain and wait for it to unregister.

        Idempotent for a job that is not loaded: bootout exits non-zero and is
        logged at debug. For a job that *was* loaded, a non-zero exit raises
        ``LaunchctlError`` -- the job may still be running, and the caller must
        not read that as stopped. The wait afterward is what makes a following
        ``bootstrap`` race-free.
        """
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
        if not self.wait_until_unregistered():
            logger.warning(
                "%s still registered after bootout; a bootstrap may race the teardown",
                self._label,
            )

    def bootstrap(self) -> None:
        """Bootstrap the job, idempotent against a stale registration.

        Waits for the domain to be clear before bootstrapping; on a residual
        exit-5 (stale registration) waits again and retries exactly once.
        Raises ``LaunchctlError`` if the job never clears or bootstrap keeps
        failing -- the caller surfaces that as a non-zero exit.
        """
        if not self.wait_until_unregistered():
            raise LaunchctlError(
                f"{self._label} did not leave {self._domain()} within "
                f"{self._UNREGISTER_TIMEOUT_S:.0f}s; refusing to bootstrap onto "
                "a stale registration"
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
            if not self.wait_until_unregistered():
                raise LaunchctlError(
                    f"{self._label} bootstrap hit exit 5 and the job did not "
                    f"clear {self._domain()} within "
                    f"{self._UNREGISTER_TIMEOUT_S:.0f}s"
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
