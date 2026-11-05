"""Which file a path names, told apart from any later file on the same inode.

voxd and the CLI both decide whether ``serve.port`` is still the file they
saw. Device and inode alone are not enough: once a file is unlinked its inode
is free, and a respawned voxd's new file can be handed the same number. The
modification and status-change times, in nanoseconds, are part of the
identity, so a reused inode -- or any rewrite of the file -- reads as another
file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    import os
    from pathlib import Path

__all__ = ["FileIdentity"]


@final
@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Device, inode, and the mtime and ctime that tell an inode's files apart."""

    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def of(cls, st: os.stat_result) -> Self:
        """Return the identity a ``stat`` or ``fstat`` result describes."""
        return cls(st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns)

    @classmethod
    def at(cls, path: Path) -> Self | None:
        """Return the identity of the file at *path*.

        ``None`` is the documented answer when no file is there; any other
        ``stat`` failure raises ``OSError``.
        """
        try:
            return cls.of(path.stat())
        except FileNotFoundError:
            return None
