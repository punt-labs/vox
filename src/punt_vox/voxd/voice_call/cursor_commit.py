"""Commit a private cursor and any newly linked directories to disk."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import final


@final
class CursorCommit:
    """Own the ordering of directory creation, data sync, and rename."""

    __slots__ = ()

    @classmethod
    def prepare(cls, directory: Path) -> None:
        """Create missing directories and sync each new entry in its parent."""
        try:
            directory.mkdir(mode=0o700)
        except FileNotFoundError:
            cls.prepare(directory.parent)
            cls.prepare(directory)
            return
        except FileExistsError:
            if not directory.is_dir():
                raise
            return
        cls._sync_directory(directory.parent)

    @classmethod
    def write(cls, path: Path, text: str) -> None:
        """Sync private file data before rename and the directory afterward."""
        directory = path.parent
        cls.prepare(directory)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete_on_close=False,
        ) as tmp:
            tmp.write(text)
            tmp.flush()
            os.fchmod(tmp.fileno(), 0o600)
            os.fsync(tmp.fileno())
            tmp.close()
            Path(tmp.name).replace(path)
        cls._sync_directory(directory)

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
