"""Share ElevenLabs name lookups among clients with the same credentials."""

from __future__ import annotations

import logging
from collections import OrderedDict
from threading import Lock
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.providers.elevenlabs_voices import DESCRIPTION_SEPARATOR, ElevenLabsVoices

if TYPE_CHECKING:
    from punt_vox.providers.elevenlabs_client import ElevenLabsClient

logger = logging.getLogger(__name__)

_VOICE_CACHE_TTL_S = 1800
_MAX_CATALOGS = 8

type _Entry = tuple[Lock, ElevenLabsVoices]


@final
class ElevenLabsCatalog:
    """Resolve names with bounded, credential-scoped catalog storage."""

    __slots__ = ("_entry", "_scope")
    _entry: _Entry
    _scope: bytes | None
    _shared: ClassVar[OrderedDict[bytes, _Entry]] = OrderedDict()
    _shared_lock: ClassVar[Lock] = Lock()

    def __new__(cls, client: ElevenLabsClient) -> Self:
        """Keep an isolated resolver until a credential-scoped lookup is needed."""
        self = super().__new__(cls)
        self._scope = client.catalog_scope
        self._entry = (
            Lock(),
            ElevenLabsVoices(
                lambda: cls._fetch(client),
                default_key="matilda",
                ttl_seconds=_VOICE_CACHE_TTL_S,
            ),
        )
        return self

    @property
    def default_name(self) -> str:
        """Return the default name without fetching the catalog."""
        return self._entry[1].default_key

    def resolve(self, name: str) -> tuple[str, str]:
        """Return the canonical name and ID, skipping the catalog for raw IDs."""
        if ElevenLabsVoices.is_id(name):
            return name, name
        lock, voices = self._catalog()
        with lock:
            return name.lower(), voices.resolve(name)

    def short_names(self) -> list[str]:
        """Return available short names without descriptions."""
        lock, voices = self._catalog()
        with lock:
            return [
                name for name in voices.list_all() if DESCRIPTION_SEPARATOR not in name
            ]

    def _catalog(self) -> _Entry:
        scope = self._scope
        if scope is None:
            return self._entry

        with self._shared_lock:
            entry = self._shared.setdefault(scope, self._entry)
            self._shared.move_to_end(scope)
            if len(self._shared) > _MAX_CATALOGS:
                self._shared.popitem(last=False)
            return entry

    @staticmethod
    def _fetch(client: ElevenLabsClient) -> dict[str, str]:
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
