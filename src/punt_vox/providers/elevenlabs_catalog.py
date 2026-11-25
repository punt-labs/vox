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
from punt_vox.types_synthesis import SynthesisSpec

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
    # An evicted entry can still coordinate lookups until its callers release it.
    _live: ClassVar[WeakValueDictionary[bytes, ElevenLabsCatalogEntry]] = (
        WeakValueDictionary()
    )

    def __new__(cls, client: ElevenLabsClient) -> Self:
        """Capture the credential scope or keep an isolated resolver."""
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
        if SynthesisSpec.ELEVENLABS_VOICE_ID.fullmatch(name):
            return name, name
        return name.lower(), self._catalog().lookup(
            self._client, lambda voices: voices.resolve(name)
        )

    def short_names(self) -> list[str]:
        """Refresh the shared catalog for explicit voice listings."""
        if isinstance(scope := self._scope, ElevenLabsCatalogEntry):
            names = scope.lookup(self._client, ElevenLabsVoices.list_all)
        else:
            names = self._catalog().lookup(self._client, ElevenLabsVoices.refresh_list)
        return [name for name in names if DESCRIPTION_SEPARATOR not in name]

    def _catalog(self) -> ElevenLabsCatalogEntry:
        if isinstance(scope := self._scope, ElevenLabsCatalogEntry):
            return scope

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
