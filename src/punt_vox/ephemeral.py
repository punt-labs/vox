"""Ephemeral output directory management.

Provides a `.vox/` directory in the project root for temporary audio
files. Files are cleaned up automatically before each synthesis to
prevent accumulation.

The project root is derived from the config path (which respects the
daemon's per-session ContextVar override) rather than ``Path.cwd()``,
so this works correctly when the daemon's working directory is ``/``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from punt_vox.config import resolve_config_path

logger = logging.getLogger(__name__)

_EPHEMERAL_DIR_NAME = ".vox"


def project_root() -> Path:
    """Derive project root from config path, falling back to cwd.

    ``resolve_config_path()`` returns ``<root>/.vox/config.md``.
    In daemon mode the ContextVar override ensures this points at the
    correct project even though ``Path.cwd()`` is ``/``.
    """
    config = resolve_config_path()
    # config is <root>/.vox/config.md — parent.parent is the project root
    root = config.parent.parent
    # Sanity check: if root is / or empty, fall back to cwd
    if root == Path("/") or not root.parts:
        return Path.cwd()
    return root


def ephemeral_output_dir() -> Path:
    """Return the ephemeral output directory (``.vox/`` in project root).

    Creates the directory if it does not exist.
    """
    path = project_root() / _EPHEMERAL_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_ephemeral(*, keep: Path | None = None) -> int:
    """Delete all files in the ephemeral directory except ``keep``.

    Args:
        keep: Optional path to preserve (e.g. the file currently being played).

    Returns:
        Number of files deleted.
    """
    eph_dir = project_root() / _EPHEMERAL_DIR_NAME
    if not eph_dir.is_dir():
        return 0

    deleted = 0
    for child in eph_dir.iterdir():
        if child.is_file() and child != keep and child.suffix == ".mp3":
            child.unlink()
            deleted += 1

    if deleted:
        logger.debug("Cleaned %d ephemeral file(s) from %s", deleted, eph_dir)
    return deleted
