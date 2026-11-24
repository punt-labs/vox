"""One synchronized ElevenLabs voice resolver that owns no credential."""

from __future__ import annotations

import logging
from contextvars import ContextVar
from threading import Lock
from typing import TYPE_CHECKING, Self, final

from punt_vox.providers.elevenlabs_voices import DESCRIPTION_SEPARATOR, ElevenLabsVoices

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.providers.elevenlabs_client import ElevenLabsClient

logger = logging.getLogger(__name__)

DEFAULT_NAME = "matilda"
_VOICE_CACHE_TTL_S = 1800
_lookup_client: ContextVar[ElevenLabsClient] = ContextVar("elevenlabs_catalog_client")


@final
class ElevenLabsCatalogEntry:
    """Run one catalog lookup at a time so concurrent misses share a fetch."""

    __slots__ = ("_lock", "_voices")
    _lock: Lock
    _voices: ElevenLabsVoices

    def __new__(cls) -> Self:
        """Create an empty catalog entry without binding it to a client."""
        self = super().__new__(cls)
        self._lock = Lock()
        self._voices = ElevenLabsVoices(
            cls._fetch, default_key=DEFAULT_NAME, ttl_seconds=_VOICE_CACHE_TTL_S
        )
        return self

    def lookup[R](
        self, client: ElevenLabsClient, operation: Callable[[ElevenLabsVoices], R]
    ) -> R:
        """Use the caller's client for any refresh, then forget it."""
        with self._lock:
            token = _lookup_client.set(client)
            try:
                return operation(self._voices)
            finally:
                _lookup_client.reset(token)

    @staticmethod
    def _fetch() -> dict[str, str]:
        client = _lookup_client.get()
        fresh: dict[str, str] = {}
        for voice in client.call(lambda sdk: sdk.voices.get_all()).voices:
            if voice.name is None:  # unnamed voices are reachable by raw id only
                continue
            full_name = voice.name.lower()
            fresh.setdefault(full_name, voice.voice_id)
            fresh.setdefault(
                full_name.split(DESCRIPTION_SEPARATOR, 1)[0], voice.voice_id
            )

        logger.debug("Fetched %d voice entries from ElevenLabs API", len(fresh))
        return fresh
