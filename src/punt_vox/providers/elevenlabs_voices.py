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
        described: set[tuple[str, str]] = set()
        for name, voice_id in self._cache.items():
            short, separator, _ = name.partition(DESCRIPTION_SEPARATOR)
            groups[short].add(voice_id)
            if separator:
                described.add((short, voice_id))

        names = {voice_id: voice_id for voice_id in self._cache.values()}
        for name in sorted(self._cache, reverse=True):
            short, separator, _ = name.partition(DESCRIPTION_SEPARATOR)
            voice_id = self._cache[name]
            if self.is_id(name):
                continue
            if (
                not separator
                and len(groups[short]) > 1
                and (short, voice_id) in described
            ):
                continue
            names[voice_id] = name
        return sorted(names.values())
