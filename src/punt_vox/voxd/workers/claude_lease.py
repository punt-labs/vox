"""Coordinate with regular Claude writers using their heartbeat directory lease."""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from threading import Event, Thread
from typing import Self, final

__all__ = ["ClaudeConfigLease"]


@final
class ClaudeConfigLease:
    """Bound acquisition and recover abandoned native leases conservatively."""

    __slots__ = ("_path",)
    _path: Path

    def __new__(cls, config: Path) -> Self:
        """Use the native writer's sibling directory, not a flock on its inode."""
        self = super().__new__(cls)
        self._path = config.with_name(config.name + ".lock")
        return self

    def _acquire(self) -> os.stat_result:
        for _ in range(50):
            try:
                self._path.mkdir(mode=0o700)
                return self._path.stat()
            except FileExistsError:
                with suppress(FileNotFoundError):
                    info = self._path.stat(follow_symlinks=False)
                    if not stat.S_ISDIR(info.st_mode):
                        raise ValueError("Claude lease must be a directory") from None
                    if info.st_mtime < time.time() - 60:
                        self._path.rmdir()
                        continue
                time.sleep(0.1)
        raise TimeoutError("Claude configuration is locked by another writer")

    def _owns(self, identity: os.stat_result) -> bool:
        try:
            current = self._path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        return (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)

    def _refresh(self, stop: Event, identity: os.stat_result) -> None:
        while not stop.wait(2):
            if not self._owns(identity):
                return
            try:
                os.utime(self._path, follow_symlinks=False)
            except OSError:
                return

    @contextmanager
    def held(self) -> Generator[None]:
        """Refresh ownership until the complete read-modify-write has finished."""
        identity = self._acquire()
        stop = Event()
        heartbeat = Thread(target=self._refresh, args=(stop, identity), daemon=True)
        try:
            heartbeat.start()
            yield
        finally:
            stop.set()
            if heartbeat.ident is not None:
                heartbeat.join()
            if self._owns(identity):
                self._path.rmdir()
