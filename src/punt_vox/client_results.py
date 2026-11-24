"""The results ``synthesize`` and ``record`` build from voxd's terminal frame.

Split out of :mod:`punt_vox.client` so the client module holds the
connection and its RPCs, not the value types its replies decode into;
:mod:`punt_vox.client` re-exports both, so callers keep importing them
from there.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from punt_vox.client_errors import VoxdProtocolError

__all__ = ["RecordResult", "SynthesizeResult"]


@dataclass(frozen=True, slots=True)
class RecordResult:
    """Locator for a recording the daemon stored -- never a client path.

    ``record`` captures to the daemon-owned store; the client never names a
    daemon path. ``id`` and ``name`` are the store reference (the store
    filename) a caller passes to :meth:`VoxClient.play` or
    :meth:`VoxClient.fetch`. ``store_path`` is that reference expressed
    relative to the daemon's data root (``recordings/foo.mp3``) -- a logical
    locator with no absolute prefix, never a path on the client's own disk.
    ``byte_count`` is the size the daemon wrote; ``cached`` reports a cache hit.
    """

    id: str
    name: str
    store_path: Path
    byte_count: int
    cached: bool = False

    @classmethod
    def from_audio(cls, terminal: dict[str, Any]) -> Self:  # a decoded frame
        """Build the locator from ``record``'s terminal ``audio`` frame.

        Raises ``VoxdProtocolError`` for any other frame, a missing field, or
        a non-integer byte count -- never a raw ``ValueError`` a CLI or tool
        catch would miss.

        Raises:
            VoxdProtocolError: If the response is not a complete, valid audio result.
        """
        if terminal.get("type") != "audio":
            raise VoxdProtocolError(
                f"Expected 'audio' response, got '{terminal.get('type')}'"
            )
        for field in ("name", "path", "bytes"):
            if field not in terminal:
                raise VoxdProtocolError(f"'audio' response missing {field!r}")
        try:
            byte_count = int(terminal["bytes"])
        except (TypeError, ValueError) as exc:
            raise VoxdProtocolError(
                f"'audio' response has non-integer 'bytes': {terminal['bytes']!r}"
            ) from exc
        store_name = str(terminal["name"])
        return cls(
            id=store_name,
            name=store_name,
            store_path=Path(str(terminal["path"])),
            byte_count=byte_count,
            cached=bool(terminal.get("cached", False)),
        )


@dataclass(frozen=True, slots=True)
class SynthesizeResult:
    """Result of a ``synthesize`` call on voxd.

    ``deduped`` is True when the caller passed an ``once=<ttl>`` window and
    the server found an identical text already played within it -- the
    audio was NOT re-played; treat it as success, not an error. Non-deduped
    results leave ``original_played_at`` and ``ttl_seconds_remaining`` None.

    ``cached`` reports whether voxd served the audio from its
    content-addressed cache (True) or synthesized it fresh (False) -- the
    observability signal a caller asserts to confirm a cache hit. A dedup
    short-circuit leaves it False since no synthesis path ran.
    """

    request_id: str
    deduped: bool = False
    original_played_at: float | None = None
    ttl_seconds_remaining: float | None = None
    cached: bool = False

    @classmethod
    def from_terminal(cls, request_id: str, terminal: dict[str, Any]) -> Self:
        """Build the result from ``synthesize``'s last frame (empty: none came).

        Raises:
            ValueError: If a deduplication timestamp is not numeric.
            TypeError: If a deduplication timestamp is null or cannot become seconds.
        """
        if not terminal.get("deduped"):
            return cls(request_id, cached=bool(terminal.get("cached", False)))
        return cls(
            request_id,
            deduped=True,
            original_played_at=cls._seconds(terminal, "original_played_at"),
            ttl_seconds_remaining=cls._seconds(terminal, "ttl_seconds_remaining"),
        )

    @staticmethod
    def _seconds(terminal: dict[str, Any], key: str) -> float | None:
        """Return *key* as seconds; ``None`` when the dedup frame omits it."""
        return float(terminal[key]) if key in terminal else None
