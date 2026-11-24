"""Advisory fields for successful speech replies."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.types_synthesis import DEFAULT_RATE
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from punt_vox.types_synthesis import SynthesisSpec


@final
class SpeechReply(WireReply):
    """Add provider advisories to audio replies."""

    __slots__ = ()

    async def audio(self, spec: SynthesisSpec, payload: dict[str, object]) -> None:
        """Send audio metadata, with an advisory if ElevenLabs ignored the rate."""
        rate = spec.rate
        provider = (spec.provider or "").lower()
        if provider == "elevenlabs" and rate not in (None, DEFAULT_RATE):
            payload = {
                **payload,
                "warning": f"ElevenLabs ignores the requested speech rate ({rate}%).",
            }
        await self.send(payload)
