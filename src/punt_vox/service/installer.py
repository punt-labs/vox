"""Top-level service installer composing all backends."""

from __future__ import annotations

import getpass
import logging
import os
import platform
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Self, assert_never

from punt_vox.paths import (
    ensure_user_dirs as _paths_ensure_user_dirs,
    keys_env_file as _paths_keys_env_file,
    user_state_dir as _paths_user_state_dir,
)
from punt_vox.service.backend import (
    BackendCommandError,
    PortProbeError,
    PrivilegeDeniedError,
)
from punt_vox.service.health_verify import HealthVerifier, ServiceHealthError
from punt_vox.service.keys_env import KeysEnvWriter
from punt_vox.service.launchd import LaunchdBackend
from punt_vox.service.process import DEFAULT_PORT, ProcessManager
from punt_vox.service.systemd import PrivilegedRun, SystemdBackend

if TYPE_CHECKING:
    from punt_vox.service.backend import InstallableBackend
    from punt_vox.service.types import PlatformName

logger = logging.getLogger(__name__)

# A backend command, a refused privilege, a broken port probe, or a unit that
# never ran or served: each fails install and uninstall loudly with its cause
# (SystemExit carries it to the CLI's JSON and text), never as a traceback.
_SERVICE_ERRORS = (
    BackendCommandError,
    PrivilegeDeniedError,
    PortProbeError,
    ServiceHealthError,
)


class ServiceInstaller:
    """Compose ProcessManager, KeysEnvWriter, and the platform's backend."""

    __slots__ = ("_keys_writer", "_process_mgr")

    _keys_writer: KeysEnvWriter
    _process_mgr: ProcessManager

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._process_mgr = ProcessManager()
        self._keys_writer = KeysEnvWriter()
        return self

    @staticmethod
    def backend_for(plat: PlatformName) -> InstallableBackend:
        """Return the service backend for *plat* -- the one platform dispatch."""
        match plat:
            case "macos":
                return LaunchdBackend(ServiceInstaller._voxd_exec_args)
            case "linux":
                return SystemdBackend(
                    ServiceInstaller._voxd_exec_args, PrivilegedRun.on_this_host()
                )
            case _:
                assert_never(plat)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
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
    def detect_platform() -> PlatformName:
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

    def install(self) -> str:
        """Install voxd as a system service.  Return a status message.

        Must be run as a normal user, not as root or under ``sudo``.
        """
        self._refuse_root("install")
        try:
            return self._install(self.backend_for(self.detect_platform()))
        except _SERVICE_ERRORS as exc:
            raise SystemExit(f"vox daemon install failed: {exc}") from exc

    @staticmethod
    def _refuse_root(verb: str) -> None:
        """Raise ``SystemExit`` before *verb* touches anything, when run as root."""
        if os.geteuid() != 0:
            return
        msg = (
            f"vox daemon {verb} must run as your normal user, not root. "
            "The service lives in your home directory and cannot "
            "function under root. Re-run without sudo:\n\n"
            f"    vox daemon {verb}\n"
        )
        raise SystemExit(msg)

    def _install(self, backend: InstallableBackend) -> str:
        """Stop any previous voxd, install the unit, and verify it serves."""
        user = getpass.getuser()
        args = self._voxd_exec_args()
        state_root = self._ensure_user_dirs()
        keys = self._keys_writer.write(os.environ, _paths_keys_env_file())
        logger.info("Wrote provider keys to %s", keys.path)

        # Stop any previous voxd through the manager first, so launchd's
        # KeepAlive does not respawn it the instant the port cleanup kills it.
        if backend.is_installed():
            backend.request_stop()
        self._process_mgr.ensure_port_free()
        backend.install(user)
        # Registration alone does not prove voxd runs or serves: wait for the
        # unit to be active and for gated health to answer, or fail with why.
        HealthVerifier.for_backend(backend).verify()

        lines = [
            f"voxd daemon running on port {DEFAULT_PORT}.",
            f"  Service: {backend.service_path}",
            f"  Keys:    {keys.path}",
            *keys.notice_lines(),
            f"  State:   {state_root}",
            f"  Command: {' '.join(args)}",
            f"  User:    {user}",
        ]
        return self._noted(os.linesep.join(lines))

    # ------------------------------------------------------------------
    # Uninstall
    # ------------------------------------------------------------------

    def uninstall(self) -> str:
        """Remove voxd system service.  Return a status message.

        Raise ``SystemExit`` if the service files are removed but a voxd
        daemon survives the kill -- a voxd owned by another user cannot be
        signalled, so reporting success would strand a live daemon. Must be
        run as a normal user: as root its run-directory writes would follow
        whatever the user left there.
        """
        self._refuse_root("uninstall")
        try:
            return self._uninstall(self.backend_for(self.detect_platform()))
        except _SERVICE_ERRORS as exc:
            raise SystemExit(f"vox daemon uninstall failed: {exc}") from exc

    def _uninstall(self, backend: InstallableBackend) -> str:
        """Remove the unit, then kill and re-scan for a surviving voxd."""
        backend.uninstall()
        try:
            # kill_stale_daemon() returns False both for "nothing to kill" and
            # "kill failed"; re-scan the port to tell a survivor from an empty
            # port.
            survives = (
                not self._process_mgr.kill_stale_daemon()
                and self._stale_daemon_survives()
            )
        except PortProbeError as exc:
            # The unit is already gone: say so, never a bare "failed".
            msg = (
                f"vox daemon uninstall: voxd unit removed ({backend.service_path}); "
                f"daemon not stopped: {exc}"
            )
            raise SystemExit(msg) from exc
        if survives:
            msg = (
                f"voxd files removed ({backend.service_path}), but a daemon is "
                "still running and could not be stopped -- it may be owned by "
                "another user."
            )
            raise SystemExit(msg)
        return self._noted(f"voxd daemon uninstalled. Removed {backend.service_path}.")

    def _noted(self, message: str) -> str:
        """Return *message*, plus why the stale-daemon kill left serve.port."""
        note = self._process_mgr.port_file_note
        if not note:
            return message
        return f"{message}{os.linesep}  Note:    {note}"

    def _stale_daemon_survives(self) -> bool:
        """Return True if a voxd daemon still occupies the daemon port."""
        port = self._process_mgr.read_port_file() or DEFAULT_PORT
        pids = self._process_mgr.find_pid_on_port(port)
        return any(self._process_mgr.is_vox_daemon_process(p) for p in pids)
