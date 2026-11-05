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
asks the CLI or drives the daemon directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Self, final

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.client_sync import VoxClientSync
from punt_vox.service import (
    DaemonLifecycle,
    install as svc_install,
    uninstall as svc_uninstall,
)
from punt_vox.service.lifecycle_report import LifecycleRefusedError

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.cli_io import OutputFlags
    from punt_vox.output_formatter import OutputFormatter
    from punt_vox.service.lifecycle_report import LifecycleReport

__all__ = ["DaemonCli", "build_daemon_app"]

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
        try:
            report = verb(DaemonLifecycle.for_this_host())
        except LifecycleRefusedError as exc:
            raise typer.BadParameter(str(exc)) from exc
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

        Routes through the client so it queries the configured daemon --
        honoring ``VOXD_HOST`` / ``VOXD_PORT`` / ``VOXD_TOKEN`` -- rather than
        a hardcoded ``127.0.0.1``, matching ``vox status`` and ``vox doctor``.
        ``--json`` returns the daemon health snapshot (``status``, ``port``,
        ``pid``, ``provider``, ``daemon_version``, ``uptime_seconds``,
        ``queued``, ``active_sessions``) -- the same shape ``mic:status``
        reports for the daemon block.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        try:
            health = VoxClientSync().health()
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            not_running: dict[str, object] = {
                "status": "not_running",
                "reason": str(exc),
            }
            self._formatter.emit(not_running, f"Daemon: not running ({exc})")
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
