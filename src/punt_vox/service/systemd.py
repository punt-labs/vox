"""Linux systemd backend for the voxd system service."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.paths import user_state_dir as _paths_user_state_dir
from punt_vox.service.backend import BackendCommandError, PrivilegeDeniedError
from punt_vox.service.systemd_unit import SystemdUnit

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from punt_vox.service.backend import CommandRunner, TerminalCheck

logger = logging.getLogger(__name__)

_SYSTEMD_DIR = Path("/etc/systemd/system")
_SYSTEMD_UNIT = _SYSTEMD_DIR / "voxd.service"

_SUDO_NOTICE = (
    "Running systemctl for voxd through sudo. You may be prompted for your "
    "sudo password."
)


@final
class PrivilegedRun:
    """Run one ``systemctl <verb> voxd`` through sudo, never probing privilege.

    ``sudo -n`` runs first and never prompts, so a scoped NOPASSWD rule or a
    cached timestamp is the whole path. Exit 4 (access denied) and 5 (unit not
    loaded) come only from systemctl. Exit 1 is ambiguous -- sudo wanting a
    password, or a generic systemctl failure -- and no probe separates them
    (``sudo -n -l`` reports permission, not password-free-ness), so: with no
    terminal it is a refusal carrying sudo's stderr verbatim; with one, the
    interactive ``sudo`` runs exactly once. Classification reads exit codes
    only, never the localised stderr text.
    """

    __slots__ = ("_has_tty", "_run")

    _ACCESS_DENIED: ClassVar[int] = 4
    _NOT_LOADED: ClassVar[int] = 5
    _AMBIGUOUS: ClassVar[int] = 1
    _TAIL_CHARS: ClassVar[int] = 400

    _run: CommandRunner
    _has_tty: TerminalCheck

    def __new__(cls, run: CommandRunner, has_tty: TerminalCheck) -> Self:
        self = super().__new__(cls)
        self._run = run
        self._has_tty = has_tty
        return self

    @classmethod
    def on_this_host(cls) -> Self:
        """Return the runner that drives the real ``sudo``."""
        return cls(cls._subprocess_run, cls._has_terminal)

    def __call__(self, verb: str) -> None:
        """Run ``systemctl <verb> voxd``; raise a typed error if it fails."""
        argv = ["systemctl", verb, "voxd"]
        code, stderr = self._run(["sudo", "-n", *argv], capture=True)
        if code == 0:
            return
        tail = stderr.strip()[-self._TAIL_CHARS :]
        self._raise_systemctl(argv, code, tail)
        if code != self._AMBIGUOUS:
            raise BackendCommandError.of(["sudo", "-n", *argv], code, tail)
        if not self._has_tty():
            msg = (
                f"sudo could not run `systemctl {verb} voxd` non-interactively, "
                f"or systemctl failed (see detail); run `vox daemon {verb}` from "
                f"a terminal, or grant NOPASSWD for `systemctl {verb} voxd`. "
                f"detail: {tail}"
            )
            raise PrivilegeDeniedError(msg)
        logger.warning(_SUDO_NOTICE)
        code, _ = self._run(["sudo", *argv], capture=False)
        if code == 0:
            return
        self._raise_systemctl(argv, code, "")
        raise BackendCommandError.of(
            ["sudo", *argv], code, "sudo or systemctl refused; see the terminal"
        )

    def _raise_systemctl(self, argv: list[str], code: int, tail: str) -> None:
        """Raise for the two exit codes only systemctl produces."""
        if code == self._ACCESS_DENIED:
            msg = f"{' '.join(argv)}: access denied (exit 4). {tail}".strip()
            raise PrivilegeDeniedError(msg)
        if code == self._NOT_LOADED:
            raise BackendCommandError.of(
                argv, code, "voxd.service not loaded; run `vox daemon install`"
            )

    @staticmethod
    def _subprocess_run(argv: Sequence[str], *, capture: bool) -> tuple[int, str]:
        """Run *argv*; capture stderr, or leave it on the terminal for sudo."""
        try:
            result = subprocess.run(
                list(argv),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE if capture else None,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise BackendCommandError.of(argv, 127, str(exc)) from exc
        return result.returncode, result.stderr or ""

    @staticmethod
    def _has_terminal() -> bool:
        """Return True when a controlling terminal can carry a sudo prompt."""
        try:
            fd = os.open("/dev/tty", os.O_RDWR)
        except OSError:
            return False
        os.close(fd)
        return True


@final
class SystemdBackend:
    """Install, query, and drive voxd under Linux systemd."""

    __slots__ = ("_privileged", "_unit")

    # States in which the unit has a process, or is about to or is losing one.
    _LIVE_STATES: ClassVar[frozenset[str]] = frozenset(
        {"active", "activating", "reloading", "deactivating"}
    )

    _unit: SystemdUnit
    _privileged: PrivilegedRun

    def __new__(
        cls,
        voxd_exec_args_fn: Callable[[], list[str]],
        privileged: PrivilegedRun,
    ) -> Self:
        self = super().__new__(cls)
        self._unit = SystemdUnit(_SYSTEMD_UNIT, voxd_exec_args_fn)
        self._privileged = privileged
        return self

    @property
    def unit(self) -> SystemdUnit:
        """Return the unit file this backend installs."""
        return self._unit

    @property
    def service_path(self) -> str:
        """Return the installed unit's path."""
        return str(self._unit.path)

    # -- the lifecycle seam -------------------------------------------------

    def is_installed(self) -> bool:
        """Return True when the unit file is present."""
        return self._unit.exists()

    def is_active(self) -> bool:
        """Return True while systemd runs, starts, reloads, or stops voxd."""
        state = self._systemctl("is-active", "voxd", check=False)
        return state in self._LIVE_STATES

    def main_pid(self) -> int | None:
        """Return systemd's ``MainPID``; ``None`` when it reports 0 (none)."""
        raw = self._systemctl("show", "-p", "MainPID", "--value", "voxd", check=True)
        try:
            pid = int(raw)
        except ValueError as exc:
            argv = ["systemctl", "show", "-p", "MainPID", "--value", "voxd"]
            raise BackendCommandError.of(argv, 0, f"unparseable {raw!r}") from exc
        return pid or None

    def installed_bind(self) -> str:
        """Return the ``VOXD_BIND`` written into the installed unit."""
        return self._unit.installed_bind()

    def request_stop(self) -> None:
        """Ask systemd to stop voxd; raise a typed error on failure."""
        self._privileged("stop")

    def request_start(self) -> None:
        """Ask systemd to start voxd; raise a typed error on failure."""
        self._privileged("start")

    # -- install / uninstall ----------------------------------------------

    def install(self, user: str) -> None:
        """Install the unit, enable it, and (re)start voxd.  Sudo runs 4 times."""
        logger.warning(_SUDO_NOTICE)
        tmp_unit = _paths_user_state_dir() / "voxd.service.tmp"
        tmp_unit.write_text(self._unit.content(user))
        logger.info("Wrote unit to %s", tmp_unit)
        target = str(self._unit.path)
        try:
            for argv in (
                ["install", "-m", "644", "-o", "root", "-g", "root"],
                ["systemctl", "daemon-reload"],
                ["systemctl", "enable", "voxd"],
                ["systemctl", "restart", "voxd"],
            ):
                extra = [str(tmp_unit), target] if argv[0] == "install" else []
                subprocess.run(["sudo", *argv, *extra], check=True)
            logger.info("Installed %s, enabled and restarted voxd.service", target)
        finally:
            try:
                tmp_unit.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove tmp unit %s", tmp_unit)

    def uninstall(self) -> None:
        """Disable voxd and remove the unit."""
        if not self._unit.exists():
            logger.info("No unit found at %s — nothing to uninstall", self._unit.path)
            return
        logger.warning(_SUDO_NOTICE)
        subprocess.run(["sudo", "systemctl", "disable", "--now", "voxd"], check=False)
        subprocess.run(["sudo", "rm", "-f", str(self._unit.path)], check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
        logger.info("Removed %s", self._unit.path)

    @staticmethod
    def _systemctl(*args: str, check: bool) -> str:
        """Run an unprivileged ``systemctl`` query and return its stdout."""
        argv = ["systemctl", *args]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise BackendCommandError.of(argv, 127, str(exc)) from exc
        if check and result.returncode != 0:
            raise BackendCommandError.of(argv, result.returncode, result.stderr.strip())
        return result.stdout.strip()
