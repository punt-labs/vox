"""Smooth and measure the dry spells in streamed PCM output.

A dry spell is the buffer running empty while audio was playing. The device
then plays zeros: a jump from the last sample to zero when the audio stops,
and a jump from zero to the first sample when it resumes, are both audible
clicks. :class:`GapSmoother` runs on the PortAudio callback thread and removes
both without touching audio that never met a gap: it carries the last sample
down to zero across :data:`FADE_FRAMES` after the cut, and blends the first
:data:`FADE_FRAMES` of resumed audio up from wherever that tail had got to.
Nothing is buffered, so no latency is added.

It also tells starvation from idleness. The call's output stream stays open
between the agent's turns, so a pause longer than :data:`IDLE_GAP_FRAMES` is a
turn boundary, not a network that fell behind real time.
"""

from __future__ import annotations

import struct
from typing import Literal, Self, final

from punt_vox.voxd.pcm_audio import BYTES_PER_FRAME, PCM_SAMPLE_RATE_HZ, PcmDuration

__all__ = ["FADE_FRAMES", "IDLE_GAP_FRAMES", "GapSmoother"]

FADE_FRAMES = PCM_SAMPLE_RATE_HZ * 3 // 1000
"""3 ms: long enough that the step between samples is a small fraction of the
signal, short enough to stay inside one pause between syllables."""

IDLE_GAP_FRAMES = PCM_SAMPLE_RATE_HZ
"""1 s: speech does not stall this long mid-sentence, a turn boundary does."""

type _Mode = Literal["playing", "cut", "resuming"]
"""``playing -> cut`` when the buffer runs dry, ``cut -> resuming`` when audio
returns, ``resuming -> playing`` once the fade-in is done."""

_SAMPLE = struct.Struct("<h")


@final
class GapSmoother:
    """Fade audio out of and back in across dry spells, and count them.

    Only the device thread calls :meth:`shape`; the loop reads the counts
    after the device is released, when no callback can still run.
    """

    __slots__ = (
        "_held",
        "_idle_gaps",
        "_last",
        "_longest",
        "_missing",
        "_mode",
        "_peak",
        "_pos",
        "_starved",
        "_total",
    )

    _held: int  # The tail's value when audio resumed: the fade-in starts there.
    _idle_gaps: int
    _last: int  # The last sample handed to the device.
    _longest: int  # Frames in the longest counted dry spell.
    _missing: int  # Frames the open dry spell has been silent so far.
    _mode: _Mode
    _peak: int  # The sample the tail started from.
    _pos: int  # Frames into the tail (cut) or the fade-in (resuming).
    _starved: int
    _total: int  # Frames silent across the counted dry spells.

    def __new__(cls) -> Self:
        """Create a smoother that has seen no audio."""
        self = super().__new__(cls)
        self._held = 0
        self._idle_gaps = 0
        self._last = 0
        self._longest = 0
        self._missing = 0
        self._mode = "playing"
        self._peak = 0
        self._pos = 0
        self._starved = 0
        self._total = 0
        return self

    @property
    def starved(self) -> int:
        """Return how many dry spells cut speech short of the idle limit."""
        return self._starved

    @property
    def idle_gaps(self) -> int:
        """Return how many pauses were longer than the idle limit."""
        return self._idle_gaps

    @property
    def total(self) -> PcmDuration:
        """Return the silence across the counted dry spells."""
        return PcmDuration(self._total * BYTES_PER_FRAME)

    @property
    def longest(self) -> PcmDuration:
        """Return the longest counted dry spell."""
        return PcmDuration(self._longest * BYTES_PER_FRAME)

    def settle(self) -> None:
        """Count the dry spell still open when the stream ends."""
        if self._mode == "cut":
            self._end_spell()
            self._mode = "playing"

    def shape(self, out: memoryview, filled: int, *, starving: bool) -> None:
        """Smooth one block whose first *filled* bytes are audio, the rest zeros.

        *starving* is whether the block ran short because the response is
        still arriving and audio has already played: a dry spell.
        """
        frames = filled // BYTES_PER_FRAME
        if frames:
            if self._mode == "cut":
                self._resume()
            if self._mode == "resuming":
                self._fade_in(out, frames)
            (self._last,) = _SAMPLE.unpack_from(out, (frames - 1) * BYTES_PER_FRAME)
        if starving:
            if self._mode != "cut":
                self._cut()
            self._missing += len(out) // BYTES_PER_FRAME - frames
            self._fade_out(out, frames)
        elif self._mode == "cut":
            # The response finished or was flushed: the pause is over.
            self._end_spell()
            self._mode = "playing"

    def _cut(self) -> None:
        self._mode, self._peak, self._pos, self._missing = "cut", self._last, 0, 0

    def _resume(self) -> None:
        self._end_spell()
        self._mode, self._held, self._pos = "resuming", self._last, 0

    def _end_spell(self) -> None:
        if self._missing <= IDLE_GAP_FRAMES:
            self._starved += 1
            self._total += self._missing
            self._longest = max(self._longest, self._missing)
        else:
            self._idle_gaps += 1
        self._missing = 0

    def _fade_out(self, out: memoryview, start: int) -> None:
        """Carry the last sample down to zero across the silent frames."""
        frames = len(out) // BYTES_PER_FRAME
        for at in range(start, min(frames, start + FADE_FRAMES - self._pos)):
            self._pos += 1
            self._last = self._peak * (FADE_FRAMES - self._pos) // FADE_FRAMES
            _SAMPLE.pack_into(out, at * BYTES_PER_FRAME, self._last)

    def _fade_in(self, out: memoryview, frames: int) -> None:
        """Blend resumed audio up from the tail's value to itself."""
        for at in range(min(frames, FADE_FRAMES - self._pos)):
            (heard,) = _SAMPLE.unpack_from(out, at * BYTES_PER_FRAME)
            weight = self._pos + at
            blended = (
                heard * weight + self._held * (FADE_FRAMES - weight)
            ) // FADE_FRAMES
            _SAMPLE.pack_into(out, at * BYTES_PER_FRAME, blended)
        self._pos += min(frames, FADE_FRAMES - self._pos)
        if self._pos >= FADE_FRAMES:
            self._mode = "playing"
