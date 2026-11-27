"""Choose where a call's caller audio comes from: a script or the microphone."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, final

from punt_vox.voxd.conversation_mode.mic_audio_source import MicAudioSource
from punt_vox.voxd.voice_call.inputs import ScriptInput
from punt_vox.voxd.voice_call.mic_input import MicInput

if TYPE_CHECKING:
    from punt_vox.voxd.voice_call.contracts import CallInput

__all__ = ["CallAudio"]


@final
class CallAudio:
    """Build a call's input from its start request."""

    __slots__ = ()

    @staticmethod
    async def from_message(msg: dict[str, object]) -> CallInput:
        """Return the scripted input when the request names a script, else the mic."""
        if "script" in msg:
            return await asyncio.to_thread(ScriptInput.from_wire, msg["script"])
        return MicInput.from_wire(msg, MicAudioSource.with_portaudio())
