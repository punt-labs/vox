"""The capture backend a microphone probe needs: the default input, opened once.

A probe asks for one thing a call does not: the current default input device
and a started stream on it, selected and opened as one step, with no output
device resolved. This protocol names that one method so the probe can be
tested against a fake and :class:`~.portaudio.PortAudio` is one of its
implementations, not its only one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Type-only: the name is declared in ``typings/sounddevice/__init__.pyi``
    # for the type checkers; the stub-free runtime module has no such attribute.
    from sounddevice import RawInputCallback

    from punt_vox.voxd.conversation_mode.input_device import InputDevice
    from punt_vox.voxd.conversation_mode.mic_audio_source import InputStreamHandle

__all__ = ["ProbeBackend"]


@runtime_checkable
class ProbeBackend(Protocol):
    """Opens the current default input device as a started raw mono stream.

    The pair returned names the device the stream captures from; the backend
    selects and opens it as one step so the two cannot disagree.
    """

    def open_default_input(
        self, *, samplerate: float, blocksize: int, callback: RawInputCallback
    ) -> tuple[InputDevice, InputStreamHandle]:
        """Return the default input device and a started stream on it, or raise."""
        ...
