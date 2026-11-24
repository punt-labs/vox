"""Live caller audio for a voice call: the microphone, watched for silence."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.voxd.voice_call.capture_probe import CaptureProbe

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from punt_vox.voxd.conversation_mode.mic_audio_source import MicAudioSource

__all__ = ["MicInput"]


@final
class MicInput:
    """Stream the microphone for as long as the call consumes it."""

    __slots__ = ("_source",)
    _source: MicAudioSource
    # The echo declarations a live call accepts: the caller wears a headset,
    # or the system cancels the speaker's echo before it reaches the mic.
    ECHO_CONTROL: ClassVar[frozenset[str]] = frozenset({"headset", "aec"})

    def __new__(cls, source: MicAudioSource) -> Self:
        """Bind the microphone source the call captures from."""
        self = super().__new__(cls)
        self._source = source
        return self

    @classmethod
    def echo_label(cls, value: object) -> str:
        """Name a request's echo declaration for the log; a stray one is "other"."""
        accepted = cls.ECHO_CONTROL | {"none"}
        return value if isinstance(value, str) and value in accepted else "other"

    @classmethod
    def from_wire(cls, msg: dict[str, object], source: MicAudioSource) -> Self:
        """Accept a live call only when the request declares echo protection.

        Raises:
            ValueError: When the request declares neither a headset nor AEC.
        """
        if msg.get("echo_control") not in cls.ECHO_CONTROL:
            raise ValueError(
                "Live calls require echo protection: wear a headset and use "
                "'vox call --headset', or configure system echo cancellation "
                "and use 'vox call --aec'"
            )
        return cls(source)

    def chunks(self) -> AsyncIterator[bytes]:
        """Yield each captured block's PCM, failing a capture that stays silent.

        Raises:
            ValueError: When the first seconds hold only zero samples.
        """
        return CaptureProbe().watch(self._source.chunks())
