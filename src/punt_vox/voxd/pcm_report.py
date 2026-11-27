"""What one streamed utterance's flush and close report: audio and dry spells."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from punt_vox.voxd.pcm_audio import PcmDuration

__all__ = ["FlushReport", "StreamReport"]


@final
@dataclass(frozen=True, slots=True)
class FlushReport:
    """What one :meth:`PcmStreamSink.flush` discarded and how long it took."""

    dropped: PcmDuration
    elapsed_ms: float


@final
@dataclass(frozen=True, slots=True)
class StreamReport:
    """How one utterance's stream ended: audio fed, heard and discarded.

    ``underflows`` counts dry spells: the buffer ran empty and cut audio that
    was playing while the response was still arriving. ``gap_total`` and
    ``gap_longest`` are the silence those spells added up to and the longest
    one. A pause longer than the idle limit is a turn boundary, counted in
    ``idle_gaps`` and never as starvation. ``device_flags`` counts callbacks
    PortAudio flagged (under/overflow, priming), and ``device_lost`` is set
    when the device stopped on its own.
    """

    fed: PcmDuration
    played: PcmDuration
    dropped: PcmDuration
    interrupted: bool
    underflows: int
    gap_total: PcmDuration
    gap_longest: PcmDuration
    idle_gaps: int
    device_flags: int
    device_lost: bool

    @property
    def degraded(self) -> bool:
        """Return whether the device starved, flagged, or stopped on its own."""
        return bool(self.underflows or self.device_flags or self.device_lost)

    def describe(self, elapsed_ms: float) -> str:
        """Return the closing log line for a stream that lived *elapsed_ms*."""
        return (
            f"PCM stream closed at {elapsed_ms:.0f} ms: "
            f"fed {self.fed.ms:.0f} ms, played {self.played.ms:.0f} ms, "
            f"dropped {self.dropped.ms:.0f} ms, interrupted={self.interrupted}, "
            f"underflows={self.underflows} (total {self.gap_total.ms:.0f} ms, "
            f"longest {self.gap_longest.ms:.0f} ms), idle_gaps={self.idle_gaps}, "
            f"device_flags={self.device_flags}, device_lost={self.device_lost}"
        )
