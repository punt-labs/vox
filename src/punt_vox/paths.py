"""Shared per-user path resolution for voxd and the vox CLI.

Both ``voxd.py`` and ``service.py`` need a consistent view of where the
daemon's per-user state lives. Previously each file defined its own
``_data_root()``/``_config_dir()``/``_log_dir()``/``_run_dir()`` helpers
that resolved to FHS system paths (``/etc/vox``, ``/var/log/vox``,
``/var/run/vox``) on Linux and Homebrew-prefix paths on macOS. That was a
regression: voxd runs as a single user (``User=`` in the systemd unit,
``UserName`` in the launchd plist), so its state is per-user, not
system-shared. The FHS paths stranded user API keys on upgrade, required
``sudo`` to edit personal tokens, and created a chown mismatch where the
file voxd was told to read was owned by root.

This module is the single source of truth for those paths. Keep it
lightweight — stdlib only — so both the heavy voxd import chain and the
minimal client can depend on it without pulling providers.
"""

from __future__ import annotations

import getpass
import os
import pwd
from pathlib import Path

_STATE_DIR_NAME = ".punt-labs"
_SUBDIR_NAME = "vox"


def user_state_dir() -> Path:
    """Return ``~/.punt-labs/vox`` for the current user.

    Same path on macOS and Linux. No FHS split, no Homebrew prefix.
    """
    return Path.home() / _STATE_DIR_NAME / _SUBDIR_NAME


def user_state_dir_for(user: str) -> Path:
    """Return ``~<user>/.punt-labs/vox`` for an arbitrary user.

    Used by ``vox daemon install`` when running under ``sudo``: the
    process's home dir is root's, but the target state dir must be the
    installing user's. Looks up the target user's home via ``pwd``.
    Falls back to ``Path.home() / .punt-labs / vox`` if the user is not
    in the password database, so callers never crash on a typo.
    """
    try:
        home = Path(pwd.getpwnam(user).pw_dir)
    except KeyError:
        return user_state_dir()
    return home / _STATE_DIR_NAME / _SUBDIR_NAME


def config_dir() -> Path:
    """Directory holding ``keys.env`` — same as the state dir root."""
    return user_state_dir()


def log_dir() -> Path:
    """Rotating log files live under ``<state>/logs``."""
    return user_state_dir() / "logs"


def run_dir() -> Path:
    """Runtime state (``serve.port``, ``serve.token``) under ``<state>/run``.

    This directory holds the auth token and is created with mode 0700
    so other local users cannot read it.
    """
    return user_state_dir() / "run"


def cache_dir() -> Path:
    """Synthesis cache lives under ``<state>/cache``."""
    return user_state_dir() / "cache"


def keys_env_file() -> Path:
    """Full path to ``keys.env`` inside the config dir."""
    return config_dir() / "keys.env"


def installing_user() -> str:
    """Get the real user, not root, when running under ``sudo``.

    ``SUDO_USER`` is the canonical source when sudo preserves it.
    Falls back to the current process's login name.
    """
    return os.environ.get("SUDO_USER") or getpass.getuser()


def ensure_user_dirs(state_root: Path) -> None:
    """Create ``state_root`` and its required subdirectories.

    Creates ``<state_root>``, ``<state_root>/logs``, ``<state_root>/run``,
    and ``<state_root>/cache``. The ``run`` dir is chmod 0700 because it
    holds the auth token. The other dirs inherit the process umask.

    Idempotent: safe to call repeatedly. Does not chown — callers under
    ``sudo`` must invoke this with ``seteuid`` already switched to the
    target user (or call it as the target user via ``os.fork``).
    """
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "logs").mkdir(parents=True, exist_ok=True)
    run = state_root / "run"
    run.mkdir(parents=True, exist_ok=True)
    # Enforce 0700 even on pre-existing dirs with looser permissions.
    run.chmod(0o700)
    (state_root / "cache").mkdir(parents=True, exist_ok=True)
