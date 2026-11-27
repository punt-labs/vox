"""Smooth and measure the dry spells in streamed PCM output.

A dry spell is the buffer running empty while audio was playing. The device
then plays zeros: a jump from the last sample to zero when the audio stops,
and a jump from zero to the first sample when it resumes, are both audible
clicks. :class:`GapSmoother` runs on the PortAudio callback thread and removes
both without touching audio that never met a gap: it carries the last sample
down to zero across :data:`FADE_FRAMES` after any cut (a dry spell, a flush,
the end of the response), and blends the first :data:`FADE_FRAMES` of resumed
audio up from wherever that tail had got to. Nothing is buffered, so no
latency is added.

It also tells starvation from a turn boundary. The call's output stream stays
open between the agent's turns, so a silence is starvation only when the
*same* response resumes after it. The sink marks where each response begins;
a spell that ends at such a mark, or with the stream, was nobody's lateness.
"""

from __future__ import annotations

import struct
from collections import deque
from typing import Literal, Self, final

from punt_vox.voxd.pcm_audio import BYTES_PER_FRAME, PCM_SAMPLE_RATE_HZ, PcmDuration

__all__ = ["FADE_FRAMES", "GapSmoother"]

FADE_FRAMES = PCM_SAMPLE_RATE_HZ * 3 // 1000
"""3 ms: long enough that the step between samples is a small fraction of the
signal, short enough to stay inside one pause between syllables."""

type _Mode = Literal["playing", "cut", "tail", "resuming"]
"""``playing -> cut`` when the buffer runs dry mid-response, ``playing | cut ->
tail`` when a block is short for any other reason, ``cut | tail -> resuming``
when audio returns, ``resuming -> playing`` once the fade-in is done. Only
``cut`` is an open spell; ``tail`` just finishes the fade-out."""

_SAMPLE = struct.Struct("<h")


@final
class GapSmoother:
    """Fade audio out of and back in across silences, and count the spells.

    Only the device thread calls :meth:`shape`; the loop calls
    :meth:`begin_response` and reads the counts after the device is released,
    when no callback can still run.
    """

    __slots__ = (
        "_held",
        "_idle_gaps",
        "_last",
        "_longest",
        "_marks",
        "_missing",
        "_mode",
        "_peak",
        "_pos",
        "_response",
        "_starved",
        "_total",
    )

    _held: int  # The tail's value when audio resumed: the fade-in starts there.
    _idle_gaps: int
    _last: int  # The last sample handed to the device.
    _longest: int  # Frames in the longest starvation.
    _marks: deque[int]  # Stream offsets where a new response's audio begins.
    _missing: int  # Frames the open dry spell has been silent so far.
    _mode: _Mode
    _peak: int  # The sample the tail started from.
    _pos: int  # Frames into the tail (cut, tail) or the fade-in (resuming).
    _response: int | None  # The response the last chunk belonged to.
    _starved: int
    _total: int  # Frames silent across the starvations.

    def __new__(cls) -> Self:
        """Create a smoother that has seen no audio."""
        self = super().__new__(cls)
        self._held = 0
        self._idle_gaps = 0
        self._last = 0
        self._longest = 0
        self._marks = deque()
        self._missing = 0
        self._mode = "playing"
        self._peak = 0
        self._pos = 0
        self._response = None
        self._starved = 0
        self._total = 0
        return self

    @property
    def starved(self) -> int:
        """Return how many times the same response resumed after a silence."""
        return self._starved

    @property
    def idle_gaps(self) -> int:
        """Return how many silences ended at a new response or with the stream."""
        return self._idle_gaps

    @property
    def total(self) -> PcmDuration:
        """Return the silence across the starvations."""
        return PcmDuration(self._total * BYTES_PER_FRAME)

    @property
    def longest(self) -> PcmDuration:
        """Return the longest starvation."""
        return PcmDuration(self._longest * BYTES_PER_FRAME)

    def note_response(self, offset: int, response: int | None) -> None:
        """Note that the chunk at stream byte *offset* belongs to *response*.

        A response that differs from the previous chunk's begins at *offset*.
        ``None`` means the caller does not tell responses apart.
        """
        if response is not None and response != self._response:
            self._response = response
            self._marks.append(offset)

    def settle(self) -> None:
        """Count the dry spell still open when the stream ends, as idle."""
        if self._mode == "cut":
            self._end_spell(is_idle=True)
        self._mode = "playing"

    def shape(
        self, out: memoryview, filled: int, *, starving: bool, played: int
    ) -> None:
        """Smooth one block whose first *filled* bytes are audio, the rest zeros.

        *starving* is whether the block ran short because the response is
        still arriving and audio has already played: a dry spell. *played* is
        the stream offset of the block's first byte.
        """
        frames = filled // BYTES_PER_FRAME
        if frames:
            is_new_response = self._reached(played)
            if self._mode in {"cut", "tail"}:
                self._resume(is_idle=is_new_response)
            if self._mode == "resuming":
                self._fade_in(out, frames)
            (self._last,) = _SAMPLE.unpack_from(out, (frames - 1) * BYTES_PER_FRAME)
        if starving:
            if self._mode != "cut":
                self._begin_tail("cut")
            self._missing += len(out) // BYTES_PER_FRAME - frames
        elif filled < len(out):
            self._wind_down()
        if self._mode in {"cut", "tail"}:
            self._fade_out(out, frames)

    def _reached(self, played: int) -> bool:
        """Return whether a response mark lies at or before stream byte *played*."""
        reached = False
        while self._marks and self._marks[0] <= played:
            self._marks.popleft()
            reached = True
        return reached

    def _begin_tail(self, mode: _Mode) -> None:
        self._mode, self._peak, self._pos, self._missing = mode, self._last, 0, 0

    def _wind_down(self) -> None:
        """A short block that is no dry spell: the response ended or was cut."""
        if self._mode == "cut":
            self._end_spell(is_idle=True)
            self._mode = "tail"
        elif self._mode != "tail" and self._last:
            self._begin_tail("tail")

    def _resume(self, *, is_idle: bool) -> None:
        if self._mode == "cut":
            self._end_spell(is_idle=is_idle)
        self._mode, self._held, self._pos = "resuming", self._last, 0

    def _end_spell(self, *, is_idle: bool) -> None:
        if is_idle:
            self._idle_gaps += 1
        else:
            self._starved += 1
            self._total += self._missing
            self._longest = max(self._longest, self._missing)
        self._missing = 0

    def _fade_out(self, out: memoryview, start: int) -> None:
        """Carry the last sample down to zero across the silent frames."""
        frames = len(out) // BYTES_PER_FRAME
        for at in range(start, min(frames, start + FADE_FRAMES - self._pos)):
            self._pos += 1
            self._last = self._peak * (FADE_FRAMES - self._pos) // FADE_FRAMES
            _SAMPLE.pack_into(out, at * BYTES_PER_FRAME, self._last)
        if self._mode == "tail" and self._pos >= FADE_FRAMES:
            self._mode = "playing"

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
