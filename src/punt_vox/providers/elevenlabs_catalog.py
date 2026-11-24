"""Share ElevenLabs name lookups among clients with the same credentials."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import TYPE_CHECKING, ClassVar, Self, final
from weakref import WeakValueDictionary

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

    __slots__ = ("_client", "_local", "_scope")
    _client: ElevenLabsClient
    _local: ElevenLabsCatalogEntry
    _scope: bytes | None
    _shared: ClassVar[OrderedDict[bytes, ElevenLabsCatalogEntry]] = OrderedDict()
    _shared_lock: ClassVar[Lock] = Lock()
    # An evicted entry can still coordinate lookups until its callers release it.
    _live: ClassVar[WeakValueDictionary[bytes, ElevenLabsCatalogEntry]] = (
        WeakValueDictionary()
    )

    def __new__(cls, client: ElevenLabsClient) -> Self:
        """Capture the credential scope and create a provider-local resolver."""
        self = super().__new__(cls)
        self._client = client
        self._local = ElevenLabsCatalogEntry()
        self._scope = client.catalog_scope
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
        """Return short names from the provider-local roster."""
        names = self._local.lookup(self._client, ElevenLabsVoices.list_all)
        return [name for name in names if DESCRIPTION_SEPARATOR not in name]

    def _catalog(self) -> ElevenLabsCatalogEntry:
        if (scope := self._scope) is None:
            return self._local

        with self._shared_lock:
            entry = self._live.get(scope)
            if entry is None:
                entry = ElevenLabsCatalogEntry()
                self._live[scope] = entry
            self._shared[scope] = entry
            self._shared.move_to_end(scope)
            if len(self._shared) > _MAX_CATALOGS:
                self._shared.popitem(last=False)
            return entry
