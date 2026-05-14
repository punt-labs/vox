"""macOS launchd backend for voxd system service."""

from __future__ import annotations

import html
import logging
import os
import subprocess
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Self

from punt_vox.paths import (
    log_dir as _paths_log_dir,
    user_state_dir as _paths_user_state_dir,
)
from punt_vox.service.process import ProcessManager

logger = logging.getLogger(__name__)

_LABEL = "com.punt-labs.voxd"
_LAUNCHD_DIR = Path("/Library/LaunchDaemons")
_LAUNCHD_PLIST = _LAUNCHD_DIR / f"{_LABEL}.plist"

_SUDO_NOTICE = (
    "Installing voxd as a system service. You may be prompted for your sudo password."
)


class LaunchdBackend:
    """Install, uninstall, stop, and query voxd under macOS launchd."""

    __slots__ = ("_process_mgr", "_voxd_exec_args_fn")

    _process_mgr: ProcessManager
    _voxd_exec_args_fn: Callable[[], list[str]]

    def __new__(
        cls,
        process_mgr: ProcessManager,
        voxd_exec_args_fn: Callable[[], list[str]],
    ) -> Self:
        self = super().__new__(cls)
        self._process_mgr = process_mgr
        self._voxd_exec_args_fn = voxd_exec_args_fn
        return self

    @staticmethod
    def _extra_env() -> dict[str, str]:
        """Return extra env vars to bake into the launchd plist."""
        extras: dict[str, str] = {}
        bind = os.environ.get("VOXD_BIND")
        if bind:
            extras["VOXD_BIND"] = bind
        return extras

    def plist_content(self, user: str) -> str:
        """Generate the launchd plist XML for *user*."""
        args = self._voxd_exec_args_fn()
        # Plist XML reads <string> values literally -- use html.escape for
        # XML-safe encoding (not shlex.quote, which adds shell quotes).
        program_args = "\n".join(
            f"        <string>{html.escape(a)}</string>" for a in args
        )
        log_dir = _paths_log_dir()
        stdout_log = html.escape(str(log_dir / "voxd-stdout.log"))
        stderr_log = html.escape(str(log_dir / "voxd-stderr.log"))
        path_value = html.escape(
            os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        )
        escaped_user = html.escape(user)
        extra_env = "".join(
            f"\n            <key>{html.escape(k)}</key>"
            f"\n            <string>{html.escape(v)}</string>"
            for k, v in self._extra_env().items()
        )
        return textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
              "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0">
            <dict>
                <key>Label</key>
                <string>{_LABEL}</string>
                <key>UserName</key>
                <string>{escaped_user}</string>
                <key>ProgramArguments</key>
                <array>
            {program_args}
                </array>
                <key>EnvironmentVariables</key>
                <dict>
                    <key>PATH</key>
                    <string>{path_value}</string>{extra_env}
                </dict>
                <key>RunAtLoad</key>
                <true/>
                <key>KeepAlive</key>
                <true/>
                <key>StandardOutPath</key>
                <string>{stdout_log}</string>
                <key>StandardErrorPath</key>
                <string>{stderr_log}</string>
            </dict>
            </plist>
        """)

    def stop(self) -> None:
        """Unload voxd from launchd if loaded.  Idempotent.

        Called as a pre-flight step by ``install()`` before
        ``ensure_port_free`` so launchd's ``KeepAlive=true`` does not
        respawn the daemon the instant the port-cleanup step kills it.
        """
        if not _LAUNCHD_PLIST.exists():
            return
        subprocess.run(
            ["sudo", "launchctl", "unload", "-w", str(_LAUNCHD_PLIST)],
            check=False,
        )
        logger.info("Unloaded any previously-loaded %s", _LABEL)

    def install(self, user: str) -> None:
        """Install the launchd plist.  Sudo is invoked three times."""
        state_root = _paths_user_state_dir()
        tmp_plist = state_root / "com.punt-labs.voxd.plist.tmp"
        tmp_plist.write_text(self.plist_content(user))
        logger.info("Wrote plist to %s", tmp_plist)

        try:
            subprocess.run(
                [
                    "sudo",
                    "install",
                    "-m",
                    "644",
                    "-o",
                    "root",
                    "-g",
                    "wheel",
                    str(tmp_plist),
                    str(_LAUNCHD_PLIST),
                ],
                check=True,
            )
            logger.info("Installed %s", _LAUNCHD_PLIST)

            subprocess.run(
                ["sudo", "launchctl", "load", "-w", str(_LAUNCHD_PLIST)],
                check=True,
            )
            logger.info("Loaded %s into launchd", _LABEL)

            subprocess.run(
                ["sudo", "launchctl", "kickstart", "-k", f"system/{_LABEL}"],
                check=True,
            )
            logger.info("Kickstarted %s", _LABEL)
        finally:
            try:
                tmp_plist.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove tmp plist %s", tmp_plist)

    def uninstall(self) -> None:
        """Remove the launchd plist and kill any stale daemon."""
        if _LAUNCHD_PLIST.exists():
            logger.warning(_SUDO_NOTICE)
            subprocess.run(
                ["sudo", "launchctl", "unload", "-w", str(_LAUNCHD_PLIST)],
                check=False,
            )
            subprocess.run(
                ["sudo", "rm", "-f", str(_LAUNCHD_PLIST)],
                check=True,
            )
            logger.info("Removed %s", _LAUNCHD_PLIST)
        else:
            logger.info("No plist found at %s — nothing to uninstall", _LAUNCHD_PLIST)
        self._process_mgr.kill_stale_daemon()

    def status(self) -> bool:
        """Return True if voxd is registered and running under launchd."""
        result = subprocess.run(
            ["launchctl", "list", _LABEL],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
