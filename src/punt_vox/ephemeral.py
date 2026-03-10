"""Ephemeral output directory management.

Provides a `.vox/` directory in the current working directory for
temporary audio files. Files are cleaned up automatically before
each synthesis to prevent accumulation.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_EPHEMERAL_DIR_NAME = ".vox"


def ephemeral_output_dir() -> Path:
    """Return the ephemeral output directory (`.vox/` in cwd).

    Creates the directory if it does not exist.
    """
    path = Path.cwd() / _EPHEMERAL_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_ephemeral(*, keep: Path | None = None) -> int:
    """Delete all files in the ephemeral directory except ``keep``.

    Args:
        keep: Optional path to preserve (e.g. the file currently being played).

    Returns:
        Number of files deleted.
    """
    eph_dir = Path.cwd() / _EPHEMERAL_DIR_NAME
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
