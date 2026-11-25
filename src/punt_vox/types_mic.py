"""The microphone probe report a voxd ``mic_probe`` call returns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self, cast, final

__all__ = ["MicProbeReport"]


@final
@dataclass(frozen=True, slots=True)
class MicProbeReport:
    """What the daemon heard when it opened its default input device briefly.

    ``peak`` is the largest absolute 16-bit sample over the capture window,
    so a device macOS has silenced (no privacy grant for the daemon's
    process) reports ``peak == 0`` while still opening without error.
    ``overflows`` counts the callbacks PortAudio ran late, each of which
    dropped audio before it; a loaded host shows here. The report is the
    daemon's evidence; the verdict belongs to the caller.
    """

    device: str
    sample_rate_hz: int
    peak: int
    samples: int
    overflows: int

    @property
    def delivered(self) -> bool:
        """Return whether the device delivered any samples at all.

        A stream that opens but delivers nothing in the window is a device
        or driver that is not producing, not one that is silenced; the two
        need different advice.
        """
        return self.samples > 0

    @property
    def signal(self) -> bool:
        """Return whether any non-zero sample arrived."""
        return self.peak > 0

    def to_wire(self) -> dict[str, object]:
        """Return the JSON-ready mapping the ``mic_result`` frame carries."""
        return {
            "device": self.device,
            "sample_rate_hz": self.sample_rate_hz,
            "peak": self.peak,
            "samples": self.samples,
            "overflows": self.overflows,
        }

    @classmethod
    def from_wire(cls, raw: object) -> Self:
        """Build a report from a daemon reply; raise on a malformed one.

        Raises:
            ValueError: If *raw* is not a mapping with the five report fields.
        """
        if not isinstance(raw, Mapping):
            raise ValueError("mic probe reply is not a mapping")
        # Wire boundary: JSON yields object until each field is narrowed below.
        fields = cast("Mapping[str, object]", raw)
        device = fields.get("device")
        rate = fields.get("sample_rate_hz")
        peak = fields.get("peak")
        samples = fields.get("samples")
        overflows = fields.get("overflows")
        if not (
            isinstance(device, str)
            and isinstance(rate, int)
            and isinstance(peak, int)
            and isinstance(samples, int)
            and isinstance(overflows, int)
        ):
            raise ValueError("mic probe reply is missing a report field")
        return cls(
            device=device,
            sample_rate_hz=rate,
            peak=peak,
            samples=samples,
            overflows=overflows,
        )
