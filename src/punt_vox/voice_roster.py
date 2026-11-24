"""Fetch and validate a provider voice roster for CLI and MCP clients."""

from __future__ import annotations

import random
from functools import partial
from typing import TYPE_CHECKING, cast, final

from punt_vox.cascade import Cascade, RosterError
from punt_vox.types_errors import VoiceNotFoundError
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.types_synthesis_errors import ProviderNotConfiguredError
from punt_vox.voices import VOICE_BLURBS

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.types_voice_roster import FeaturedVoice
    from punt_vox.voice_target import RosterClient, VoiceTarget

__all__ = ["RosterError", "VoiceRoster"]


@final
class VoiceRoster:
    """Validate and present the daemon's provider voice roster."""

    __slots__ = ()

    @staticmethod
    def fetch(client: RosterClient, target: VoiceTarget) -> list[str] | RosterError:
        """Fetch advertised names, rejecting malformed roster replies."""
        roster = Cascade.fetch_roster(client, target)
        if isinstance(roster, RosterError):
            return roster
        if not VoiceRoster._is_voice_list(roster):
            return RosterError("voxd returned an invalid voice roster.")
        return roster

    @staticmethod
    def _is_voice_list(roster: object) -> bool:
        return isinstance(roster, list) and all(
            isinstance(name, str) for name in cast("list[object]", roster)
        )

    @staticmethod
    def selection_error(
        client: RosterClient, target: VoiceTarget, name: str
    ) -> str | None:
        """Return a selection error, or None for an advertised name or raw ID."""
        match_id = SynthesisSpec.ELEVENLABS_VOICE_ID.fullmatch
        if target.provider.lower() == "elevenlabs" and match_id(name):
            return None
        roster = VoiceRoster.fetch(client, target)
        if isinstance(roster, RosterError):
            return roster.message
        key = name.lower()
        if all(voice.lower() != key for voice in roster):
            return str(VoiceNotFoundError(name, roster))
        return None

    @staticmethod
    def selection_check(target: VoiceTarget) -> Callable[[dict[str, str]], None]:
        """Build the MCP config check for the provider and model that were read."""
        return partial(VoiceRoster._require_current, target)

    @staticmethod
    def _require_current(target: VoiceTarget, fields: dict[str, str]) -> None:
        active = fields.get("provider")
        if not active:
            raise ProviderNotConfiguredError.for_mcp()
        target.require_current(fields)

    @staticmethod
    def featured(provider: str | None, roster: list[str]) -> list[FeaturedVoice]:
        """Sample up to six roster names that have provider-specific blurbs."""
        featured: list[FeaturedVoice] = [
            {"name": name, "blurb": blurb}
            for (prov, name), blurb in VOICE_BLURBS.items()
            if prov == provider and name in roster
        ]
        return random.sample(featured, min(6, len(featured)))
