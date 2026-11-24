"""Fetch and validate a provider voice roster for CLI and MCP clients."""

from __future__ import annotations

import random
from functools import partial
from typing import TYPE_CHECKING, cast, final

from punt_vox.cascade import Cascade, RosterError
from punt_vox.types_errors import VoiceNotFoundError
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voices import VOICE_BLURBS

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.cascade import RosterClient

__all__ = ["RosterError", "VoiceRoster"]

_FEATURED_CAP = 6


@final
class VoiceRoster:
    """Validate and present the daemon's provider voice roster."""

    __slots__ = ()

    @staticmethod
    def fetch(client: RosterClient, provider: str) -> list[str] | RosterError:
        """Fetch advertised names, rejecting malformed roster replies."""
        roster = Cascade.fetch_roster(client, provider)
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
    def selection_error(client: RosterClient, provider: str, name: str) -> str | None:
        """Return a selection error, or None for an advertised name or raw ID."""
        if (
            provider.lower() == "elevenlabs"
            and SynthesisSpec.ELEVENLABS_VOICE_ID.fullmatch(name)
        ):
            return None
        roster = VoiceRoster.fetch(client, provider)
        if isinstance(roster, RosterError):
            return roster.message
        key = name.lower()
        if all(voice.lower() != key for voice in roster):
            return str(VoiceNotFoundError(name, roster))
        return None

    @staticmethod
    def provider_check(provider: str) -> Callable[[dict[str, str]], None]:
        """Build a check that rejects a removed or changed config provider."""
        return partial(VoiceRoster._require_same_provider, provider)

    @staticmethod
    def _require_same_provider(provider: str, fields: dict[str, str]) -> None:
        active = fields.get("provider") or None
        if (active or "").lower() != provider.lower():
            msg = (
                f"The active provider changed from {provider!r} to {active!r}. "
                "Retry setting the voice."
            )
            raise ValueError(msg)

    @staticmethod
    def featured(provider: str | None, roster: list[str]) -> list[dict[str, str]]:
        """Sample up to six roster names that have provider-specific blurbs."""
        featured = [
            {"name": name, "blurb": blurb}
            for (prov, name), blurb in VOICE_BLURBS.items()
            if prov == provider and name in roster
        ]
        return random.sample(featured, min(_FEATURED_CAP, len(featured)))
