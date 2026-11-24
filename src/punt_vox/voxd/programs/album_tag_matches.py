"""The albums that match one catalog tag query, in playback order."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.selection import Selection
from punt_vox.voxd.programs.store_errors import AlbumNotFoundError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from punt_vox.voxd.programs.album_tags import PromptFingerprint, TagQuery
    from punt_vox.voxd.programs.catalog import Album
    from punt_vox.voxd.programs.part import Part

__all__ = ["AlbumTagMatches"]

logger = logging.getLogger(__name__)


@final
class AlbumTagMatches:
    """A newest-first set of tag matches whose parts are still read live."""

    __slots__ = ("_albums",)
    _albums: tuple[Album, ...]

    def __new__(cls, albums: Iterable[Album], query: TagQuery) -> Self:
        """Match by tags and rank by creation time, then by ID."""
        self = super().__new__(cls)
        matches = (album for album in albums if query.matches(album.manifest.tags))
        self._albums = tuple(sorted(matches, key=cls._recency, reverse=True))
        return self

    @property
    def albums(self) -> tuple[Album, ...]:
        """Return the matching albums, newest first."""
        return self._albums

    def newest(self) -> Album | None:
        """Return the newest matching album, if any."""
        return self._albums[0] if self._albums else None

    def resume(self, fingerprint: PromptFingerprint) -> Album | None:
        """Return the newest match with the same prompt fingerprint, if any."""
        for album in self._albums:
            if album.manifest.prompt_fingerprint == fingerprint:
                return album
        return None

    def select(self) -> Selection:
        """Collect live ready parts newest-first, excluding missing albums."""
        return Selection.from_albums(
            (album.locator, self._radio_parts(album)) for album in self._albums
        )

    @staticmethod
    def _radio_parts(album: Album) -> tuple[Part, ...]:
        """Read ready parts, treating a missing album as an empty contributor."""
        try:
            return album.ready_parts()
        except (AlbumNotFoundError, FileNotFoundError) as exc:
            logger.warning("skipping missing album %s: %s", album.id.value, exc)
            return ()

    @staticmethod
    def _recency(album: Album) -> tuple[datetime, str]:
        """Rank by creation time, then by ID for deterministic ties."""
        return (album.manifest.created, album.id.value)
