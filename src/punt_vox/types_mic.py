"""The microphone probe report a voxd ``mic_probe`` call returns."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Self, cast, final

__all__ = ["MicProbeReport"]


@final
class MicProbeReport:
    """What the daemon heard when it opened its default input device briefly.

    ``peak`` is the largest absolute 16-bit sample over the capture window,
    so a device macOS has silenced (no privacy grant for the daemon's
    process) reports ``peak == 0`` while still opening without error.
    ``overflows`` counts the callbacks PortAudio ran late, each of which
    dropped audio before it; a loaded host shows here. ``platform`` is the
    daemon's ``sys.platform``: the doctor may run on another machine, and
    the advice for silence depends on where the device is. The report is
    the daemon's evidence; the verdict belongs to the caller, and the
    evidence is read-only.
    """

    __slots__ = (
        "_device",
        "_overflows",
        "_peak",
        "_platform",
        "_sample_rate_hz",
        "_samples",
    )
    _device: str
    _sample_rate_hz: int
    _peak: int
    _samples: int
    _overflows: int
    _platform: str

    def __new__(
        cls,
        *,
        device: str,
        sample_rate_hz: int,
        peak: int,
        samples: int,
        overflows: int,
        platform: str,
    ) -> Self:
        """Record one capture window's evidence."""
        self = super().__new__(cls)
        self._device = device
        self._sample_rate_hz = sample_rate_hz
        self._peak = peak
        self._samples = samples
        self._overflows = overflows
        self._platform = platform
        return self

    @property
    def device(self) -> str:
        """The input device's name as PortAudio reports it."""
        return self._device

    @property
    def sample_rate_hz(self) -> int:
        """The capture's sample rate, in hertz."""
        return self._sample_rate_hz

    @property
    def peak(self) -> int:
        """The largest absolute 16-bit sample in the window."""
        return self._peak

    @property
    def samples(self) -> int:
        """How many samples the device delivered."""
        return self._samples

    @property
    def overflows(self) -> int:
        """How many callbacks ran late and dropped audio."""
        return self._overflows

    @property
    def platform(self) -> str:
        """The daemon's ``sys.platform``, where the device is."""
        return self._platform

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MicProbeReport):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash((MicProbeReport, *self._key()))

    def __repr__(self) -> str:
        return (
            f"MicProbeReport(device={self._device!r},"
            f" sample_rate_hz={self._sample_rate_hz!r}, peak={self._peak!r},"
            f" samples={self._samples!r}, overflows={self._overflows!r},"
            f" platform={self._platform!r})"
        )

    def _key(self) -> tuple[str, int, int, int, int, str]:
        return (
            self._device,
            self._sample_rate_hz,
            self._peak,
            self._samples,
            self._overflows,
            self._platform,
        )

    @property
    def delivered(self) -> bool:
        """Return whether the device delivered any samples at all.

        A stream that opens but delivers nothing in the window is a device
        or driver that is not producing, not one that is silenced; the two
        need different advice.
        """
        return self._samples > 0

    @property
    def signal(self) -> bool:
        """Return whether any non-zero sample arrived."""
        return self._peak > 0

    def to_wire(self) -> dict[str, object]:
        """Return the JSON-ready mapping the ``mic_result`` frame carries."""
        return {
            "device": self._device,
            "sample_rate_hz": self._sample_rate_hz,
            "peak": self._peak,
            "samples": self._samples,
            "overflows": self._overflows,
            "platform": self._platform,
        }

    @classmethod
    def from_wire(cls, raw: object) -> Self:
        """Build a report from a daemon reply; raise on a malformed one.

        Raises:
            ValueError: If *raw* is not a mapping with the six report fields.
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
        platform = fields.get("platform")
        if not (
            isinstance(device, str)
            and isinstance(rate, int)
            and isinstance(peak, int)
            and isinstance(samples, int)
            and isinstance(overflows, int)
            and isinstance(platform, str)
        ):
            raise ValueError("mic probe reply is missing a report field")
        return cls(
            device=device,
            sample_rate_hz=rate,
            peak=peak,
            samples=samples,
            overflows=overflows,
            platform=platform,
        )
