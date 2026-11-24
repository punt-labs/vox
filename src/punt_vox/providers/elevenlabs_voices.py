"""ElevenLabs lookup aliases and unambiguous voice suggestions."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import final

from punt_vox.providers.voice_resolver import VoiceResolver

_VOICE_ID_RE = re.compile(r"^[0-9a-zA-Z]{20}$")
DESCRIPTION_SEPARATOR = " - "


@final
class ElevenLabsVoices(VoiceResolver[str]):
    """Resolve ElevenLabs names and IDs from the account's voice catalog."""

    __slots__ = ()

    @staticmethod
    def is_id(name: str) -> bool:
        """Return whether a name is accepted directly as a voice ID."""
        return bool(_VOICE_ID_RE.match(name))

    def _available_names(self) -> list[str]:
        groups: dict[str, set[str]] = defaultdict(set)
        for name, voice_id in self._cache.items():
            short, _, _ = name.partition(DESCRIPTION_SEPARATOR)
            groups[short].add(voice_id)

        names = {voice_id: voice_id for voice_id in self._cache.values()}
        for name in sorted(self._cache, reverse=True):
            short, separator, _ = name.partition(DESCRIPTION_SEPARATOR)
            if not self.is_id(name) and (separator or len(groups[short]) == 1):
                names[self._cache[name]] = name
        return sorted(names.values())
