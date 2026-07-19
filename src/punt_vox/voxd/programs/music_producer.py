"""The playlist Producer: ElevenLabs music generation with varied track lengths.

``MusicProducer`` is the playlist ``Producer``. It wraps an injected
``MusicProvider`` (the existing ``ElevenLabsMusicProvider`` in production, a fake
in tests) and maps the provider's failures onto the two ``Producer`` error
types. It also varies track length: instead of the hard-wired 120s, a
``LengthPolicy`` samples a realistic, slightly-randomized length per Part.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Final, Self, final

from elevenlabs.core import ApiError  # pyright: ignore[reportMissingTypeStubs]

from punt_vox.voxd.programs.part import Part
from punt_vox.voxd.programs.producer import (
    PartSpec,
    ProducerBadInputError,
    ProducerTransientError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.types import MusicProvider

__all__ = ["LengthPolicy", "MusicProducer"]

_PLAYLIST_MIN_MS: Final = 90_000
_PLAYLIST_MAX_MS: Final = 210_000

# HTTP statuses that mean "retrying will not help" (bad prompt, ToS, auth).
_PERMANENT_STATUS: Final = frozenset({400, 401, 403, 404, 422})


@final
class LengthPolicy:
    """Sample a realistic, slightly-randomized Part length in milliseconds.

    Draws a uniform length in the closed range ``[min_ms, max_ms]``; the
    playlist default is 90--210s. Replaces the hard-wired 120s so a pool of
    tracks varies in length rather than being uniform.
    """

    __slots__ = ("_max_ms", "_min_ms")
    _min_ms: int
    _max_ms: int

    def __new__(
        cls, *, min_ms: int = _PLAYLIST_MIN_MS, max_ms: int = _PLAYLIST_MAX_MS
    ) -> Self:
        if min_ms < 1:
            msg = f"min_ms must be >= 1, got {min_ms}"
            raise ValueError(msg)
        if max_ms < min_ms:
            msg = f"max_ms {max_ms} must be >= min_ms {min_ms}"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._min_ms = min_ms
        self._max_ms = max_ms
        return self

    @property
    def min_ms(self) -> int:
        """Return the lower bound of the sampled length."""
        return self._min_ms

    @property
    def max_ms(self) -> int:
        """Return the upper bound of the sampled length."""
        return self._max_ms

    def sample(self) -> int:
        """Return a fresh length in ``[min_ms, max_ms]`` milliseconds."""
        return self._min_ms + secrets.randbelow(self._max_ms - self._min_ms + 1)


@final
class MusicProducer:
    """Produce playlist Parts by wrapping a ``MusicProvider`` (ElevenLabs)."""

    __slots__ = ("_length", "_provider")
    _provider: MusicProvider
    _length: LengthPolicy

    def __new__(cls, provider: MusicProvider, length: LengthPolicy) -> Self:
        self = super().__new__(cls)
        self._provider = provider
        self._length = length
        return self

    async def produce(self, spec: PartSpec, target: Path) -> Part:
        """Generate ``spec`` into ``target``, mapping failures to Producer errors."""
        duration_ms = self._length.sample()
        try:
            await self._provider.generate_track(spec.prompt, duration_ms, target)
        except ApiError as exc:
            raise self._classify(exc) from exc
        except (TimeoutError, ConnectionError, OSError) as exc:
            raise ProducerTransientError(str(exc)) from exc
        spec.tags.write_to(target)
        return Part(target.name, spec.index)

    @staticmethod
    def _classify(exc: ApiError) -> ProducerBadInputError | ProducerTransientError:
        """Route an ElevenLabs ``ApiError`` to the right Producer error type.

        ``ApiError`` ships from an untyped SDK path, so ``status_code`` is read
        defensively (PY-TS-9 exception): a permanent HTTP status becomes
        ``ProducerBadInputError``; everything else (429, 5xx, unknown) is transient.
        """
        status: object = getattr(exc, "status_code", None)
        if isinstance(status, int) and status in _PERMANENT_STATUS:
            return ProducerBadInputError(str(exc))
        return ProducerTransientError(str(exc))
