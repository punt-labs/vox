"""The output device PortAudio plays through by default, as it reports itself."""

from __future__ import annotations

from typing import Self, final

__all__ = ["OutputDevice"]


@final
class OutputDevice:
    """The default output device, by the name PortAudio reports."""

    __slots__ = ("_default_samplerate_hz", "_name")
    _name: str
    _default_samplerate_hz: float

    def __new__(cls, name: str, default_samplerate_hz: float) -> Self:
        """Build a device from the name and default sample rate PortAudio reports.

        Raises:
            ValueError: When the name is empty or the sample rate is not positive.
        """
        if not name:
            msg = "output device name must not be empty"
            raise ValueError(msg)
        if not default_samplerate_hz > 0:  # Written so NaN fails the check too.
            msg = f"output device sample rate must be positive: {default_samplerate_hz}"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._name = name
        self._default_samplerate_hz = default_samplerate_hz
        return self

    @property
    def name(self) -> str:
        """The device name as PortAudio reports it."""
        return self._name

    @property
    def default_samplerate_hz(self) -> float:
        """The device's default sample rate, in hertz."""
        return self._default_samplerate_hz

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, OutputDevice):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash((OutputDevice, *self._key()))

    def __repr__(self) -> str:
        return f"OutputDevice({self._name!r}, {self._default_samplerate_hz!r})"

    def _key(self) -> tuple[str, float]:
        return (self._name, self._default_samplerate_hz)
