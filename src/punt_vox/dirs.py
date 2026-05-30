"""Cross-platform per-repo and user-content directory resolution."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Per-repo subdirectory under .punt-labs/
_REPO_SUBDIR = Path(".punt-labs") / "vox"

DEFAULT_CONFIG_DIR = _REPO_SUBDIR  # Path(".punt-labs/vox")


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


def _parse_xdg_user_dir(key: str) -> Path | None:
    """Parse a single key from ~/.config/user-dirs.dirs.

    Returns None if the file doesn't exist, the key isn't found,
    or the value cannot be resolved.
    """
    dirs_file = Path.home() / ".config" / "user-dirs.dirs"
    if not dirs_file.is_file():
        return None
    try:
        text = dirs_file.read_text(encoding="utf-8")
    except OSError:
        return None

    # Expected line format: XDG_MUSIC_DIR="$HOME/Music"
    pattern = re.compile(
        rf'^{re.escape(key)}="([^"]*)"',
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return None

    raw = match.group(1)
    # Expand $HOME -- the only variable XDG user-dirs.dirs uses
    resolved = raw.replace("$HOME", str(Path.home()))
    return Path(resolved)


def _resolve_music_dir() -> Path:
    """Resolve the platform-standard music directory.

    Linux:   $XDG_MUSIC_DIR from ~/.config/user-dirs.dirs,
             fallback ~/Music
    macOS:   ~/Music
    Windows: %USERPROFILE%\\Music
    """
    if sys.platform == "linux":
        xdg = _parse_xdg_user_dir("XDG_MUSIC_DIR")
        if xdg is not None:
            return xdg
    # Cross-platform fallback: ~/Music exists on macOS by default,
    # is the conventional location on Linux, and is the standard
    # media library on Windows.
    return Path.home() / "Music"


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


def music_output_dir() -> Path:
    """Return the directory for generated music tracks.

    Layout: ~/Music/vox/tracks/ (replaces ~/vox-output/music/)
    """
    return default_output_dir() / "tracks"
