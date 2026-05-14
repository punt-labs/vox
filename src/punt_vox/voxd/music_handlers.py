"""Music playback WebSocket handlers."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

from punt_vox.voxd.music_scheduler import MusicScheduler
from punt_vox.voxd.track_generator import TrackGenerator
from punt_vox.voxd.types import MessageHandler

__all__ = [
    "MusicListHandler",
    "MusicNextHandler",
    "MusicOffHandler",
    "MusicOnHandler",
    "MusicPlayHandler",
    "MusicVibeHandler",
]

logger = logging.getLogger(__name__)


class MusicOnHandler(MessageHandler):
    """Handle 'music_on' messages: start or transfer music ownership."""

    __slots__ = (
        "_music",
        "_track_generator",
    )

    _music: MusicScheduler
    _track_generator: TrackGenerator

    def __new__(
        cls,
        *,
        music: MusicScheduler,
        track_generator: TrackGenerator,
    ) -> Self:
        self = super().__new__(cls)
        self._music = music
        self._track_generator = track_generator
        return self

    async def __call__(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Start or transfer music ownership."""
        request_id = str(msg.get("id", ""))
        owner_id = str(msg.get("owner_id", ""))
        style = str(msg.get("style", ""))
        vibe = str(msg.get("vibe", ""))
        vibe_tags = str(msg.get("vibe_tags", ""))
        name = str(msg.get("name", ""))

        if not owner_id:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": "owner_id is required"}
            )
            return

        # Check for existing track by name -- skip generation if found.
        if name:
            safe_name = TrackGenerator.slugify(name, max_len=60)
            if not safe_name:
                await websocket.send_json(
                    {"type": "error", "id": request_id, "message": "invalid track name"}
                )
                return
            existing_path = self._track_generator.output_dir / f"{safe_name}.mp3"
            if existing_path.exists():
                await self._music.kill_proc()
                self._music.mode = "on"
                if style:
                    self._music.style = style
                self._music.owner = owner_id
                self._music.vibe = (vibe, vibe_tags)
                self._music.track = existing_path
                self._music.track_name = safe_name
                self._music.state = "playing"
                self._music.replay = True
                self._music.changed.set()

                logger.info(
                    "Music on (replay): owner=%s name=%s track=%s",
                    owner_id,
                    safe_name,
                    existing_path,
                )
                await websocket.send_json(
                    {
                        "type": "music_on",
                        "id": request_id,
                        "status": "playing",
                        "track": str(existing_path),
                        "name": safe_name,
                    }
                )
                return

        # When music is already playing for a different owner, kill existing
        # playback so the new owner starts fresh.
        is_already_playing = self._music.mode == "on" and self._music.proc is not None
        if not is_already_playing or self._music.owner != owner_id:
            await self._music.kill_proc()

        self._music.mode = "on"
        if style:
            self._music.style = style
        self._music.owner = owner_id
        self._music.vibe = (vibe, vibe_tags)
        self._music.track_name = (
            TrackGenerator.slugify(name, max_len=60) if name else ""
        )
        self._music.replay = False
        self._music.state = "generating"
        self._music.changed.set()

        logger.info(
            "Music on: owner=%s style=%s vibe=%s name=%s",
            owner_id,
            self._music.style,
            vibe,
            self._music.track_name,
        )
        await websocket.send_json(
            {"type": "music_on", "id": request_id, "status": "generating"}
        )


class MusicOffHandler(MessageHandler):
    """Handle 'music_off' messages: stop music playback."""

    __slots__ = ("_music",)

    _music: MusicScheduler

    def __new__(
        cls,
        *,
        music: MusicScheduler,
    ) -> Self:
        self = super().__new__(cls)
        self._music = music
        return self

    async def __call__(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Stop music playback."""
        request_id = str(msg.get("id", ""))

        await self._music.kill_proc()
        self._music.mode = "off"
        self._music.state = "idle"
        self._music.replay = False
        self._music.changed.set()

        logger.info("Music off")
        await websocket.send_json(
            {"type": "music_off", "id": request_id, "status": "stopped"}
        )


class MusicPlayHandler(MessageHandler):
    """Handle 'music_play' messages: replay a saved track by name."""

    __slots__ = (
        "_music",
        "_track_generator",
    )

    _music: MusicScheduler
    _track_generator: TrackGenerator

    def __new__(
        cls,
        *,
        music: MusicScheduler,
        track_generator: TrackGenerator,
    ) -> Self:
        self = super().__new__(cls)
        self._music = music
        self._track_generator = track_generator
        return self

    async def __call__(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Replay a saved track by name."""
        request_id = str(msg.get("id", ""))
        name = str(msg.get("name", ""))
        owner_id = str(msg.get("owner_id", ""))

        if not name:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": "name is required"}
            )
            return

        if not owner_id:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": "owner_id is required"}
            )
            return

        safe_name = TrackGenerator.slugify(name, max_len=60)
        if not safe_name:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": "invalid track name"}
            )
            return
        track_path = self._track_generator.output_dir / f"{safe_name}.mp3"

        if not track_path.exists():
            await websocket.send_json(
                {
                    "type": "error",
                    "id": request_id,
                    "message": f"track not found: {safe_name}",
                }
            )
            return

        # Kill current playback, set up replay.
        await self._music.kill_proc()
        self._music.mode = "on"
        self._music.owner = owner_id
        self._music.track = track_path
        self._music.track_name = safe_name
        self._music.state = "playing"
        self._music.replay = True
        self._music.changed.set()

        logger.info(
            "Music play: owner=%s name=%s track=%s",
            owner_id,
            safe_name,
            track_path,
        )
        await websocket.send_json(
            {
                "type": "music_play",
                "id": request_id,
                "status": "playing",
                "track": str(track_path),
                "name": safe_name,
            }
        )


class MusicListHandler(MessageHandler):
    """Handle 'music_list' messages: return saved tracks with metadata."""

    __slots__ = ("_track_generator",)

    _track_generator: TrackGenerator

    def __new__(
        cls,
        *,
        track_generator: TrackGenerator,
    ) -> Self:
        self = super().__new__(cls)
        self._track_generator = track_generator
        return self

    async def __call__(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Return saved tracks with metadata."""
        request_id = str(msg.get("id", ""))
        tracks = self._track_generator.list_tracks()

        await websocket.send_json(
            {
                "type": "music_list",
                "id": request_id,
                "tracks": tracks,
            }
        )


class MusicVibeHandler(MessageHandler):
    """Handle 'music_vibe' messages: update vibe if sender is owner."""

    __slots__ = ("_music",)

    _music: MusicScheduler

    def __new__(
        cls,
        *,
        music: MusicScheduler,
    ) -> Self:
        self = super().__new__(cls)
        self._music = music
        return self

    async def __call__(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Update vibe if sender is owner."""
        request_id = str(msg.get("id", ""))
        owner_id = str(msg.get("owner_id", ""))
        vibe = str(msg.get("vibe", ""))
        vibe_tags = str(msg.get("vibe_tags", ""))

        if not owner_id:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": "owner_id is required"}
            )
            return

        if owner_id != self._music.owner:
            await websocket.send_json(
                {"type": "music_vibe", "id": request_id, "status": "ignored"}
            )
            return

        new_vibe = (vibe, vibe_tags)
        if new_vibe == self._music.vibe:
            await websocket.send_json(
                {"type": "music_vibe", "id": request_id, "status": "ignored"}
            )
            return

        self._music.vibe = new_vibe
        self._music.changed.set()

        logger.info("Music vibe changed: vibe=%s tags=%s", vibe, vibe_tags)
        await websocket.send_json(
            {"type": "music_vibe", "id": request_id, "status": "generating"}
        )


class MusicNextHandler(MessageHandler):
    """Handle 'music_next' messages: skip to a new track."""

    __slots__ = ("_music",)

    _music: MusicScheduler

    def __new__(
        cls,
        *,
        music: MusicScheduler,
    ) -> Self:
        self = super().__new__(cls)
        self._music = music
        return self

    async def __call__(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Skip to a new track."""
        request_id = str(msg.get("id", ""))
        owner_id = str(msg.get("owner_id", ""))

        if not owner_id:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": "owner_id is required"}
            )
            return

        if self._music.mode != "on":
            await websocket.send_json(
                {"type": "music_next", "id": request_id, "status": "ignored"}
            )
            return

        self._music.track_name = ""
        self._music.replay = False
        self._music.changed.set()

        logger.info("Music next: owner=%s", owner_id)
        await websocket.send_json(
            {"type": "music_next", "id": request_id, "status": "generating"}
        )
