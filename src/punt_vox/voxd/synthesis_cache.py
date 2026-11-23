"""Anonymous synthesis cache lookup with per-call billing isolation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Self

from punt_vox.cache import CacheKey, cache_get, cache_put
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.synthesis_result import SynthesisOutcome

logger = logging.getLogger(__name__)


class SynthesisCache:
    """Bind a request's cache identity and billing scope for lookup and storage."""

    __slots__ = ("_anonymous", "_chars_in", "_key")

    _anonymous: bool
    _chars_in: int
    _key: CacheKey

    def __new__(cls, key: CacheKey, spec: SynthesisSpec, chars_in: int) -> Self:
        """Create a lookup without reading the cache or retaining credentials."""
        self = super().__new__(cls)
        self._key = key
        self._anonymous = spec.api_key is None
        self._chars_in = chars_in
        return self

    @classmethod
    def for_request(cls, normalized: str, spec: SynthesisSpec, chars_in: int) -> Self:
        """Build the anonymous identity from all audio-affecting settings."""
        key = CacheKey(
            normalized,
            spec.voice or "",
            spec.provider or "",
            rate=spec.rate,
            model=spec.model,
            language=spec.language,
            stability=spec.stability,
            similarity=spec.similarity,
            style=spec.style,
            speaker_boost=spec.speaker_boost,
        )
        return cls(key, spec, chars_in)

    def store(self, source: Path) -> Path | None:
        """Store anonymous audio, never audio from a credential override."""
        return cache_put(self._key, source) if self._anonymous else None

    async def outcome(self) -> SynthesisOutcome | None:
        """Return reusable audio, or None for a miss or credential override."""
        if not self._anonymous:
            return None
        cached = cache_get(self._key)
        if (
            cached is None
            or (outcome := await SynthesisOutcome.from_cache(cached)) is None
        ):
            return None
        logger.info(
            "served %d chars from cache",
            self._chars_in,
        )
        return outcome
