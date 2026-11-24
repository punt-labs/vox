"""ElevenLabs lookup aliases and unambiguous voice suggestions."""

from __future__ import annotations

from collections import defaultdict
from typing import final

from punt_vox.providers.voice_resolver import VoiceResolver
from punt_vox.types_synthesis import SynthesisSpec

DESCRIPTION_SEPARATOR = " - "


@final
class ElevenLabsVoices(VoiceResolver[str]):
    """Resolve ElevenLabs names and IDs from the account's voice catalog."""

    __slots__ = ()

    @staticmethod
    def is_id(name: str) -> bool:
        """Return whether a name is accepted directly as a voice ID."""
        return SynthesisSpec.ELEVENLABS_VOICE_ID.fullmatch(name) is not None

    def _available_names(self) -> list[str]:
        colliding_aliases = self._colliding_aliases()
        names = {voice_id: voice_id for voice_id in self._cache.values()}
        for name in sorted(self._cache, reverse=True):
            if name not in colliding_aliases and not self.is_id(name):
                names[self._cache[name]] = name
        return sorted(names.values())

    def _colliding_aliases(self) -> set[str]:
        groups: dict[str, set[str]] = defaultdict(set)
        described: set[tuple[str, str]] = set()
        for name, voice_id in self._cache.items():
            short, separator, _ = name.partition(DESCRIPTION_SEPARATOR)
            groups[short].add(voice_id)
            if separator:
                described.add((short, voice_id))
        return {
            short
            for short, voice_id in described
            if len(groups[short]) > 1 and self._cache.get(short) == voice_id
        }
