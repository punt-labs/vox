"""Synchronous provider invocation and completed direct-play results."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from punt_vox.providers.espeak import EspeakProvider
from punt_vox.providers.local_play import EspeakDirectPlayer, SayDirectPlayer
from punt_vox.providers.say import SayProvider
from punt_vox.types import AudioRequest, DirectPlayProvider, TTSProvider
from punt_vox.voxd.api_key_env import ApiKeyEnv

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DirectPlayInvocation:
    """Construct and invoke a direct player inside its provider environment."""

    provider_name: str
    key_env: ApiKeyEnv
    provider_factory: Callable[[], TTSProvider]
    request: AudioRequest

    def run(self) -> int | None:
        """Play synchronously, returning None when the provider opts out."""
        with self.key_env.applied():
            return self._play()

    def _play(self) -> int | None:
        """Dispatch to the direct player supported by this provider."""
        provider = self.provider_factory()
        if self.provider_name == "say" and isinstance(provider, SayProvider):
            player = SayDirectPlayer(voices=provider._voices)  # pyright: ignore[reportPrivateUsage]
            return player.play_directly(self.request)
        if self.provider_name == "espeak" and isinstance(provider, EspeakProvider):
            espeak_player = EspeakDirectPlayer(
                binary=provider._binary,  # pyright: ignore[reportPrivateUsage]
                voices=provider._voices,  # pyright: ignore[reportPrivateUsage]
            )
            return espeak_player.play_directly(self.request)
        if isinstance(provider, DirectPlayProvider):
            return provider.play_directly(self.request)
        return None


@dataclass(frozen=True, slots=True)
class DirectPlayResult:
    """A completed direct play's metadata, without the spoken text."""

    provider: str
    chars: int
    elapsed: float
    rc: int

    def publish(self, record_result: Callable[..., object]) -> None:
        """Record the completed playback and log its exit status."""
        record_result(
            path=Path(f"<direct:{self.provider}>"),
            rc=self.rc,
            elapsed=self.elapsed,
            stderr="" if self.rc == 0 else f"play_directly rc={self.rc}",
        )
        if self.rc == 0:
            logger.info(
                "spoke %d chars (%.1fs)",
                self.chars,
                self.elapsed,
            )
        else:
            logger.error(
                "Direct-play FAILED: elapsed=%.3fs rc=%d",
                self.elapsed,
                self.rc,
            )
