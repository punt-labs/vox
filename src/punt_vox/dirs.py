"""Cross-platform per-repo and user-content directory resolution."""

from __future__ import annotations

import os
import re
import sys
from itertools import product
from pathlib import Path

from punt_vox.config_target import (
    CONFIG_PATHS,
    DEFAULT_CONFIG_DIR as DEFAULT_CONFIG_DIR,
    RepoConfigTarget,
)

_XDG_MUSIC_SETTING = re.compile(r'^XDG_MUSIC_DIR="([^"]*)"', re.MULTILINE)


def _ancestors(start: Path | None) -> tuple[Path, ...]:
    """*start* (default: cwd), resolved, followed by each of its parents."""
    path = (start or Path.cwd()).resolve()
    return (path, *path.parents)


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) to find a git repo root."""
    return next((p for p in _ancestors(start) if (p / ".git").exists()), None)


def find_config_dir(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) to find ``.punt-labs/vox/``.

    Returns the directory if either ``vox.md`` or ``vox.local.md``
    exists inside it.  No legacy fallback.  A repo with no config of its
    own inherits the nearest one above it (e.g. its workspace's).
    """
    paths = (base / name for base, name in product(_ancestors(start), CONFIG_PATHS))
    return next((path.parent for path in paths if path.exists()), None)


def writable_config_dir(start: Path | None = None) -> Path | None:
    """Return the config directory a setting write from *start* lands in.

    Inside a git repo, reuse an existing config path as-is, including a
    symlinked config. If there isn't one, write to the repo root's
    ``.punt-labs/vox/``, seeding it from any config inherited from above the
    repo. Outside a git repo, use :func:`find_config_dir` as-is.

    Raises:
        PermissionError: A new repository config resolves outside the checkout.
    """
    found = find_config_dir(start)
    root = find_repo_root(start)
    if root is None or (found is not None and found.is_relative_to(root)):
        return found
    return RepoConfigTarget(root).prepare(found)


def _read_xdg_music_dir() -> Path | None:
    """Read XDG_MUSIC_DIR from the current user's XDG file.

    Return None if the file is missing, unreadable, or not UTF-8, or if the
    key isn't found.
    """
    home = Path.home()
    try:
        text = (home / ".config" / "user-dirs.dirs").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _XDG_MUSIC_SETTING.search(text)
    # Expand $HOME -- the only variable XDG user-dirs.dirs uses
    return Path(match.group(1).replace("$HOME", str(home))) if match else None


def _resolve_music_dir() -> Path:
    r"""Resolve the platform-standard music directory.

    Linux:   $XDG_MUSIC_DIR from ~/.config/user-dirs.dirs,
             fallback ~/Music
    macOS:   ~/Music
    Windows: %USERPROFILE%\Music
    """
    xdg = _read_xdg_music_dir() if sys.platform == "linux" else None
    # Cross-platform fallback: ~/Music exists on macOS by default,
    # is the conventional location on Linux, and is the standard
    # media library on Windows.
    return xdg or Path.home() / "Music"


def default_output_dir() -> Path:
    """Resolve the default output directory for saved audio.

    Resolution order:
    1. VOX_OUTPUT_DIR env var (explicit override)
    2. ~/Music/vox/ (platform-standard)
    """
    env_dir = os.environ.get("VOX_OUTPUT_DIR")
    return Path(env_dir) if env_dir else _resolve_music_dir() / "vox"
