"""macOS launchd backend for voxd system service."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.service.backend import BackendCommandError
from punt_vox.service.launchctl import LaunchctlAgent, LaunchctlError
from punt_vox.service.voxd_plist import VoxdPlist

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_LABEL = "com.punt-labs.voxd"
_LAUNCHD_DIR = Path.home() / "Library" / "LaunchAgents"
_LAUNCHD_PLIST = _LAUNCHD_DIR / f"{_LABEL}.plist"


@final
class LaunchdBackend:
    """Drive the voxd LaunchAgent's launchd lifecycle.

    Composes two collaborators and owns neither of their jobs: a
    :class:`VoxdPlist` authors the plist content and file, and a
    :class:`LaunchctlAgent` runs every ``launchctl`` invocation, serialising the
    bootout/bootstrap race that leaves voxd down on a first restart. Every
    ``LaunchctlError`` leaves this backend as ``BackendCommandError``, the one
    failure type the lifecycle classifies.
    """

    __slots__ = ("_agent", "_plist")

    _plist: VoxdPlist
    _agent: LaunchctlAgent

    def __new__(cls, voxd_exec_args_fn: Callable[[], list[str]]) -> Self:
        self = super().__new__(cls)
        self._plist = VoxdPlist(_LABEL, _LAUNCHD_PLIST, voxd_exec_args_fn)
        self._agent = LaunchctlAgent(_LABEL, str(_LAUNCHD_PLIST))
        return self

    def plist_content(self) -> str:
        """Return the LaunchAgent plist XML the install would write."""
        return self._plist.content()

    @property
    def service_path(self) -> str:
        """Return the installed plist's path."""
        return str(self._plist.path)

    # -- the lifecycle seam -------------------------------------------------

    def is_installed(self) -> bool:
        """Return True when the plist is present."""
        return self._plist.exists()

    def is_active(self) -> bool:
        """Return True while the job is loaded or its process is still present."""
        return self._query("list", self._agent.is_loaded) or (
            self.main_pid() is not None
        )

    def is_active_within(self, within_s: float) -> bool:
        """Return True when launchd confirms the job is active inside *within_s*.

        For a bounded wait: the launchctl queries get no more than *within_s*
        between them, and a query that runs out of time confirms nothing.
        """
        return self._query("list", lambda: self._agent.running_within(within_s))

    def main_pid(self) -> int | None:
        """Return the job's pid; ``None`` when launchd reports no process."""
        return self._query("print", self._agent.pid)

    def installed_bind(self) -> str:
        """Return the ``VOXD_BIND`` written into the installed plist."""
        return self._plist.installed_bind()

    def request_stop(self) -> None:
        """Boot the job out; raise ``BackendCommandError`` if that fails."""
        self._drive("bootout", self._agent.bootout)

    def request_start(self) -> None:
        """Bootstrap and kickstart the job; raise ``BackendCommandError``."""
        self._drive("bootstrap", self._agent.start)

    # -- install / uninstall ----------------------------------------------

    def install(self, user: str) -> None:
        """Write the plist and bring the job up.  No sudo required."""
        del user  # a LaunchAgent always runs as the logged-in session user
        try:
            self._plist.write()
        except OSError as exc:
            argv = ["write", str(self._plist.path)]
            raise BackendCommandError.of(argv, 1, str(exc)) from exc
        self._drive("bootstrap", self._agent.start)
        logger.info("Bootstrapped and kickstarted %s into launchd", _LABEL)

    def uninstall(self) -> None:
        """Boot the job out and remove the plist."""
        if not self._plist.exists():
            logger.info(
                "No plist found at %s -- nothing to uninstall", self._plist.path
            )
            return
        self._drive("bootout", self._agent.bootout)
        self._plist.remove()

    @staticmethod
    def _query[T](verb: str, query: Callable[[], T]) -> T:
        """Run a launchctl query, translating its failure for the lifecycle."""
        try:
            return query()
        except LaunchctlError as exc:
            argv = ["launchctl", verb, _LABEL]
            raise BackendCommandError.of(argv, 1, str(exc)) from exc

    @staticmethod
    def _drive(verb: str, action: Callable[[], None]) -> None:
        """Run a launchctl *action*, translating its failure for the lifecycle."""
        try:
            action()
        except LaunchctlError as exc:
            raise BackendCommandError.of(
                ["launchctl", verb, _LABEL], 1, str(exc)
            ) from exc
