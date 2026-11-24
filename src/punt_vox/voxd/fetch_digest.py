"""Measure a store file off-loop and retain the opened file for the sender."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import logging
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING, Self, final

from punt_vox.types_audio import FETCH_CHUNK_BYTES

if TYPE_CHECKING:
    from pathlib import Path
    from typing import BinaryIO

__all__ = ["FetchDigest", "MeasuredFile"]

logger = logging.getLogger(__name__)

_MAX_CONCURRENT_MEASUREMENTS = 2


@dataclass(frozen=True, slots=True)
class MeasuredFile:
    """An open, rewound file with the metadata announced to the client."""

    handle: BinaryIO
    total: int
    chunks: int
    sha256: str


@final
class FetchDigest:
    """Bound a daemon's digest work so downloads leave workers for other RPCs."""

    __slots__ = ("_chunk_bytes", "_slots")

    _chunk_bytes: int
    _slots: asyncio.Semaphore

    def __new__(cls, chunk_bytes: int = FETCH_CHUNK_BYTES) -> Self:
        """Set the read size for both passes of associated transfers.

        Raises:
            ValueError: If chunk_bytes is not positive.
        """
        if chunk_bytes <= 0:
            msg = f"chunk_bytes must be positive, got {chunk_bytes}"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._chunk_bytes = chunk_bytes
        self._slots = asyncio.Semaphore(_MAX_CONCURRENT_MEASUREMENTS)
        return self

    @property
    def chunk_bytes(self) -> int:
        """Return the maximum raw bytes per read and per data frame."""
        return self._chunk_bytes

    async def measure(self, path: Path) -> MeasuredFile:
        """Measure in a worker and transfer ownership of the opened file."""
        await self._slots.acquire()
        stopped = Event()
        try:
            work = asyncio.get_running_loop().run_in_executor(
                None, self._read, path, stopped
            )
        except BaseException:
            self._slots.release()
            raise
        work.add_done_callback(self._release_slot)
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            stopped.set()
            work.add_done_callback(self._discard)
            raise

    def _release_slot(self, _: asyncio.Future[MeasuredFile]) -> None:
        self._slots.release()

    def _read(self, path: Path, stopped: Event) -> MeasuredFile:
        if stopped.is_set():
            raise asyncio.CancelledError
        hasher = hashlib.sha256()
        total = 0
        with ExitStack() as cleanup:
            handle = cleanup.enter_context(self._open(path))
            while not stopped.is_set():
                data = handle.read(self._chunk_bytes)
                if not data:
                    handle.seek(0)
                    chunks = -(-total // self._chunk_bytes)
                    measured = MeasuredFile(handle, total, chunks, hasher.hexdigest())
                    cleanup.pop_all()
                    return measured
                hasher.update(data)
                total += len(data)
            raise asyncio.CancelledError

    @staticmethod
    def _open(path: Path) -> BinaryIO:
        # The path was checked before awaiting a worker; verify the opened entry
        # without following a newly installed link or blocking on a FIFO.
        def opener(name: str, flags: int) -> int:
            fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError(errno.EINVAL, "not a regular file", name)
            except BaseException:
                os.close(fd)
                raise
            return fd

        return open(path, "rb", opener=opener)

    @staticmethod
    def _discard(work: asyncio.Future[MeasuredFile]) -> None:
        if work.cancelled():
            return
        try:
            work.result().handle.close()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Abandoned fetch measurement failed")
