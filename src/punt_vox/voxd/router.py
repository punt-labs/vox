"""WebSocket message routing for voxd."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Self

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from punt_vox import cache as _cache_module
from punt_vox.providers import auto_detect_provider, get_provider
from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.dedup import ChimeDedup, OnceDedup
from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.music_scheduler import MusicScheduler
from punt_vox.voxd.playback import PlaybackItem, PlaybackQueue
from punt_vox.voxd.synthesis import (  # pyright: ignore[reportPrivateUsage]
    _LOCAL_PROVIDERS,
    SynthesisPipeline,
)
from punt_vox.voxd.track_generator import TrackGenerator

__all__ = ["WebSocketRouter"]

logger = logging.getLogger(__name__)


class WebSocketRouter:
    """Route WebSocket messages to handler methods for voxd."""

    __slots__ = (
        "_auth_token",
        "_chime_dedup",
        "_chimes",
        "_client_count",
        "_handlers",
        "_health",
        "_music",
        "_once_dedup",
        "_playback",
        "_synthesis",
        "_track_generator",
    )

    _auth_token: str | None
    _chime_dedup: ChimeDedup
    _chimes: ChimeResolver
    _client_count: int
    _handlers: dict[str, Callable[[dict[str, object], WebSocket], Awaitable[None]]]
    _health: DaemonHealth
    _music: MusicScheduler
    _once_dedup: OnceDedup
    _playback: PlaybackQueue
    _synthesis: SynthesisPipeline
    _track_generator: TrackGenerator

    def __new__(
        cls,
        *,
        synthesis: SynthesisPipeline,
        playback: PlaybackQueue,
        music: MusicScheduler,
        chime_dedup: ChimeDedup,
        once_dedup: OnceDedup,
        chimes: ChimeResolver,
        health: DaemonHealth,
        auth_token: str | None,
        track_generator: TrackGenerator,
    ) -> Self:
        self = super().__new__(cls)
        self._synthesis = synthesis
        self._playback = playback
        self._music = music
        self._chime_dedup = chime_dedup
        self._once_dedup = once_dedup
        self._chimes = chimes
        self._health = health
        self._auth_token = auth_token
        self._track_generator = track_generator
        self._client_count = 0
        self._handlers = {
            "synthesize": self._handle_synthesize,
            "chime": self._handle_chime,
            "record": self._handle_record,
            "voices": self._handle_voices,
            "health": self._handle_health,
            "music_on": self._handle_music_on,
            "music_off": self._handle_music_off,
            "music_play": self._handle_music_play,
            "music_list": self._handle_music_list,
            "music_vibe": self._handle_music_vibe,
            "music_next": self._handle_music_next,
        }
        return self

    # -- Properties ------------------------------------------------------------

    @property
    def client_count(self) -> int:
        """Return the number of connected WebSocket clients."""
        return self._client_count

    @property
    def handlers(
        self,
    ) -> dict[str, Callable[[dict[str, object], WebSocket], Awaitable[None]]]:
        """Return the handler dispatch table."""
        return self._handlers

    # -- Connection handler ----------------------------------------------------

    async def handle_connection(self, websocket: WebSocket) -> None:
        """Main WebSocket route at /ws."""
        if not self._check_auth(websocket):
            await websocket.close(code=1008)
            return

        await websocket.accept()
        self._client_count += 1
        logger.info("Client connected (total: %d)", self._client_count)

        try:
            while True:
                # Preempt Starlette's RuntimeError on a peer-closed socket.
                # After the vox-ehf fix in 4.3.0, chime/unmute clients return
                # on the "playing" ack and close the WebSocket while this
                # loop is still awaiting the next receive_text(). See vox-ewh.
                if websocket.application_state != WebSocketState.CONNECTED:
                    break
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"type": "error", "id": "", "message": "invalid JSON"}
                    )
                    continue

                if not isinstance(msg, dict):
                    await websocket.send_json(
                        {"type": "error", "id": "", "message": "expected JSON object"}
                    )
                    continue

                msg_type = str(msg.get("type", ""))  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                handler = self._handlers.get(msg_type)
                if handler is None:
                    msg_id = str(msg.get("id", ""))  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                    await websocket.send_json(
                        {
                            "type": "error",
                            "id": msg_id,
                            "message": f"unknown message type: {msg_type}",
                        }
                    )
                    continue

                await handler(msg, websocket)  # pyright: ignore[reportUnknownArgumentType]
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("WebSocket error")
        finally:
            self._client_count -= 1
            logger.info("Client disconnected (total: %d)", self._client_count)

    # -- Auth ------------------------------------------------------------------

    def _check_auth(self, websocket: WebSocket) -> bool:
        """Verify the auth token from query param."""
        if self._auth_token is None:
            return True  # No auth configured (tests)
        token = websocket.query_params.get("token", "")
        return hmac.compare_digest(token, self._auth_token)

    # -- Parse helpers ---------------------------------------------------------

    @staticmethod
    def _parse_optional_float(msg: dict[str, object], key: str) -> float | None:
        """Extract an optional float field from a message dict."""
        raw = msg.get(key)
        if raw is None:
            return None
        return float(str(raw))

    @staticmethod
    def _parse_optional_int(msg: dict[str, object], key: str) -> int | None:
        """Extract an optional int field from a message dict."""
        raw = msg.get(key)
        if raw is None:
            return None
        return int(str(raw))

    @staticmethod
    def _parse_optional_str(msg: dict[str, object], key: str) -> str | None:
        """Extract an optional string field, returning None for empty strings."""
        raw = str(msg.get(key, ""))
        return raw or None

    # -- Message handlers ------------------------------------------------------

    async def _handle_synthesize(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'synthesize' message: TTS + enqueue playback."""
        request_id = str(msg.get("id", ""))
        text = str(msg.get("text", ""))
        if not text:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": "empty text"}
            )
            return

        voice = self._parse_optional_str(msg, "voice")
        provider_name = (
            self._parse_optional_str(msg, "provider") or auto_detect_provider()
        )
        model = self._parse_optional_str(msg, "model")
        rate = self._parse_optional_int(msg, "rate")
        language = self._parse_optional_str(msg, "language")
        vibe_tags = self._parse_optional_str(msg, "vibe_tags")
        stability = self._parse_optional_float(msg, "stability")
        similarity = self._parse_optional_float(msg, "similarity")
        style = self._parse_optional_float(msg, "style")
        speaker_boost_raw = msg.get("speaker_boost")
        speaker_boost = (
            bool(speaker_boost_raw) if speaker_boost_raw is not None else None
        )
        api_key = self._parse_optional_str(msg, "api_key")
        once = self._parse_optional_int(msg, "once")

        resolved_voice = voice or ""

        # Opt-in dedup: only when the caller explicitly sets `once` to a
        # positive TTL. With `once` absent, null, or 0, every request plays.
        dedup_recorded = False
        if once is not None and once > 0:
            hit = self._once_dedup.check_and_record(text, float(once))
            if hit is not None:
                logger.info(
                    "Dedup hit: id=%s text=%d chars original_played_at=%.3f "
                    "ttl_remaining=%.1fs",
                    request_id,
                    len(text),
                    hit.original_played_at,
                    hit.ttl_seconds_remaining,
                )
                await websocket.send_json(
                    {
                        "type": "done",
                        "id": request_id,
                        "deduped": True,
                        "original_played_at": hit.original_played_at,
                        "ttl_seconds_remaining": hit.ttl_seconds_remaining,
                    }
                )
                return
            dedup_recorded = True

        def _rollback_dedup() -> None:
            if dedup_recorded:
                self._once_dedup.rollback(text)

        logger.info(
            "Synthesize: id=%s provider=%s voice=%s chars=%d",
            request_id,
            provider_name,
            resolved_voice,
            len(text),
        )

        # Local providers (espeak, say) play directly to the audio device.
        if provider_name in _LOCAL_PROVIDERS:
            direct_result = await self._synthesis.try_direct_play(
                text=text,
                voice=voice,
                provider_name=provider_name,
                model=model,
                language=language,
                rate=rate,
                vibe_tags=vibe_tags,
                stability=stability,
                similarity=similarity,
                style=style,
                speaker_boost=speaker_boost,
                api_key=api_key,
                record_result=self._record_playback_result,
            )
            if isinstance(direct_result, Exception):
                _rollback_dedup()
                await websocket.send_json(
                    {
                        "type": "error",
                        "id": request_id,
                        "message": str(direct_result),
                    }
                )
                return
            if direct_result is not None:
                if direct_result == 0:
                    await websocket.send_json({"type": "done", "id": request_id})
                else:
                    _rollback_dedup()
                    await websocket.send_json(
                        {
                            "type": "error",
                            "id": request_id,
                            "message": f"play_directly failed with rc={direct_result}",
                        }
                    )
                return

        try:
            output_path = await self._synthesis.synthesize_to_file(
                text,
                voice,
                provider_name,
                model,
                language,
                rate,
                vibe_tags,
                stability,
                similarity,
                style,
                speaker_boost=speaker_boost,
                api_key=api_key,
                request_id=request_id,
            )
        except Exception as exc:
            _rollback_dedup()
            logger.exception("Synthesis failed for id=%s", request_id)
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": str(exc)}
            )
            return

        # Enqueue for playback
        done_event = asyncio.Event()
        await self._playback._queue.put(
            PlaybackItem(path=output_path, request_id=request_id, notify=done_event)
        )
        await websocket.send_json({"type": "playing", "id": request_id})
        await done_event.wait()
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_json({"type": "done", "id": request_id})

    async def _handle_record(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'record' message: TTS without playback, return audio bytes."""
        request_id = str(msg.get("id", ""))
        text = str(msg.get("text", ""))
        if not text:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": "empty text"}
            )
            return

        voice = self._parse_optional_str(msg, "voice")
        provider_name = (
            self._parse_optional_str(msg, "provider") or auto_detect_provider()
        )
        model = self._parse_optional_str(msg, "model")
        rate = self._parse_optional_int(msg, "rate")
        language = self._parse_optional_str(msg, "language")
        vibe_tags = self._parse_optional_str(msg, "vibe_tags")
        stability = self._parse_optional_float(msg, "stability")
        similarity = self._parse_optional_float(msg, "similarity")
        style = self._parse_optional_float(msg, "style")
        speaker_boost_raw = msg.get("speaker_boost")
        speaker_boost = (
            bool(speaker_boost_raw) if speaker_boost_raw is not None else None
        )
        api_key = self._parse_optional_str(msg, "api_key")

        logger.info(
            "Record: id=%s provider=%s voice=%s chars=%d",
            request_id,
            provider_name,
            voice or "",
            len(text),
        )

        try:
            output_path = await self._synthesis.synthesize_to_file(
                text,
                voice,
                provider_name,
                model,
                language,
                rate,
                vibe_tags,
                stability,
                similarity,
                style,
                speaker_boost=speaker_boost,
                api_key=api_key,
                request_id=request_id,
            )
        except Exception as exc:
            logger.exception("Record synthesis failed for id=%s", request_id)
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": str(exc)}
            )
            return

        audio_data = output_path.read_bytes()
        is_cache_owned = output_path.is_relative_to(_cache_module.CACHE_DIR)
        if not is_cache_owned:
            output_path.unlink(missing_ok=True)
        encoded = base64.b64encode(audio_data).decode("ascii")
        await websocket.send_json({"type": "audio", "id": request_id, "data": encoded})

    async def _handle_chime(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'chime' message: play a bundled chime sound."""
        signal = str(msg.get("signal", "done"))
        path = self._chimes.resolve(signal)
        if path is None:
            logger.warning("Unknown chime signal: %s", signal)
            await websocket.send_json(
                {"type": "error", "id": "", "message": f"unknown chime: {signal}"}
            )
            return

        if not self._chime_dedup.should_play(signal):
            logger.info("Dedup: skipping duplicate chime %s", signal)
            await websocket.send_json({"type": "done", "id": ""})
            return

        logger.info("Chime: %s", signal)
        done_event = asyncio.Event()
        await self._playback._queue.put(
            PlaybackItem(path=path, request_id=f"chime:{signal}", notify=done_event)
        )
        await websocket.send_json({"type": "playing", "id": f"chime:{signal}"})
        await done_event.wait()
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_json({"type": "done", "id": f"chime:{signal}"})

    async def _handle_voices(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'voices' message: list available voices."""
        provider_name = (
            self._parse_optional_str(msg, "provider") or auto_detect_provider()
        )

        try:
            provider = get_provider(provider_name, config_dir=None)
            voice_list = await asyncio.to_thread(provider.list_voices)
        except Exception as exc:
            logger.exception("Voice listing failed for provider=%s", provider_name)
            await websocket.send_json(
                {
                    "type": "error",
                    "id": "",
                    "message": f"voice listing failed: {exc}",
                }
            )
            return

        await websocket.send_json(
            {"type": "voices", "provider": provider_name, "voices": voice_list}
        )

    async def _handle_health(
        self,
        _msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'health' message over the authenticated WebSocket."""
        payload = self._health.full_payload()
        payload["type"] = "health"
        await websocket.send_json(payload)

    # -- Music handlers --------------------------------------------------------

    async def _handle_music_on(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'music_on' message: start or transfer music ownership."""
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

    async def _handle_music_off(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'music_off' message: stop music playback."""
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

    async def _handle_music_play(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'music_play' message: replay a saved track by name."""
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

    async def _handle_music_list(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'music_list' message: return saved tracks with metadata."""
        request_id = str(msg.get("id", ""))
        tracks = self._track_generator.list_tracks()

        await websocket.send_json(
            {
                "type": "music_list",
                "id": request_id,
                "tracks": tracks,
            }
        )

    async def _handle_music_vibe(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'music_vibe' message: update vibe if sender is owner."""
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

    async def _handle_music_next(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'music_next' message: skip to a new track."""
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

    # -- Private helpers -------------------------------------------------------

    def _record_playback_result(
        self,
        *,
        path: Path,
        rc: int,
        elapsed: float,
        stderr: str,
    ) -> None:
        """Update the playback queue's last_result with a freshly-observed result."""
        self._playback.set_last_result(
            {
                "file": str(path),
                "rc": rc,
                "elapsed_s": round(elapsed, 4),
                "stderr": stderr,
                "ts": time.time(),
            }
        )
