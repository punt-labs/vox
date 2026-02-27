"""Ephemeral output directory management.

Provides a `.tts/` directory in the current working directory for
temporary audio files. Files are cleaned up automatically before
each synthesis to prevent accumulation.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_EPHEMERAL_DIR_NAME = ".tts"


def ephemeral_output_dir() -> Path:
    """Return the ephemeral output directory (`.tts/` in cwd).

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


def remove_ephemeral_dir() -> bool:
    """Remove the entire ephemeral directory.

    Returns:
        True if the directory existed and was removed, False otherwise.
    """
    eph_dir = Path.cwd() / _EPHEMERAL_DIR_NAME
    if eph_dir.is_dir():
        shutil.rmtree(eph_dir)
        logger.debug("Removed ephemeral directory %s", eph_dir)
        return True
    return False
