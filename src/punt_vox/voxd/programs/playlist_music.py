"""PlaylistMusic -- the production playlist producer, composed in one place.

The daemon's composition root asks for the producer here instead of wiring
the ElevenLabs provider, the edge trim, and the length policy itself, so the
music recipe lives beside the Programs package that consumes it.
"""

from __future__ import annotations

import os
from typing import final

from punt_vox.providers.credentials import ProviderCredentials
from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider
from punt_vox.voxd.programs.edge_trim import EdgeTrim, EdgeTrimmedMusic
from punt_vox.voxd.programs.music_producer import LengthPolicy, MusicProducer

__all__ = ["PlaylistMusic"]


@final
class PlaylistMusic:
    """Build the playlist ``MusicProducer`` voxd runs in production."""

    __slots__ = ()

    @staticmethod
    def producer() -> MusicProducer:
        """Return ElevenLabs music, trimmed of edge silence, at varied lengths.

        The key is read here, from the variable the readiness gate names, and
        passed explicitly. Boot calls this after the keys file is loaded and
        before any request runs, so the long-lived music client holds the
        daemon's own key -- never one a synthesis request has swapped into the
        environment for its own duration.
        """
        key_var = ProviderCredentials().api_key_env_vars()["elevenlabs"]
        music = ElevenLabsMusicProvider(api_key=os.environ.get(key_var))
        return MusicProducer(EdgeTrimmedMusic(music, EdgeTrim()), LengthPolicy())
