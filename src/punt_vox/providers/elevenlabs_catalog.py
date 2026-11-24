"""Share ElevenLabs name lookups among clients with the same credentials."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.providers.elevenlabs_catalog_entry import (
    DEFAULT_NAME,
    ElevenLabsCatalogEntry,
)
from punt_vox.providers.elevenlabs_voices import DESCRIPTION_SEPARATOR, ElevenLabsVoices

if TYPE_CHECKING:
    from punt_vox.providers.elevenlabs_client import ElevenLabsClient

_MAX_CATALOGS = 8


@final
class ElevenLabsCatalog:
    """Resolve names with bounded, credential-scoped catalog storage."""

    __slots__ = ("_client", "_scope")
    _client: ElevenLabsClient
    _scope: bytes | ElevenLabsCatalogEntry
    _shared: ClassVar[OrderedDict[bytes, ElevenLabsCatalogEntry]] = OrderedDict()
    _shared_lock: ClassVar[Lock] = Lock()

    def __new__(cls, client: ElevenLabsClient) -> Self:
        """Select a credential scope or keep an isolated resolver."""
        self = super().__new__(cls)
        self._client = client
        scope = client.catalog_scope
        self._scope = ElevenLabsCatalogEntry() if scope is None else scope
        return self

    @property
    def default_name(self) -> str:
        """Return the default name without fetching the catalog."""
        return DEFAULT_NAME

    def resolve(self, name: str) -> tuple[str, str]:
        """Return the canonical name and ID, skipping the catalog for raw IDs."""
        if ElevenLabsVoices.is_id(name):
            return name, name
        return name.lower(), self._catalog().lookup(
            self._client, lambda voices: voices.resolve(name)
        )

    def short_names(self) -> list[str]:
        """Return available short names without descriptions."""
        names = self._catalog().lookup(self._client, ElevenLabsVoices.list_all)
        return [name for name in names if DESCRIPTION_SEPARATOR not in name]

    def _catalog(self) -> ElevenLabsCatalogEntry:
        scope = self._scope
        if isinstance(scope, ElevenLabsCatalogEntry):
            return scope

        with self._shared_lock:
            entry = self._shared.get(scope)
            if entry is None:
                entry = ElevenLabsCatalogEntry()
                self._shared[scope] = entry
            self._shared.move_to_end(scope)
            if len(self._shared) > _MAX_CATALOGS:
                self._shared.popitem(last=False)
            return entry
