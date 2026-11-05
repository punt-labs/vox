"""The ``vox daemon`` CLI verbs -- install, uninstall, start, stop, restart, status.

The daemon subcommand group manages the ``voxd`` system service on the local
host (macOS LaunchAgent / Linux systemd). :class:`DaemonCli` is a humble
object: each verb parses ``--json`` / ``--verbose`` / ``--quiet`` through the
shared :class:`OutputFlags`, dispatches to :mod:`punt_vox.service`, and
formats via the shared :class:`OutputFormatter`. ``start``, ``stop`` and
``restart`` each emit the :class:`LifecycleReport` the service layer returns
and exit with its code.
Every subcommand answers ``--json`` (parity with ``vox status``, ``vox voices``,
``vox music``, ``vox rec``); ``vox daemon status --json`` emits the fields the
daemon reports over ``health()`` so a caller reads the same shape whether it
asks the CLI or drives the daemon directly. ``status`` reaches the daemon
through the same lifecycle probe as ``start`` and ``stop``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Annotated, Self, final

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.client_sync import VoxClientSync
from punt_vox.service import (
    DaemonLifecycle,
    install as svc_install,
    uninstall as svc_uninstall,
)
from punt_vox.service.backend import BackendCommandError, PortProbeError
from punt_vox.service.lifecycle_report import LifecycleRefusedError

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.cli_io import OutputFlags
    from punt_vox.output_formatter import OutputFormatter
    from punt_vox.service.lifecycle_report import LifecycleReport
    from punt_vox.types_health import HealthStatus

__all__ = ["DaemonCli", "build_daemon_app"]

# Either one names a daemon other than the local service, so ``status`` asks
# it through the client, as ``vox status`` and ``vox doctor`` do.
_ENDPOINT_ENV = ("VOXD_HOST", "VOXD_PORT")

# Redeclared per-verb so ``--json`` parses after the subcommand as well as
# before it (matching the music/rec/enablement groups).
_JsonOutput = Annotated[bool, typer.Option("--json", help="Output JSON.")]
_Verbose = Annotated[
    bool, typer.Option("--verbose", "-v", help="Enable debug logging.")
]
_Quiet = Annotated[
    bool, typer.Option("--quiet", "-q", help="Suppress non-JSON output.")
]


@final
class DaemonCli:
    """The ``vox daemon`` verbs bound onto a nested typer group.

    The output flags are shared with the top-level app (one accumulating
    :class:`OutputFlags`, one :class:`OutputFormatter`), so a ``--json`` passed
    at either position selects the same mode.
    """

    __slots__ = ("_flags", "_formatter")

    _formatter: OutputFormatter
    _flags: OutputFlags

    def __new__(cls, formatter: OutputFormatter, flags: OutputFlags) -> Self:
        self = super().__new__(cls)
        self._formatter = formatter
        self._flags = flags
        return self

    def install(
        self,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Register vox as a system service (launchd/systemd).

        Run as your normal user, NOT under ``sudo``. On macOS no sudo is
        needed -- the LaunchAgent installs to ``~/Library/LaunchAgents/``.
        On Linux, vox will prompt once for your sudo password to place
        the systemd unit. Running under sudo yourself would cause per-user
        state to land under ``/root/.punt-labs/vox/`` -- wrong on both
        platforms.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        self._service(svc_install, "installed", "Install")

    def uninstall(
        self,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Remove the vox system service.

        Symmetric with ``vox daemon install``. Stops the running voxd,
        removes the launchd plist (macOS) or systemd unit (Linux), and
        reports what was removed. Runs as your normal user; on Linux vox
        prompts once for your sudo password to touch the systemd unit.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        self._service(svc_uninstall, "uninstalled", "Uninstall")

    def start(
        self,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Start the installed voxd service and verify it serves this wheel.

        Leaves a running voxd alone (``already running``). Runs as your normal
        user, NOT under ``sudo``; on Linux, vox runs ``sudo -n systemctl start
        voxd`` and prompts for your password only when a terminal is present.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        self._run(lambda lifecycle: lifecycle.start())

    def stop(
        self,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Stop voxd and leave it stopped until ``start`` or the next boot.

        The service stays installed and enabled; ``vox daemon uninstall``
        keeps it down across reboots. Runs as your normal user, NOT under
        ``sudo``.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        self._run(lambda lifecycle: lifecycle.stop())

    def restart(
        self,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Stop voxd, then start it, and verify the new wheel is serving.

        Use this after ``uv tool upgrade punt-vox`` so the running daemon
        picks up the new wheel: an upgrade replaces the on-disk binary but
        does not cycle the long-running voxd process. Runs as your normal
        user, NOT under ``sudo``.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        self._run(lambda lifecycle: lifecycle.restart())

    def _service(self, action: Callable[[], str], key: str, label: str) -> None:
        """Run install or uninstall; report ``{key: bool}`` and its message."""
        try:
            result = action()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            self._formatter.emit(
                {key: False, "reason": str(exc)}, f"{label} failed: {exc}"
            )
            raise typer.Exit(code=code) from exc
        self._formatter.emit({key: True, "message": result}, result)

    def _run(self, verb: Callable[[DaemonLifecycle], LifecycleReport]) -> None:
        """Run one lifecycle verb, emit its report, and exit with its code."""
        report = verb(self._lifecycle())
        self._formatter.emit(report.to_json(), report.text())
        if report.exit_code:
            raise typer.Exit(code=report.exit_code)

    def status(
        self,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Show the voxd daemon's health -- reachable, port, uptime, version.

        With ``VOXD_HOST`` or ``VOXD_PORT`` set, it queries that configured
        daemon through the client -- honoring ``VOXD_HOST`` / ``VOXD_PORT`` /
        ``VOXD_TOKEN`` -- matching ``vox status`` and ``vox doctor``. With
        neither set it reads the local service the way ``start`` and ``stop``
        do: the installed unit's bind and port 8421, the token only from
        ``serve.token``, sent only to a visible voxd on the port, so a missing
        ``serve.port`` does not hide a running daemon.
        ``--json`` returns the daemon health snapshot (``status``, ``port``,
        ``pid``, ``daemon_version``, ``uptime_seconds``, ``queued``,
        ``active_sessions``) -- the same shape ``mic:status`` reports for the
        daemon block. No answer is ``not_running``; a port or unit vox cannot
        inspect is ``unknown``. Both exit 1.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        try:
            health = self._read_health()
        except (
            VoxdConnectionError,
            VoxdProtocolError,
            PortProbeError,
            BackendCommandError,
        ) as exc:
            # No answer is "not running"; a probe vox could not run says
            # nothing about voxd, so it is "unknown", never "not running".
            down = isinstance(exc, VoxdConnectionError | VoxdProtocolError)
            state = "not_running" if down else "unknown"
            self._formatter.emit(
                {"status": state, "reason": str(exc)},
                f"Daemon: {state.replace('_', ' ')} ({exc})",
            )
            raise typer.Exit(code=1) from exc

        payload: dict[str, object] = {
            "status": health.status,
            "port": health.port,
            "pid": health.pid,
            # ``provider`` is not on the health payload -- the daemon has
            # no provider of its own; per-provider readiness moves to the
            # ``provider_status`` op (design §3.6, delivered by PR 3).
            "daemon_version": health.daemon_version,
            "uptime_seconds": health.uptime_seconds,
            "queued": health.queued,
            "active_sessions": health.active_sessions,
        }
        text = (
            f"Daemon: {health.status} on port {health.port}\n"
            f"  Uptime:   {health.uptime_seconds}s\n"
            f"  Sessions: {health.active_sessions}"
        )
        self._formatter.emit(payload, text)

    @classmethod
    def _read_health(cls) -> HealthStatus:
        """Return health from the configured daemon, else the local service.

        ``VOXD_HOST`` or ``VOXD_PORT`` names a daemon the operator chose
        (possibly remote), so the client reaches it as ``vox status`` does;
        otherwise the lifecycle probe reads the installed service.
        """
        if any(os.environ.get(name, "").strip() for name in _ENDPOINT_ENV):
            return VoxClientSync().health()
        return cls._lifecycle().health()

    @staticmethod
    def _lifecycle() -> DaemonLifecycle:
        """Return this host's lifecycle; root or an unsupported OS is exit 2."""
        try:
            return DaemonLifecycle.for_this_host()
        except LifecycleRefusedError as exc:
            raise typer.BadParameter(str(exc)) from exc


def build_daemon_app(formatter: OutputFormatter, flags: OutputFlags) -> typer.Typer:
    """Return the ``vox daemon`` Typer group with bound verbs (no wrappers)."""
    cli = DaemonCli(formatter, flags)
    app = typer.Typer(
        help="Manage the vox daemon service.",
        no_args_is_help=True,
    )
    app.command("install")(cli.install)
    app.command("uninstall")(cli.uninstall)
    app.command("start")(cli.start)
    app.command("stop")(cli.stop)
    app.command("restart")(cli.restart)
    app.command("status")(cli.status)
    return app
