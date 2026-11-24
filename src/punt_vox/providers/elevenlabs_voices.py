"""ElevenLabs lookup aliases and unambiguous voice suggestions."""

from __future__ import annotations

import re
from typing import final

from punt_vox.providers.voice_resolver import VoiceResolver

_VOICE_ID_RE = re.compile(r"^[0-9a-zA-Z]{20}$")


@final
class ElevenLabsVoices(VoiceResolver[str]):
    """Resolve ElevenLabs names and IDs from the account's voice catalog."""

    __slots__ = ()

    @staticmethod
    def is_id(name: str) -> bool:
        """Return whether a name is accepted directly as a voice ID."""
        return bool(_VOICE_ID_RE.match(name))

    def _available_names(self) -> list[str]:
        names = {voice_id: voice_id for voice_id in self._cache.values()}
        names.update(
            (self._cache[name], name)
            for name in sorted(self._cache, reverse=True)
            if not self.is_id(name)
        )
        return sorted(names.values())
