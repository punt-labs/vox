"""launchctl GUI-domain control for the voxd LaunchAgent.

Owns every ``launchctl`` invocation for one agent job -- ``bootout``,
``bootstrap``, ``kickstart``, and the ``print`` registration probe -- so the
plist-authoring backend (``LaunchdBackend``) and the ``vox daemon restart``
orchestrator share one race-free implementation.

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
import subprocess
import time
from typing import ClassVar, Self

logger = logging.getLogger(__name__)

__all__ = ["LaunchctlAgent", "LaunchctlError"]


class LaunchctlError(RuntimeError):
    """A ``launchctl`` operation failed unrecoverably.

    Raised when the job never leaves the GUI domain within the bound, or when
    ``bootstrap`` still fails after the single exit-5 retry. The caller
    translates this into a non-zero exit with a clear message.
    """

    __slots__ = ()


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
        non-zero once it has been fully unregistered.
        """
        result = subprocess.run(  # noqa: S603
            ["launchctl", "print", self._service_target()],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

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

        Idempotent: a bootout of an unloaded job exits non-zero and is logged
        at debug. The wait afterward is what makes a subsequent ``bootstrap``
        race-free -- without it the bootstrap collides with the teardown.
        """
        result = subprocess.run(  # noqa: S603
            ["launchctl", "bootout", self._service_target()],  # noqa: S607
            check=False,
        )
        if result.returncode != 0:
            logger.debug(
                "bootout %s exited %d (job may not be loaded)",
                self._label,
                result.returncode,
            )
        else:
            logger.info("Booted out %s", self._label)
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
        return subprocess.run(  # noqa: S603
            ["launchctl", "bootstrap", self._domain(), self._plist],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )

    def kickstart(self) -> None:
        """Kickstart (restart) the bootstrapped job.

        Raises ``LaunchctlError`` on failure so ``start()`` reports a single
        error type to callers.
        """
        result = subprocess.run(  # noqa: S603
            ["launchctl", "kickstart", "-k", self._service_target()],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
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
