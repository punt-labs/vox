"""Judge the change from one music track to the next as the listener hears it.

voxd's player starts mpv with ``--gapless-audio=yes`` and no crossfade, so a
good change has no dead air and no loudness jump across the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from levels import meter

MAX_GAP_S = 1.0
"""Longest silence a listener may hear between two tracks."""

MAX_JUMP_LU = 6.0
"""Largest loudness step between the end of one track and the next."""

WINDOW_S = 3.0
"""Span measured on each side of the boundary (ebur128 needs at least 0.4 s)."""

_DETECT_S = 0.05


@dataclass(frozen=True, slots=True)
class Transition:
    """A rendered playback and the time, in seconds, one track gives way."""

    rendered: Path
    boundary: float

    @classmethod
    def between(
        cls, first: Path, second: Path, rendered: Path, volume: int
    ) -> Transition:
        """Render ``first`` then ``second`` through mpv and mark the change."""
        meter.render([first, second], rendered, volume)
        return cls(rendered=rendered, boundary=meter.duration(first))

    def gap(self) -> float:
        """Seconds of silence touching the boundary, or 0 when there is none."""
        low, high = self.boundary - WINDOW_S, self.boundary + WINDOW_S
        spans = meter.silences(self.rendered, _DETECT_S)
        return max(
            (span.duration for span in spans if span.start <= high and span.end >= low),
            default=0.0,
        )

    def jump(self) -> float:
        """Loudness difference, in LU, across the boundary."""
        before = meter.loudness(
            self.rendered, (max(self.boundary - WINDOW_S, 0.0), WINDOW_S)
        )
        after = meter.loudness(self.rendered, (self.boundary, WINDOW_S))
        return abs(before.integrated_lufs - after.integrated_lufs)

    def assert_smooth(self) -> None:
        gap = self.gap()
        assert gap <= MAX_GAP_S, f"{gap:.2f}s of silence between tracks"
        jump = self.jump()
        assert jump <= MAX_JUMP_LU, f"{jump:.1f} LU loudness jump between tracks"
