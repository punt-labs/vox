"""What one sink's flush and close report: audio and dry spells."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

from punt_vox.voxd.pcm_audio import PcmDuration
from punt_vox.voxd.pcm_loss import DeviceLoss
from punt_vox.voxd.pcm_output import OutputDeviceLostError

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
    """How one sink's stream ended: audio fed, heard and discarded.

    The stream spans every agent utterance from the first reply to the close.
    ``underflows`` counts silences after which the *same* utterance resumed:
    the buffer ran dry while audio was late. ``gap_total`` and ``gap_longest``
    are the silence those added up to and the longest one, at any length. A
    silence that ended where a new utterance began (a turn, or a pause for a
    tool call) is counted in ``idle_gaps`` and never as starvation. A silence
    the stream ended in (a barge-in, or the hang-up) cannot be told from
    either, so it is neither: its length is ``trailing_gap``.
    ``device_flags`` counts callbacks PortAudio flagged (under/overflow,
    priming), and ``device_loss`` says why the device was lost, if it was.
    ``lost_before_flush`` is set when a barge-in found the device already lost.
    """

    fed: PcmDuration
    played: PcmDuration
    dropped: PcmDuration
    interrupted: bool
    underflows: int
    gap_total: PcmDuration
    gap_longest: PcmDuration
    idle_gaps: int
    trailing_gap: PcmDuration
    device_flags: int
    device_loss: DeviceLoss | None  # None while the device stayed healthy.
    lost_before_flush: bool

    @property
    def degraded(self) -> bool:
        """Return whether the device starved, flagged, or stopped on its own."""
        return bool(
            self.underflows
            or self.device_flags
            or self.device_lost
            or self.lost_before_flush
        )

    @property
    def device_lost(self) -> bool:
        """Return whether the device stopped, stalled or never confirmed."""
        return self.device_loss is not None

    def check_played_out(self) -> None:
        """Raise :class:`OutputDeviceLostError` if audio was lost to the device.

        A flush (a barge-in) is deliberate, and a device that merely confirmed
        late heard every block: neither fails the stream.
        """
        loss = self.device_loss
        if loss is not None and loss.is_fatal and not self.interrupted:
            raise OutputDeviceLostError(loss.message)

    @property
    def note(self) -> str | None:
        """Return a non-fatal degradation a call status should show, if any."""
        if self.interrupted:
            if self.lost_before_flush:
                return "output device was lost before the reply was cut"
            return None
        if self.device_loss is DeviceLoss.UNCONFIRMED:
            return self.device_loss.message
        return None

    def describe(self, elapsed_ms: float) -> str:
        """Return the closing log line for a stream that lived *elapsed_ms*."""
        return (
            f"PCM stream closed at {elapsed_ms:.0f} ms: "
            f"fed {self.fed.ms:.0f} ms, played {self.played.ms:.0f} ms, "
            f"dropped {self.dropped.ms:.0f} ms, interrupted={self.interrupted}, "
            f"underflows={self.underflows} (total {self.gap_total.ms:.0f} ms, "
            f"longest {self.gap_longest.ms:.0f} ms), idle_gaps={self.idle_gaps}, "
            f"trailing_gap={self.trailing_gap.ms:.0f} ms, "
            f"device_flags={self.device_flags}, device_loss={self.device_loss}"
        )
