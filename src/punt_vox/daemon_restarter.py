"""Daemon restart orchestration: stop, wait, start, verify."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from typing import Self

import typer

from punt_vox.client import VoxClientSync, VoxdConnectionError, VoxdProtocolError
from punt_vox.output_formatter import OutputFormatter
from punt_vox.paths import installed_version, log_dir

__all__ = ["DaemonRestarter"]

logger = logging.getLogger(__name__)


class DaemonRestarter:
    """Orchestrate a voxd daemon restart with health verification."""

    __slots__ = ("_formatter",)

    _formatter: OutputFormatter

    def __new__(cls, formatter: OutputFormatter) -> Self:
        self = super().__new__(cls)
        self._formatter = formatter
        return self

    def run(self) -> None:
        """Execute the full stop-wait-start-verify sequence."""
        self._refuse_unsupported_platform()
        plat = self._detect_platform()
        self._stop(plat)
        self._wait_port_free()
        self._start(plat)
        self._verify_health()

    # -- phases --------------------------------------------------------------

    def _refuse_unsupported_platform(self) -> None:
        """Refuse Windows and root execution."""
        if sys.platform == "win32":
            raise typer.BadParameter(
                "vox daemon restart is only supported on macOS and Linux; "
                "Windows does not have a comparable system service manager."
            )
        if os.geteuid() == 0:
            raise typer.BadParameter(
                "vox daemon restart must be run as your normal user, not root "
                "or sudo. vox will prompt for your sudo password when it drives "
                "systemctl/launchctl. Re-run without sudo:\n\n"
                "    vox daemon restart\n"
            )

    @staticmethod
    def _detect_platform() -> str:
        """Return the service platform identifier."""
        from punt_vox.service import detect_platform  # noqa: PLC0415

        return detect_platform()

    @staticmethod
    def _stop(plat: str) -> None:
        """Stop the daemon via the platform service manager."""
        from punt_vox.service import stop_daemon  # noqa: PLC0415

        logger.info("Stopping voxd via service manager...")
        stop_daemon(plat)

    @staticmethod
    def _wait_port_free() -> None:
        """Wait for the daemon port to become available."""
        from punt_vox.service import ensure_port_free  # noqa: PLC0415

        logger.info("Waiting for port to free...")
        try:
            ensure_port_free()
        except SystemExit as exc:
            reason = str(exc) if exc.code not in (0, None) else ""
            detail = f": {reason}" if reason else ""
            typer.echo(
                f"Error: port still occupied after service manager stop{detail}\n"
                f"Check the logs at {log_dir() / 'voxd.log'}",
                err=True,
            )
            raise typer.Exit(code=1) from exc

    @staticmethod
    def _start(plat: str) -> None:
        """Start the daemon via the platform service manager."""
        logger.info("Starting voxd via service manager...")
        try:
            if plat == "macos":
                subprocess.run(
                    [  # noqa: S607 -- sudo/launchctl are intentional
                        "sudo",
                        "launchctl",
                        "load",
                        "-w",
                        "/Library/LaunchDaemons/com.punt-labs.voxd.plist",
                    ],
                    check=True,
                )
                subprocess.run(
                    [  # noqa: S607 -- sudo/launchctl are intentional
                        "sudo",
                        "launchctl",
                        "kickstart",
                        "-k",
                        "system/com.punt-labs.voxd",
                    ],
                    check=True,
                )
            else:
                subprocess.run(
                    ["sudo", "systemctl", "start", "voxd"],  # noqa: S607
                    check=True,
                )
        except subprocess.CalledProcessError as exc:
            log_path = log_dir() / "voxd.log"
            typer.echo(
                f"Error: service manager failed to start voxd: {exc}\n"
                f"Check the logs at {log_path}",
                err=True,
            )
            raise typer.Exit(code=1) from exc

    def _verify_health(self) -> None:
        """Poll for daemon health and verify version matches the wheel."""
        logger.info("Waiting for voxd to come back up...")
        deadline = time.monotonic() + 5.0
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            try:
                health = VoxClientSync().health()
            except (VoxdConnectionError, VoxdProtocolError) as exc:
                last_exc = exc
                time.sleep(0.2)
                continue

            pid = health.get("pid", "?")
            port = health.get("port", "?")
            running_version = str(health.get("daemon_version", ""))
            wheel_version = installed_version()
            log_path = log_dir() / "voxd.log"

            if not running_version:
                typer.echo(
                    "Error: restarted daemon did not report a version. Expected "
                    f"{wheel_version}. Check {log_path} — the daemon may be "
                    "running pre-feat/install-verify-hardening code that cannot "
                    "self-report its version.",
                    err=True,
                )
                raise typer.Exit(code=1)
            if running_version != wheel_version:
                typer.echo(
                    f"Error: daemon reports version {running_version} but wheel is "
                    f"{wheel_version}. The restart did not pick up the new code. "
                    f"Check {log_path}.",
                    err=True,
                )
                raise typer.Exit(code=1)

            self._formatter.emit(
                {
                    "restarted": True,
                    "pid": pid,
                    "port": port,
                    "daemon_version": running_version,
                },
                f"voxd restarted (pid={pid}, listening on port {port}, "
                f"version {running_version})",
            )
            return

        log_path = log_dir() / "voxd.log"
        reason = f": {last_exc}" if last_exc is not None else ""
        typer.echo(
            f"Error: voxd did not come back up within 5s{reason}\n"
            f"Check the logs at {log_path}",
            err=True,
        )
        raise typer.Exit(code=1)
