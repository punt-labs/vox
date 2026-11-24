"""Cross-platform per-repo and user-content directory resolution."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Per-repo subdirectory under .punt-labs/
_REPO_SUBDIR = Path(".punt-labs") / "vox"

DEFAULT_CONFIG_DIR = _REPO_SUBDIR  # Path(".punt-labs/vox")
_XDG_MUSIC_SETTING = re.compile(r'^XDG_MUSIC_DIR="([^"]*)"', re.MULTILINE)


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) to find a git repo root."""
    path = (start or Path.cwd()).resolve()
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            return parent
    return None


def find_config_dir(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) to find ``.punt-labs/vox/``.

    Returns the directory if either ``vox.md`` or ``vox.local.md``
    exists inside it.  No legacy fallback.
    """
    path = (start or Path.cwd()).resolve()
    for parent in (path, *path.parents):
        d = parent / _REPO_SUBDIR
        if (d / "vox.md").exists() or (d / "vox.local.md").exists():
            return d
    return None


def _read_xdg_music_dir(home: Path) -> Path | None:
    """Read XDG_MUSIC_DIR from the given user's XDG file.

    Return None if the file is missing, unreadable, or not UTF-8, or if the
    key isn't found.
    """
    dirs_file = home / ".config" / "user-dirs.dirs"
    if not dirs_file.is_file():
        return None
    try:
        text = dirs_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    match = _XDG_MUSIC_SETTING.search(text)
    if not match:
        return None

    raw = match.group(1)
    # Expand $HOME -- the only variable XDG user-dirs.dirs uses
    resolved = raw.replace("$HOME", str(home))
    return Path(resolved)


def _resolve_music_dir() -> Path:
    r"""Resolve the platform-standard music directory.

    Linux:   $XDG_MUSIC_DIR from ~/.config/user-dirs.dirs,
             fallback ~/Music
    macOS:   ~/Music
    Windows: %USERPROFILE%\Music
    """
    home = Path.home()
    if sys.platform == "linux":
        xdg = _read_xdg_music_dir(home)
        if xdg is not None:
            return xdg
    # Cross-platform fallback: ~/Music exists on macOS by default,
    # is the conventional location on Linux, and is the standard
    # media library on Windows.
    return home / "Music"


def default_output_dir() -> Path:
    """Resolve the default output directory for saved audio.

    Resolution order:
    1. VOX_OUTPUT_DIR env var (explicit override)
    2. ~/Music/vox/ (platform-standard)
    """
    env_dir = os.environ.get("VOX_OUTPUT_DIR")
    if env_dir:
        return Path(env_dir)
    return _resolve_music_dir() / "vox"
