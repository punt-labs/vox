"""Judge the change from one music track to the next as the listener hears it.

voxd loads one track at a time into mpv with no crossfade, so a good change
has no dead air at the boundary and no loudness step from one track's body to
the next. The step compares whole tracks: a window at the boundary would
measure a fade, not the level the listener settles into.
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
"""Span searched for silence on each side of the boundary."""

_DETECT_S = 0.05


@dataclass(frozen=True, slots=True)
class Transition:
    """A rendered playback and the time, in seconds, one track gives way."""

    rendered: Path
    boundary: float
    first: Path
    second: Path

    @classmethod
    def between(
        cls, first: Path, second: Path, rendered: Path, volume: int
    ) -> Transition:
        """Render ``first`` then ``second`` through mpv and mark the change."""
        meter.render([first, second], rendered, volume)
        return cls(rendered, meter.duration(first), first, second)

    def gap(self) -> float:
        """Seconds of silence touching the boundary, or 0 when there is none."""
        low, high = self.boundary - WINDOW_S, self.boundary + WINDOW_S
        spans = meter.silences(self.rendered, _DETECT_S)
        return max(
            (span.duration for span in spans if span.start <= high and span.end >= low),
            default=0.0,
        )

    def jump(self) -> float:
        """Integrated loudness difference, in LU, between the two tracks."""
        before = meter.loudness(self.first).integrated_lufs
        after = meter.loudness(self.second).integrated_lufs
        return abs(before - after)

    def assert_smooth(self) -> None:
        gap = self.gap()
        assert gap <= MAX_GAP_S, f"{gap:.2f}s of silence between tracks"
        jump = self.jump()
        assert jump <= MAX_JUMP_LU, f"{jump:.1f} LU loudness jump between tracks"
