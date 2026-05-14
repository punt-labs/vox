"""Daemon lifecycle management for ``voxd``."""
# pyright: reportPrivateUsage=false
# Re-exporting private names from submodules is the whole point of __init__.py.

from __future__ import annotations

from pathlib import Path

from punt_vox.service.installer import (
    ServiceInstaller,
    _voxd_exec_args,
)
from punt_vox.service.keys_env import KeysEnvWriter
from punt_vox.service.launchd import (
    _LAUNCHD_PLIST,
    LaunchdBackend,
)
from punt_vox.service.process import (
    DEFAULT_PORT,
    ProcessManager,
)
from punt_vox.service.systemd import (
    _LEGACY_USER_UNIT_RELATIVE,
    _SYSTEMD_DIR,
    _SYSTEMD_UNIT,
    SystemdBackend,
)

# ---------------------------------------------------------------------------
# Module-level singletons and free-function API that callers expect.
# ---------------------------------------------------------------------------

_installer = ServiceInstaller()
_process_mgr = _installer._process_mgr
_launchd = _installer._launchd
_systemd = _installer._systemd
_keys_writer = _installer._keys_writer


# -- Free-function API (preserves the public interface) --------------------


def install() -> str:
    """Install voxd as a system service.  Return a status message."""
    return _installer.install()


def uninstall() -> str:
    """Remove voxd system service.  Return a status message."""
    return _installer.uninstall()


def is_running() -> bool:
    """Check if the daemon service is currently running."""
    return _installer.is_running()


def detect_platform() -> str:
    """Return ``'macos'`` or ``'linux'``.  Raise on unsupported platforms."""
    return ServiceInstaller.detect_platform()


def read_port_file() -> int | None:
    """Read the daemon port from the port file."""
    return _process_mgr.read_port_file()


# -- Private free-function shims for callers that import private names -----


def _find_pid_on_port(port: int) -> list[int]:
    return _process_mgr.find_pid_on_port(port)


def _is_vox_daemon_process(pid: int) -> bool:
    return _process_mgr.is_vox_daemon_process(pid)


def _kill_pid(pid: int) -> bool:
    return _process_mgr.kill_pid(pid)


def _kill_stale_daemon() -> bool:
    return _process_mgr.kill_stale_daemon()


def _ensure_port_free() -> None:
    _process_mgr.ensure_port_free()


def _remove_port_file() -> None:
    _process_mgr.remove_port_file()


def _write_keys_env(env: dict[str, str], keys_path: Path) -> Path:
    return _keys_writer.write(env, keys_path)


def _ensure_user_dirs() -> Path:
    return _installer._ensure_user_dirs()


def _launchd_plist_content(user: str) -> str:
    return _launchd.plist_content(user)


def _launchd_stop() -> None:
    _launchd.stop()


def _launchd_install(user: str) -> None:
    _launchd.install(user)


def _launchd_uninstall() -> None:
    _launchd.uninstall()


def _launchd_status() -> bool:
    return _launchd.status()


def _systemd_unit_content(user: str) -> str:
    return _systemd.unit_content(user)


def _systemd_stop() -> None:
    _systemd.stop()


def _systemd_install(user: str) -> None:
    _systemd.install(user)


def _systemd_uninstall() -> None:
    _systemd.uninstall()


def _systemd_status() -> bool:
    return _systemd.status()


def _safe_systemd_value(value: str) -> bool:
    return SystemdBackend.safe_systemd_value(value)


def _systemd_audio_env_lines(user: str) -> list[str]:
    return _systemd.audio_env_lines(user)


def _legacy_user_unit_path() -> Path:
    return _systemd.legacy_user_unit_path()


def _cleanup_stale_user_unit() -> bool:
    return _systemd.cleanup_stale_user_unit()


def _run_dir() -> Path:
    return _process_mgr._run_dir()


__all__ = [
    "DEFAULT_PORT",
    "_LAUNCHD_PLIST",
    "_LEGACY_USER_UNIT_RELATIVE",
    "_SYSTEMD_DIR",
    "_SYSTEMD_UNIT",
    "KeysEnvWriter",
    "LaunchdBackend",
    "ProcessManager",
    "ServiceInstaller",
    "SystemdBackend",
    "_cleanup_stale_user_unit",
    "_ensure_port_free",
    "_ensure_user_dirs",
    "_find_pid_on_port",
    "_is_vox_daemon_process",
    "_kill_pid",
    "_kill_stale_daemon",
    "_launchd_install",
    "_launchd_plist_content",
    "_launchd_status",
    "_launchd_stop",
    "_launchd_uninstall",
    "_legacy_user_unit_path",
    "_remove_port_file",
    "_run_dir",
    "_safe_systemd_value",
    "_systemd_audio_env_lines",
    "_systemd_install",
    "_systemd_status",
    "_systemd_stop",
    "_systemd_uninstall",
    "_systemd_unit_content",
    "_voxd_exec_args",
    "_write_keys_env",
    "detect_platform",
    "install",
    "is_running",
    "read_port_file",
    "uninstall",
]
