"""The input and output devices a call runs on, and whether they share hardware.

A headset carries both legs, so the agent's voice never reaches the
microphone. A built-in microphone with Bluetooth output, or a Bluetooth
microphone with the built-in speakers, is the echo configuration: the
speakers feed the microphone and the agent interrupts itself. The pair
logs itself at call start so the daemon log names both devices and
says which configuration this is.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.voxd.log_text import LogText

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.input_device import InputDevice
    from punt_vox.voxd.conversation_mode.output_device import OutputDevice

__all__ = ["DevicePair"]

logger = logging.getLogger(__name__)

# The role words an operating system appends to one piece of hardware's two
# PortAudio devices: "MacBook Air Microphone" and "MacBook Air Speakers" are
# the same machine. A headset reports one name for both legs.
_ROLE_WORDS: Final = frozenset(
    {"microphone", "mic", "speakers", "speaker", "headphones", "input", "output"}
)


@final
class DevicePair:
    """The default input and output devices at one call's start."""

    __slots__ = ("_input", "_output")
    _input: InputDevice
    _output: OutputDevice

    def __new__(cls, input_device: InputDevice, output_device: OutputDevice) -> Self:
        """Pair the two devices PortAudio reports as defaults."""
        self = super().__new__(cls)
        self._input = input_device
        self._output = output_device
        return self

    @property
    def input(self) -> InputDevice:
        """The default input device."""
        return self._input

    @property
    def output(self) -> OutputDevice:
        """The default output device."""
        return self._output

    @property
    def shared_hardware(self) -> bool:
        """Whether both legs name one piece of hardware.

        Names are compared with their trailing role words removed, so the
        built-in microphone and speakers of one machine count as shared,
        as does a headset reporting the same name for both.
        """
        return self._stem(self._input.name) == self._stem(self._output.name)

    def log(self, *, sample_rate_hz: int) -> None:
        """Name both devices in the daemon log, and say whether they share hardware.

        The devices name themselves, so their text is made safe for one
        line each: a name with a line break may not forge a log line.
        """
        logger.info(
            "Call input device: %s (default %.0f Hz), capturing at %d Hz",
            LogText.reason(self._input.name),
            self._input.default_samplerate_hz,
            sample_rate_hz,
        )
        logger.info(
            "Call output device: %s (default %.0f Hz)",
            LogText.reason(self._output.name),
            self._output.default_samplerate_hz,
        )
        if self.shared_hardware:
            logger.info(
                "Call audio hardware: input and output are the same hardware (%s)",
                LogText.reason(self._stem(self._output.name) or self._output.name),
            )
        else:
            logger.warning(
                "Call echo configuration: input %s and output %s are different "
                "hardware, so the speaker output can reach the microphone",
                LogText.reason(self._input.name),
                LogText.reason(self._output.name),
            )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DevicePair):
            return NotImplemented
        return (self._input, self._output) == (other._input, other._output)

    def __hash__(self) -> int:
        return hash((DevicePair, self._input, self._output))

    def __repr__(self) -> str:
        return f"DevicePair({self._input!r}, {self._output!r})"

    @staticmethod
    def _stem(name: str) -> str:
        """Lower-case *name* with its trailing role words removed."""
        words = name.lower().split()
        while words and words[-1] in _ROLE_WORDS:
            words.pop()
        return " ".join(words)
