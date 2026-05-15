"""Music track generation -- name derivation, slugification, and track listing."""

from __future__ import annotations

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
        """Return a MusicTrack built from the file's stat, or None on OSError."""
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
        size_kb = self.size_bytes // 1024
        date_str = datetime.fromtimestamp(self.modified).strftime("%Y-%m-%d %H:%M")
        return f"{self.name} ({size_kb} KB, {date_str})"

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
        safe_name = self.slugify(name, max_len=60)
        if not safe_name:
            return None
        path = self._output_dir / f"{safe_name}.mp3"
        return path if path.exists() else None

    async def generate(
        self,
        vibe: tuple[str, str],
        style: str,
        track_name: str,
    ) -> tuple[Path, str]:
        """Generate a music track and return (track_path, resolved_track_name).

        Uses explicit vibe/style/track_name parameters rather than reading
        from DaemonContext, keeping TrackGenerator decoupled from daemon state.
        """
        vibe_text, vibe_tags = vibe

        from punt_vox.music import vibe_to_prompt

        hour = time.localtime().tm_hour
        prompt = vibe_to_prompt(
            vibe_text or None, vibe_tags or None, style or None, hour, signals=[]
        )

        self._output_dir.mkdir(parents=True, exist_ok=True)

        resolved_name = track_name or self.auto_track_name(vibe_text, style)
        safe_name = self.slugify(resolved_name, max_len=60)
        filename = f"{safe_name}.mp3"
        output_path = self._output_dir / filename

        from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider

        provider = ElevenLabsMusicProvider()
        await provider.generate_track(prompt, _MUSIC_DURATION_MS, output_path)
        return output_path, safe_name

    def auto_track_name(self, vibe: str, style: str) -> str:
        """Derive a short auto-name from vibe + style + YYYYMMDD-HHMM."""
        stamp = time.strftime("%Y%m%d-%H%M")
        vibe_part = self.slugify(vibe, max_len=20) or "ambient"
        style_part = self.slugify(style, max_len=20) or "mix"
        return f"{vibe_part}-{style_part}-{stamp}"

    def list_tracks(self) -> list[MusicTrack]:
        """Return metadata for all saved .mp3 tracks in the output directory."""
        if not self._output_dir.exists():
            return []
        tracks: list[MusicTrack] = []
        for mp3 in sorted(self._output_dir.glob("*.mp3")):
            track = MusicTrack.from_stat(mp3)
            if track is not None:
                tracks.append(track)
        return tracks

    @staticmethod
    def slugify(text: str, max_len: int = 40) -> str:
        """Slugify a string for use in filenames."""
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
        return slug[:max_len]
