"""Linux systemd backend for the voxd system service."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.paths import user_state_dir as _paths_user_state_dir
from punt_vox.service.backend import BackendCommandError, PrivilegeDeniedError
from punt_vox.service.query_answer import QueryAnswer
from punt_vox.service.systemd_unit import SystemdUnit

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from punt_vox.service.backend import CommandRunner, TerminalCheck

logger = logging.getLogger(__name__)

_SYSTEMD_DIR = Path("/etc/systemd/system")
_SYSTEMD_UNIT = _SYSTEMD_DIR / "voxd.service"

# Bounds on every systemctl call, so no verb outlives its deadline by a hung
# command: a query; ``sudo -n`` (which ``systemctl stop`` blocks until the
# unit's processes exit); and an interactive sudo waiting on a password.
_QUERY_TIMEOUT_S = 10.0
# The exit code a query that ran out of time reports, as timeout(1) does.
_TIMED_OUT = 124
_SUDO_N_TIMEOUT_S = 60.0
_SUDO_PROMPT_TIMEOUT_S = 120.0

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
        # A captured ``sudo -n`` never prompts; the interactive call waits on
        # the operator's password, so it gets the longer bound.
        timeout = _SUDO_N_TIMEOUT_S if capture else _SUDO_PROMPT_TIMEOUT_S
        try:
            result = subprocess.run(
                list(argv),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE if capture else None,
                text=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = f"could not run: {exc}"
            raise BackendCommandError.of(argv, 127, detail) from exc
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

    # Every ActiveState systemd defines; any other is-active output is an error.
    _KNOWN_STATES: ClassVar[frozenset[str]] = _LIVE_STATES | frozenset(
        {"inactive", "failed", "maintenance", "refreshing"}
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
        """Return True while systemd runs, starts, reloads, or stops voxd.

        ``is-active`` exits non-zero for every state but ``active``, so the
        printed state decides; a state systemd does not define (a bus error,
        no output) raises rather than reading as "inactive".
        """
        return self._active_state(_QUERY_TIMEOUT_S)

    def is_active_within(self, within_s: float) -> QueryAnswer:
        """Ask systemd, inside *within_s*, whether voxd is live.

        For a bounded wait: the query gets at most *within_s*, and one that
        runs out of time confirms nothing -- the answer names the command that
        did not answer instead of failing the wait. Any other query failure
        still raises.
        """
        try:
            return QueryAnswer.of(holds=self._active_state(within_s))
        except BackendCommandError as exc:
            if exc.code == _TIMED_OUT:
                return QueryAnswer.no_answer("systemctl is-active voxd")
            raise

    def _active_state(self, within_s: float) -> bool:
        """Read ``is-active`` within *within_s*; an unknown state raises."""
        state = self._systemctl("is-active", "voxd", check=False, within_s=within_s)
        if state not in self._KNOWN_STATES:
            argv = ["systemctl", "is-active", "voxd"]
            raise BackendCommandError.of(argv, 1, f"unexpected state {state!r}")
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
        """Install the unit, enable it, and (re)start voxd.  Sudo runs 4 times.

        Raises ``BackendCommandError`` naming the step that failed.
        """
        logger.warning(_SUDO_NOTICE)
        tmp_unit = _paths_user_state_dir() / "voxd.service.tmp"
        try:
            tmp_unit.write_text(self._unit.content(user))
        except OSError as exc:
            raise BackendCommandError.of(["write", str(tmp_unit)], 1, str(exc)) from exc
        logger.info("Wrote unit to %s", tmp_unit)
        target = str(self._unit.path)
        try:
            install = ["install", "-m", "644", "-o", "root", "-g", "root"]
            self._sudo(*install, str(tmp_unit), target)
            self._sudo("systemctl", "daemon-reload")
            self._sudo("systemctl", "enable", "voxd")
            self._sudo("systemctl", "restart", "voxd")
            logger.info("Installed %s, enabled and restarted voxd.service", target)
        finally:
            try:
                tmp_unit.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove tmp unit %s", tmp_unit)

    def uninstall(self) -> None:
        """Disable voxd and remove the unit; raise naming any step that fails."""
        if not self._unit.exists():
            logger.info("No unit found at %s — nothing to uninstall", self._unit.path)
            return
        logger.warning(_SUDO_NOTICE)
        self._sudo("systemctl", "disable", "--now", "voxd")
        self._sudo("rm", "-f", str(self._unit.path))
        self._sudo("systemctl", "daemon-reload")
        logger.info("Removed %s", self._unit.path)

    @staticmethod
    def _sudo(*command: str) -> None:
        """Run ``sudo <command>``, bounded; raise ``BackendCommandError``."""
        argv = ["sudo", *command]
        try:
            subprocess.run(
                argv,
                check=True,
                stderr=subprocess.PIPE,
                text=True,
                timeout=_SUDO_PROMPT_TIMEOUT_S,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            raise BackendCommandError.of(argv, exc.returncode, detail) from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = f"could not run: {exc}"
            raise BackendCommandError.of(argv, 127, detail) from exc

    @staticmethod
    def _systemctl(*args: str, check: bool, within_s: float = _QUERY_TIMEOUT_S) -> str:
        """Run an unprivileged ``systemctl`` query, bounded; return its stdout.

        A query still running after *within_s* raises ``BackendCommandError``
        with code ``_TIMED_OUT``, so a bounded wait can tell "out of time" from
        a failed query.
        """
        argv = ["systemctl", *args]
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=within_s,
            )
        except subprocess.TimeoutExpired as exc:
            detail = f"could not run: {exc}"
            raise BackendCommandError.of(argv, _TIMED_OUT, detail) from exc
        except OSError as exc:
            detail = f"could not run: {exc}"
            raise BackendCommandError.of(argv, 127, detail) from exc
        if check and result.returncode != 0:
            raise BackendCommandError.of(argv, result.returncode, result.stderr.strip())
        return result.stdout.strip()
