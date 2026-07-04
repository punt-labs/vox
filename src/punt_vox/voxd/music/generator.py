"""Music track generation -- name derivation, slugification, and track listing."""

from __future__ import annotations

import itertools
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Self

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
    def from_stat(cls, mp3: Path) -> MusicTrack | None:
        """Return a MusicTrack from the file's stat, or None if the file disappeared."""
        try:
            stat = mp3.stat()
        except FileNotFoundError:
            logger.debug("Track disappeared during listing, skipping: %s", mp3)
            return None
        return cls(
            name=mp3.stem,
            path=mp3,
            size_bytes=stat.st_size,
            modified=stat.st_mtime,
        )

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> MusicTrack:
        """Construct a MusicTrack from a wire-format dict."""
        path_str = str(d.get("path", ""))
        if not path_str:
            msg = "MusicTrack.from_dict: missing required 'path' field"
            raise ValueError(msg)
        try:
            size_bytes = int(str(d.get("size_bytes", 0)))
        except (ValueError, TypeError):
            size_bytes = 0
        try:
            modified = float(str(d.get("modified", 0)))
        except (ValueError, TypeError):
            modified = 0.0
        return cls(
            name=str(d.get("name", "")),
            path=Path(path_str),
            size_bytes=size_bytes,
            modified=modified,
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
    """Generate, name, and list music tracks in an output directory."""

    __slots__ = ("_output_dir",)

    _output_dir: Path

    def __new__(cls, output_dir: Path) -> Self:
        self = super().__new__(cls)
        self._output_dir = output_dir
        return self

    @property
    def output_dir(self) -> Path:
        """Return the output directory for generated tracks."""
        return self._output_dir

    def find_track(self, name: str) -> Path | None:
        """Return the path to an existing track by name, or None."""
        if not (safe_name := self.slugify(name, max_len=60)):
            return None
        path = self._output_dir / f"{safe_name}.mp3"
        return path if path.exists() else None

    def tracks_for(self, key: tuple[str, str]) -> list[Path]:
        """Return saved track paths sharing the (vibe, style) pool prefix."""
        return sorted(self._output_dir.glob(f"{self.pool_prefix(key)}*.mp3"))

    async def generate(
        self,
        vibe: tuple[str, str],
        style: str,
        track_name: str,
    ) -> tuple[Path, str]:
        """Generate a track and return (track_path, resolved_track_name)."""
        vibe_text, vibe_tags = vibe

        from punt_vox.music import vibe_to_prompt

        hour = time.localtime().tm_hour
        variation = len(self.tracks_for((vibe_text, style)))  # 0-based pool size
        prompt = vibe_to_prompt(
            vibe_text or None, vibe_tags or None, style or None, hour, [], variation
        )

        self._output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self.slugify(
            track_name or self.auto_track_name(vibe_text, style), 60
        )
        output_path = self._output_dir / f"{safe_name}.mp3"

        from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider

        await ElevenLabsMusicProvider().generate_track(
            prompt, _MUSIC_DURATION_MS, output_path
        )
        return output_path, safe_name

    def auto_track_name(self, vibe: str, style: str) -> str:
        """Return a collision-free auto-name at the lowest free counter."""
        stem = f"{self.pool_prefix((vibe, style))}{time.strftime('%Y%m%d_%H%M')}"
        counters = (f"{stem}_{n}" for n in itertools.count())
        return next(c for c in counters if not (self._output_dir / f"{c}.mp3").exists())

    @staticmethod
    def pool_prefix(key: tuple[str, str]) -> str:
        """Return the filename prefix shared by one (vibe, style) pool."""
        vibe_part = TrackGenerator.slugify(key[0], max_len=20) or "ambient"
        style_part = TrackGenerator.slugify(key[1], max_len=20) or "mix"
        return f"{vibe_part}_{style_part}_"

    def list_tracks(self) -> list[MusicTrack]:
        """Return metadata for all saved .mp3 tracks in the output directory."""
        mp3s = sorted(self._output_dir.glob("*.mp3"))
        return [t for mp3 in mp3s if (t := MusicTrack.from_stat(mp3)) is not None]

    @staticmethod
    def slugify(text: str, max_len: int = 40) -> str:
        """Slugify a string for use in filenames."""
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
        return slug[:max_len]
