"""Program formats and their fixed capacities (Z ``Format``, ``poolSize``)."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = ["MAX_RETRY", "Format"]

MAX_RETRY: Final = 5
"""Transient-retry cap before an empty-pool Program gives up (Z ``maxRetry``)."""


class Format(StrEnum):
    """One of the three Program formats (Z free type ``Format``).

    Only ``PLAYLIST`` is realised today; ``PODCAST`` and ``AUDIOBOOK`` are
    named so that ``pool_size`` and the operations branching on it are total
    from the start. The string values are the wire/JSON form.
    """

    PLAYLIST = "playlist"
    PODCAST = "podcast"
    AUDIOBOOK = "audiobook"

    @property
    def pool_size(self) -> int:
        """Return the full-pool Part count for this format (Z ``poolSize``)."""
        return _POOL_SIZE[self]

    @property
    def label(self) -> str:
        """Return the human surface label -- ``playlist`` renders as ``music``."""
        return _LABEL[self]


_POOL_SIZE: Final[dict[Format, int]] = {
    Format.PLAYLIST: 12,
    Format.PODCAST: 6,
    Format.AUDIOBOOK: 6,
}

_LABEL: Final[dict[Format, str]] = {
    Format.PLAYLIST: "music",
    Format.PODCAST: "podcast",
    Format.AUDIOBOOK: "audiobook",
}
