"""Music track generation -- name derivation, slugification, and track listing."""

from __future__ import annotations

import itertools
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from punt_vox.voxd.music.prompts import PromptSet
    from punt_vox.voxd.music.store import TrackStore

__all__ = ["MusicTrack", "TrackGenerator"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MusicTrack:
    """Saved music track with display metadata."""

    name: str
    path: Path
    size_bytes: int
    modified: float  # Unix timestamp (st_mtime)

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> MusicTrack:
        """Construct a MusicTrack from a wire-format dict."""
        path_str = str(d.get("path", ""))
        if not path_str:
            msg = "MusicTrack.from_dict: missing required 'path' field"
            raise ValueError(msg)
        return cls(
            name=str(d.get("name", "")),
            path=Path(path_str),
            size_bytes=int(str(d.get("size_bytes", 0))),
            modified=float(str(d.get("modified", 0))),
        )

    def display_line(self) -> str:
        """Return a human-readable summary line for this track."""
        date_str = datetime.fromtimestamp(self.modified).strftime("%Y-%m-%d %H:%M")
        return f"{self.name} ({self.size_bytes // 1024} KB, {date_str})"

    def to_dict(self) -> dict[str, object]:
        """Serialize for WebSocket wire format (backward compat)."""
        return {
            "name": self.name,
            "size_bytes": self.size_bytes,
            "modified": self.modified,
            "path": str(self.path),
        }


_MUSIC_DURATION_MS = 120_000


class TrackGenerator:
    """Generate, name, and list music tracks through an injected store.

    Disk access is delegated to a :class:`~punt_vox.voxd.music.store.TrackStore`
    (Amendment A): this class holds no ``pathlib`` logic of its own.
    """

    __slots__ = ("_store",)

    _store: TrackStore

    def __new__(cls, store: TrackStore) -> Self:
        self = super().__new__(cls)
        self._store = store
        return self

    @staticmethod
    def can_generate() -> bool:
        """Return whether generation is possible (an ElevenLabs key is set).

        Generation is hard-wired to ElevenLabs (see :meth:`generate`), so a
        usable ``ELEVENLABS_API_KEY`` is the precondition for producing tracks.
        """
        return bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())

    def find_track(self, name: str) -> Path | None:
        """Return the path to an existing track by name, or None."""
        if not (safe_name := self.slugify(name, max_len=60)):
            return None
        if not self._store.exists(safe_name):
            return None
        return self._store.path_for(safe_name)

    def tracks_for(self, prefix: str) -> tuple[Path, ...]:
        """Return saved track paths sharing a pool prefix."""
        return self._store.tracks_for(prefix)

    async def generate(
        self,
        vibe: tuple[str, str],
        style: str,
        track_name: str,
        prompts: PromptSet,
    ) -> tuple[Path, str]:
        """Generate a track and return (track_path, resolved_track_name).

        The final prompt text comes entirely from ``prompts`` -- the agent's
        base plus the variation for this pool slot (``prompts.prompt_for(index)``)
        or a minimal literal fallback. vox never composes a genre description of
        its own; ``index`` is the count of tracks already in the pool, so track 0
        draws variation 0, track 1 variation 1, and so on.
        """
        vibe_text, _ = vibe
        prefix = self.pool_prefix((vibe_text, style))
        index = len(self._store.tracks_for(prefix))
        prompt = prompts.prompt_for(index)

        self._store.prepare()
        safe_name = self.slugify(track_name or self.auto_track_name(prefix), 60)
        output_path = self._store.path_for(safe_name)

        from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider

        await ElevenLabsMusicProvider().generate_track(
            prompt, _MUSIC_DURATION_MS, output_path
        )
        return output_path, safe_name

    def auto_track_name(self, pool_prefix: str) -> str:
        """Return a collision-free auto-name within ``pool_prefix``."""
        stem = f"{pool_prefix}{time.strftime('%Y%m%d_%H%M')}"
        counters = (f"{stem}_{n}" for n in itertools.count())
        return next(c for c in counters if not self._store.exists(c))

    @staticmethod
    def pool_prefix(key: tuple[str, str]) -> str:
        """Return the filename prefix shared by one (vibe, style) pool."""
        vibe_part = TrackGenerator.slugify(key[0], max_len=20) or "ambient"
        style_part = TrackGenerator.slugify(key[1], max_len=20) or "mix"
        return f"{vibe_part}_{style_part}_"

    @staticmethod
    def pool_prefix_of(track: Path) -> str:
        """Return the pool prefix a generated track belongs to, from its name."""
        return re.sub(r"\d{8}_\d{4}_\d+$", "", track.stem) or track.stem

    def list_tracks(self) -> list[MusicTrack]:
        """Return metadata for all saved .mp3 tracks in the store."""
        return [
            MusicTrack(
                name=meta.path.stem,
                path=meta.path,
                size_bytes=meta.size_bytes,
                modified=meta.modified,
            )
            for meta in self._store.listing()
        ]

    @staticmethod
    def slugify(text: str, max_len: int = 40) -> str:
        """Slugify a string for use in filenames."""
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
        return slug[:max_len]
