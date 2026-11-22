"""ElevenLabs ``pcm_16000`` audio: its format, durations, and the shared buffer.

:class:`PcmBuffer` is the one structure the event loop and PortAudio's
callback thread both touch; its single lock makes each append, block pull
and clear atomic.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Self, final

__all__ = ["BYTES_PER_FRAME", "PCM_SAMPLE_RATE_HZ", "PcmBuffer", "PcmDuration"]

PCM_SAMPLE_RATE_HZ = 16_000
"""ElevenLabs ``pcm_16000``: 16-bit signed little-endian mono at 16 kHz."""

BYTES_PER_FRAME = 2


@final
@dataclass(frozen=True, slots=True)
class PcmDuration:
    """A byte count of ``pcm_16000`` audio and the playing time it spans."""

    nbytes: int

    @property
    def ms(self) -> float:
        """Return how long this many bytes play for, in milliseconds."""
        return self.nbytes / BYTES_PER_FRAME / PCM_SAMPLE_RATE_HZ * 1000


@final
class PcmBuffer:
    """Thread-safe FIFO of PCM bytes shared by the loop and the device thread."""

    __slots__ = ("_lock", "_pending")

    _lock: threading.Lock
    _pending: bytearray

    def __new__(cls) -> Self:
        """Create an empty buffer."""
        self = super().__new__(cls)
        self._lock = threading.Lock()
        self._pending = bytearray()
        return self

    def append(self, pcm: bytes) -> None:
        """Queue *pcm* behind everything already pending."""
        with self._lock:
            self._pending += pcm

    def pull(self, nbytes: int) -> bytes:
        """Remove and return up to *nbytes* whole frames from the front."""
        with self._lock:
            take = min(nbytes, len(self._pending))
            take -= take % BYTES_PER_FRAME
            block = bytes(self._pending[:take])
            del self._pending[:take]
            return block

    def clear(self) -> int:
        """Discard everything pending and return how many bytes were dropped."""
        with self._lock:
            dropped = len(self._pending)
            self._pending.clear()
            return dropped

    def trim_partial_frame(self) -> int:
        """Drop a trailing half sample and return how many bytes went."""
        with self._lock:
            extra = len(self._pending) % BYTES_PER_FRAME
            if extra:
                del self._pending[-extra:]
            return extra

    def __len__(self) -> int:
        """Return the number of bytes pending."""
        with self._lock:
            return len(self._pending)
