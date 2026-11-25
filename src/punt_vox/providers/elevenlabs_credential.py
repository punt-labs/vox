"""Credentials captured when an ElevenLabs client is constructed."""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING, Self, final

from punt_vox.providers.api_key import ApiKeyCheck

if TYPE_CHECKING:
    from punt_vox.types import HealthCheck

_KEY_CHECK = ApiKeyCheck.for_provider("elevenlabs")


@final
class ElevenLabsCredential:
    """Use the same key for SDK construction, health, and catalog identity."""

    __slots__ = ("_injected_sdk", "_key")
    _key: str | None
    _injected_sdk: bool

    def __new__(cls, api_key: str | None, *, injected_sdk: bool) -> Self:
        """Capture the explicit key or the environment key in effect now."""
        self = super().__new__(cls)
        self._key = (
            api_key if api_key is not None else os.environ.get(_KEY_CHECK.source)
        )
        self._injected_sdk = injected_sdk
        return self

    @property
    def catalog_scope(self) -> bytes | None:
        """Identify the key without putting it in the catalog index."""
        if self._injected_sdk or not self._key:
            return None
        return hashlib.sha256(self._key.encode("utf-8", "surrogatepass")).digest()

    def require_key(self) -> str:
        """Return the captured key validated for use in a request header."""
        return _KEY_CHECK.require_set(self._key, "elevenlabs")

    def key_check(self) -> HealthCheck:
        """Report whether the captured key is set and can be sent."""
        return _KEY_CHECK.report("ElevenLabs", self._key)
