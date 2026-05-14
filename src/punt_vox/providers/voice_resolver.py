"""Generic voice name resolution with caching and TTL."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Self

from punt_vox.types import VoiceNotFoundError

__all__ = ["VoiceResolver"]

logger = logging.getLogger(__name__)


class VoiceResolver[V]:
    """Voice name resolution with caching, TTL, and optional fallback."""

    __slots__ = (
        "_cache",
        "_cooldown_seconds",
        "_default_key",
        "_force_refreshed_at",
        "_loaded_at",
        "_loader",
        "_ttl_seconds",
    )

    _cache: dict[str, V]
    _cooldown_seconds: int
    _default_key: str
    _force_refreshed_at: float
    _loaded_at: float
    _loader: Callable[[], dict[str, V]]
    _ttl_seconds: int

    def __new__(
        cls,
        loader: Callable[[], dict[str, V]],
        *,
        default_key: str,
        ttl_seconds: int = 0,
        cooldown_seconds: int = 60,
    ) -> Self:
        self = super().__new__(cls)
        self._loader = loader
        self._default_key = default_key
        self._ttl_seconds = ttl_seconds
        self._cooldown_seconds = cooldown_seconds
        self._cache = {}
        self._loaded_at = 0.0
        self._force_refreshed_at = 0.0
        return self

    def resolve(self, name: str, *, strict: bool = True) -> V:
        """Resolve a voice name to its value.

        strict=True (default): raise VoiceNotFoundError on miss.
        strict=False: log a warning, return the default voice.
        """
        key = name.lower()

        # Try cache first
        self._ensure_loaded()
        if key in self._cache:
            return self._cache[key]

        # Cache miss -- force refresh (rate-limited)
        self._force_load()
        if key in self._cache:
            return self._cache[key]

        # Not found
        if not strict:
            logger.warning(
                "Voice '%s' not found; falling back to default '%s'",
                name,
                self._default_key,
            )
            self._ensure_loaded()
            if self._default_key in self._cache:
                return self._cache[self._default_key]

        raise VoiceNotFoundError(name, sorted(self._cache))

    def list_all(self) -> list[str]:
        """Return sorted voice names."""
        self._ensure_loaded()
        return sorted(self._cache)

    @property
    def default_key(self) -> str:
        """Return the default voice key."""
        return self._default_key

    # -- Private ---------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load voices if cache is empty or stale."""
        if self._cache and not self._is_stale():
            return
        self._do_load()

    def _force_load(self) -> None:
        """Force a reload, rate-limited by cooldown."""
        now = time.monotonic()
        if (
            self._force_refreshed_at > 0.0
            and (now - self._force_refreshed_at) < self._cooldown_seconds
        ):
            return
        self._do_load()
        self._force_refreshed_at = now

    def _do_load(self) -> None:
        """Call the loader and replace the cache."""
        fresh = self._loader()
        self._cache.clear()
        self._cache.update(fresh)
        self._loaded_at = time.monotonic()

    def _is_stale(self) -> bool:
        """Check if the cache has exceeded its TTL."""
        if self._ttl_seconds <= 0:
            return False  # TTL=0 means load-once, never stale
        return (time.monotonic() - self._loaded_at) >= self._ttl_seconds
