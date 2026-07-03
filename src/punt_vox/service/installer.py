"""Top-level service installer composing all backends."""

from __future__ import annotations

import getpass
import ipaddress
import logging
import os
import platform
import sys
import time
from pathlib import Path
from typing import Self

from punt_vox.client import (
    VoxClientSync,
    VoxdConnectionError,
    VoxdProtocolError,
    read_token_file,
)
from punt_vox.paths import (
    ensure_user_dirs as _paths_ensure_user_dirs,
    keys_env_file as _paths_keys_env_file,
    log_dir as _paths_log_dir,
    user_state_dir as _paths_user_state_dir,
)
from punt_vox.service.keys_env import KeysEnvWriter
from punt_vox.service.launchd import (
    _LAUNCHD_PLIST,  # pyright: ignore[reportPrivateUsage]
    LaunchdBackend,
)
from punt_vox.service.process import DEFAULT_PORT, ProcessManager
from punt_vox.service.systemd import (
    _SYSTEMD_UNIT,  # pyright: ignore[reportPrivateUsage]
    SystemdBackend,
)

logger = logging.getLogger(__name__)

# Sudo notice -- Linux only (macOS LaunchAgent installs need no sudo).
_SUDO_NOTICE = (
    "Installing voxd as a system service. You may be prompted for your sudo password."
)

# voxd health poll after install. launchctl/systemctl registration proves the
# job is scheduled, not that voxd bound its port and stayed up; a daemon that
# dies on startup (bad env, missing binary) would otherwise be reported
# "running". Poll the health endpoint until it answers or the deadline lapses.
_HEALTH_DEADLINE_S = 5.0
_HEALTH_POLL_INTERVAL_S = 0.2


class _HealthTarget:
    """Resolve host, port, and token of the voxd instance just installed.

    The health poll must reach *the daemon the backend actually started* --
    never whatever the install shell's ``VOXD_*`` env vars point at. All
    three connection values are pinned from authoritative sources, bypassing
    ``VoxClientSync``'s env/run-file resolution: ``port`` is ``DEFAULT_PORT``
    (baked into every unit by ``_voxd_exec_args``); ``host`` is the bind the
    *backend* applied, gated as its unit gates it (``_effective_bind``);
    ``token`` is read from voxd's ``serve.token`` run file, never a stray
    shell ``VOXD_TOKEN``.
    """

    __slots__ = ("_host", "_port")

    _host: str
    _port: int

    def __new__(cls, platform: str) -> Self:
        self = super().__new__(cls)
        self._host = self._resolve_host(platform)
        self._port = DEFAULT_PORT
        return self

    @staticmethod
    def _effective_bind(platform: str) -> str:
        """Return the ``VOXD_BIND`` value the installed unit passes to voxd.

        The install-shell value is not necessarily what voxd receives, because
        each backend gates ``VOXD_BIND`` differently: systemd embeds it only
        when ``safe_systemd_value`` accepts it (a rejected value is dropped, so
        voxd binds its ``DEFAULT_HOST`` loopback); launchd embeds it verbatim
        when non-empty, with no gate. The gate runs on the *raw* env value to
        match the backends -- a trailing ``\\n`` fails ``safe_systemd_value``
        yet vanishes under ``.strip()``, so stripping before gating would
        disagree with the unit and resolve the wrong host.
        """
        raw = os.environ.get("VOXD_BIND")
        if not raw:
            return ""
        if platform == "linux" and not SystemdBackend.safe_systemd_value(raw):
            return ""
        return raw.strip()

    @classmethod
    def _resolve_host(cls, platform: str) -> str:
        """Map the unit's effective ``VOXD_BIND`` to a reachable health host.

        A wildcard bind (unspecified addresses, or unset/dropped) accepts
        loopback, so poll ``127.0.0.1``. A concrete bind is the only address
        voxd listens on -- poll it directly (loopback would false-fail). A
        non-IP value (e.g. a hostname) is polled as given.
        """
        bind = cls._effective_bind(platform)
        if not bind:
            return "127.0.0.1"
        try:
            if ipaddress.ip_address(bind).is_unspecified:  # 0.0.0.0, ::, ::0
                return "127.0.0.1"
        except ValueError:
            pass
        return bind

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    def client(self) -> VoxClientSync:
        """Return a client pinned to the daemon's host, port, and run-file token.

        The token is read fresh from ``serve.token`` and passed explicitly, so
        ``VoxClientSync`` cannot fall back to a stray shell ``VOXD_TOKEN``.
        voxd writes ``serve.token`` on startup, so an early probe may read
        ``None``; the poll's retry loop tolerates that brief race.
        """
        return VoxClientSync(
            host=self._host,
            port=self._port,
            token=read_token_file(),
        )


class ServiceInstaller:
    """Compose ProcessManager, KeysEnvWriter, and platform backends."""

    __slots__ = (
        "_keys_writer",
        "_launchd",
        "_process_mgr",
        "_systemd",
    )

    _keys_writer: KeysEnvWriter
    _launchd: LaunchdBackend
    _process_mgr: ProcessManager
    _systemd: SystemdBackend

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._process_mgr = ProcessManager()
        self._keys_writer = KeysEnvWriter()
        self._launchd = LaunchdBackend(self._process_mgr, _voxd_exec_args)
        self._systemd = SystemdBackend(self._process_mgr, _voxd_exec_args)
        return self

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_user_dirs() -> Path:
        """Create per-user state directories under ``~/.punt-labs/vox``."""
        state_root = _paths_user_state_dir()
        _paths_ensure_user_dirs(state_root)
        logger.info("Ensured directory tree under %s", state_root)
        return state_root

    # ------------------------------------------------------------------
    # Platform detection
    # ------------------------------------------------------------------

    @staticmethod
    def detect_platform() -> str:
        """Return ``'macos'`` or ``'linux'``.  Raise on unsupported platforms."""
        system = platform.system()
        if system == "Darwin":
            return "macos"
        if system == "Linux":
            return "linux"
        msg = (
            f"Unsupported platform: {system}. "
            "vox daemon install supports macOS and Linux."
        )
        raise SystemExit(msg)

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def _install_darwin(self) -> bool:
        """Run the macOS install path.  Return True if running.

        Writes the user LaunchAgent plist and bootstraps it.  No sudo:
        the agent lives under ``~/Library/LaunchAgents`` and runs in the
        session user's ``gui/<uid>`` domain.
        """
        self._launchd.stop()
        self._process_mgr.ensure_port_free()
        self._launchd.install()
        return self._launchd.status()

    def _install_linux(self, user: str) -> bool:
        """Run the Linux install path.  Return True if running."""
        self._systemd.cleanup_stale_user_unit()
        self._systemd.stop()
        self._process_mgr.ensure_port_free()
        self._systemd.install(user)
        return self._systemd.status()

    @staticmethod
    def _verify_serving(platform: str, service_path: Path) -> None:
        """Poll voxd's health endpoint until it answers or the deadline lapses.

        ``launchctl``/``systemctl`` registration proves only that the job is
        scheduled, not that voxd bound its port and stayed up.  Without this
        poll, ``install()`` reports "running" for a daemon that died on
        startup -- the silent-down failure mode.  Raise so ``vox daemon
        install`` exits non-zero when voxd never becomes reachable.  *platform*
        selects the backend whose ``VOXD_BIND`` gating the health host mirrors
        (see ``_HealthTarget._effective_bind``).
        """
        target = _HealthTarget(platform)
        deadline = time.monotonic() + _HEALTH_DEADLINE_S
        # None until the first probe fails: no exception has occurred yet.
        last_exc: VoxdConnectionError | VoxdProtocolError | OSError | None = None
        while time.monotonic() < deadline:
            try:
                target.client().health()
                return
            except (VoxdConnectionError, VoxdProtocolError, OSError) as exc:
                # VoxdProtocolError covers a receive timeout while voxd is
                # still binding its port -- transient during startup, so
                # retry until the deadline rather than failing on the first.
                last_exc = exc
                time.sleep(_HEALTH_POLL_INTERVAL_S)
        log_dir = _paths_log_dir()
        msg = (
            f"voxd registered but never became reachable within "
            f"{_HEALTH_DEADLINE_S:.0f}s on {target.host}:{target.port}. "
            f"Service: {service_path}. Check the daemon logs in {log_dir}."
        )
        raise RuntimeError(msg) from last_exc

    def install(self) -> str:
        """Install voxd as a system service.  Return a status message.

        Must be run as a normal user, not as root or under ``sudo``.
        """
        if os.geteuid() == 0:
            msg = (
                "vox daemon install must run as your normal user, not root. "
                "The service installs to your home directory and cannot "
                "function under root. Re-run without sudo:\n\n"
                "    vox daemon install\n"
            )
            raise SystemExit(msg)

        plat = self.detect_platform()
        user = getpass.getuser()
        args = _voxd_exec_args()

        state_root = self._ensure_user_dirs()

        keys_path = _paths_keys_env_file()
        self._keys_writer.write(dict(os.environ), keys_path)
        logger.info("Wrote provider keys to %s", keys_path)

        if plat == "macos":
            running = self._install_darwin()
            service_path = _LAUNCHD_PLIST
        else:
            logger.warning(_SUDO_NOTICE)
            running = self._install_linux(user)
            service_path = _SYSTEMD_UNIT

        # Registration alone does not prove voxd serves; verify it answers
        # health before reporting "running", so a silent-down daemon fails
        # the install loudly instead of masquerading as healthy.
        if running:
            self._verify_serving(plat, service_path)

        exec_display = " ".join(args)
        status = "running" if running else "installed (not yet running)"
        lines = [
            f"voxd daemon {status} on port {DEFAULT_PORT}.",
            f"  Service: {_LAUNCHD_PLIST if plat == 'macos' else _SYSTEMD_UNIT}",
            f"  Keys:    {keys_path}",
            f"  State:   {state_root}",
            f"  Command: {exec_display}",
            f"  User:    {user}",
        ]
        return os.linesep.join(lines)

    # ------------------------------------------------------------------
    # Uninstall
    # ------------------------------------------------------------------

    def uninstall(self) -> str:
        """Remove voxd system service.  Return a status message."""
        plat = self.detect_platform()
        if plat == "macos":
            self._launchd.uninstall()
            path = _LAUNCHD_PLIST
        else:
            self._systemd.uninstall()
            path = _SYSTEMD_UNIT
        return f"voxd daemon uninstalled. Removed {path}."

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Check if the daemon service is currently running."""
        plat = self.detect_platform()
        if plat == "macos":
            return self._launchd.status()
        return self._systemd.status()


# ---------------------------------------------------------------------------
# Module-level helper used by backends (avoids circular dependency)
# ---------------------------------------------------------------------------


def _voxd_exec_args() -> list[str]:
    """Return the command to invoke ``voxd``.

    Resolves ``voxd`` relative to ``sys.executable`` so the systemd unit
    always runs the binary from the same distribution that provided
    ``vox``.
    """
    voxd_path = Path(sys.executable).parent / "voxd"
    if not voxd_path.is_file():
        msg = (
            f"voxd binary not found at {voxd_path}. "
            "Reinstall punt-vox (uv tool install punt-vox or pip install punt-vox)."
        )
        raise SystemExit(msg)
    if not os.access(voxd_path, os.X_OK):
        msg = (
            f"voxd at {voxd_path} exists but is not executable. "
            "Reinstall punt-vox (uv tool install punt-vox or pip install punt-vox)."
        )
        raise SystemExit(msg)
    return [str(voxd_path), "--port", str(DEFAULT_PORT)]
