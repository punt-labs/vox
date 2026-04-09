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

import importlib.metadata
from pathlib import Path

_STATE_DIR_NAME = ".punt-labs"
_SUBDIR_NAME = "vox"


def user_state_dir() -> Path:
    """Return ``~/.punt-labs/vox`` for the current user.

    Same path on macOS and Linux. No FHS split, no Homebrew prefix.
    """
    return Path.home() / _STATE_DIR_NAME / _SUBDIR_NAME


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


def ensure_user_dirs(state_root: Path | None = None) -> None:
    """Create the per-user state dir and its required subdirectories.

    Creates ``<state_root>``, ``<state_root>/logs``, ``<state_root>/run``,
    and ``<state_root>/cache``. When *state_root* is ``None``, resolves
    to the current user's state dir via ``user_state_dir()``.

    All four dirs are chmod 0700 because every one of them holds
    private per-user state: provider API keys, spoken-text logs, auth
    token, cached synthesis output. The chmod is applied on every call,
    not just at creation time, so pre-existing directories with looser
    permissions (for example 0755 from an older version that respected
    process umask) are tightened on the next startup.

    Idempotent: safe to call repeatedly. Does not chown — callers are
    expected to invoke this as the target user.
    """
    if state_root is None:
        state_root = user_state_dir()
    state_root.mkdir(parents=True, exist_ok=True)
    state_root.chmod(0o700)
    for subdir in ("logs", "run", "cache"):
        d = state_root / subdir
        d.mkdir(parents=True, exist_ok=True)
        # Enforce 0700 even on pre-existing dirs with looser permissions.
        d.chmod(0o700)


def installed_version() -> str:
    """Return the installed ``punt-vox`` package version.

    Reads ``importlib.metadata.version("punt-vox")`` and falls back
    to ``punt_vox.__version__`` when the package metadata is not
    available (e.g., running from an uninstalled source tree during
    development). Used by both the ``vox doctor`` daemon-staleness
    check and by voxd at startup when populating the health response.
    Centralizing the fallback here guarantees doctor and voxd resolve
    the same value when both fall back, so the comparison in
    ``vox daemon restart`` is apples-to-apples.
    """
    try:
        return importlib.metadata.version("punt-vox")
    except importlib.metadata.PackageNotFoundError:
        # Inline import — hoisting would create a cycle because
        # punt_vox.__init__ is at the top of the module graph and
        # several modules in this package import paths.py during
        # their own import (voxd.py, service.py, __main__.py).
        from punt_vox import __version__

        return __version__
