"""The level of a microphone capture's first second, logged once."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Final, Self, final

if TYPE_CHECKING:
    from array import array

__all__ = ["CaptureLevel"]

logger = logging.getLogger(__name__)

_WINDOW_S: Final = 1.0  # Report the capture level once, over this much audio.


@final
class CaptureLevel:
    """Fold the first second of samples into a peak and an RMS, then log them."""

    __slots__ = ("_peak", "_samples", "_squares")
    _peak: int
    _squares: int
    _samples: int

    def __new__(cls) -> Self:
        """Start a meter that has measured nothing yet."""
        self = super().__new__(cls)
        self._peak, self._squares, self._samples = 0, 0, 0
        return self

    def measure(
        self, samples: array[int], window_start_s: float, seen_s: float
    ) -> None:
        """Fold a block starting at ``window_start_s``; log once the second is in."""
        if window_start_s >= _WINDOW_S:
            return
        self._samples += len(samples)
        self._peak = max(self._peak, max(map(abs, samples), default=0))
        self._squares += sum(sample * sample for sample in samples)
        if seen_s >= _WINDOW_S:
            rms = math.isqrt(self._squares // self._samples) if self._samples else 0
            logger.info(
                "Call capture level over the first second: peak %d, rms %d",
                self._peak,
                rms,
            )
