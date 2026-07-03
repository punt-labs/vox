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

from punt_vox.paths import log_dir as _paths_log_dir
from punt_vox.service.process import ProcessManager

logger = logging.getLogger(__name__)

_LABEL = "com.punt-labs.voxd"
_LAUNCHD_DIR = Path.home() / "Library" / "LaunchAgents"
_LAUNCHD_PLIST = _LAUNCHD_DIR / f"{_LABEL}.plist"


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
    def _gui_domain() -> str:
        """Return the launchd GUI domain target for the current user."""
        return f"gui/{os.getuid()}"

    @staticmethod
    def _extra_env() -> dict[str, str]:
        """Return extra env vars to bake into the launchd plist."""
        extras: dict[str, str] = {}
        bind = os.environ.get("VOXD_BIND")
        if bind:
            extras["VOXD_BIND"] = bind
        return extras

    def plist_content(self) -> str:
        """Generate the LaunchAgent plist XML.

        LaunchAgents run as the session user by default -- no ``UserName``
        key is needed (and it is invalid for agents).  ``ProcessType=Interactive``
        prevents App Nap-style throttling on the windowless daemon.
        """
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
                <key>ProcessType</key>
                <string>Interactive</string>
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
        """Bootout voxd from launchd if loaded.  Idempotent.

        Called as a pre-flight step by ``install()`` before
        ``ensure_port_free`` so launchd's ``KeepAlive=true`` does not
        respawn the daemon the instant the port-cleanup step kills it.
        """
        if not _LAUNCHD_PLIST.exists():
            return
        domain = self._gui_domain()
        result = subprocess.run(
            ["launchctl", "bootout", f"{domain}/{_LABEL}"],
            check=False,
        )
        if result.returncode != 0:
            logger.debug(
                "bootout %s exited %d (service may not be loaded)",
                _LABEL,
                result.returncode,
            )
        else:
            logger.info("Booted out %s", _LABEL)

    def install(self) -> None:
        """Install the LaunchAgent plist.  No sudo required."""
        _LAUNCHD_DIR.mkdir(parents=True, exist_ok=True)
        _LAUNCHD_PLIST.write_text(self.plist_content())
        _LAUNCHD_PLIST.chmod(0o644)
        logger.info("Wrote plist to %s", _LAUNCHD_PLIST)

        domain = self._gui_domain()
        subprocess.run(
            ["launchctl", "bootstrap", domain, str(_LAUNCHD_PLIST)],
            check=True,
        )
        logger.info("Bootstrapped %s into launchd", _LABEL)

        subprocess.run(
            ["launchctl", "kickstart", "-k", f"{domain}/{_LABEL}"],
            check=True,
        )
        logger.info("Kickstarted %s", _LABEL)

    def uninstall(self) -> bool:
        """Remove the LaunchAgent plist; return ``kill_stale_daemon()``'s result."""
        if _LAUNCHD_PLIST.exists():
            domain = self._gui_domain()
            result = subprocess.run(
                ["launchctl", "bootout", f"{domain}/{_LABEL}"],
                check=False,
            )
            if result.returncode != 0:
                logger.warning(
                    "bootout %s exited %d -- removing plist anyway",
                    _LABEL,
                    result.returncode,
                )
            _LAUNCHD_PLIST.unlink(missing_ok=True)
            logger.info("Removed %s", _LAUNCHD_PLIST)
        else:
            logger.info("No plist found at %s -- nothing to uninstall", _LAUNCHD_PLIST)
        return self._process_mgr.kill_stale_daemon()

    def status(self) -> bool:
        """Return True if voxd is registered and running under launchd."""
        result = subprocess.run(
            ["launchctl", "list", _LABEL],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
