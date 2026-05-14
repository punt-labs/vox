"""Speech synthesis and recording WebSocket handlers."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import time
from pathlib import Path
from typing import Self

from starlette.websockets import WebSocket, WebSocketDisconnect

from punt_vox import cache as _cache_module
from punt_vox.providers import auto_detect_provider
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd._parse import (
    parse_optional_float,
    parse_optional_int,
    parse_optional_str,
)
from punt_vox.voxd.dedup import OnceDedup
from punt_vox.voxd.playback import PlaybackItem, PlaybackQueue
from punt_vox.voxd.synthesis import (  # pyright: ignore[reportPrivateUsage]
    _LOCAL_PROVIDERS,
    SynthesisPipeline,
)
from punt_vox.voxd.types import MessageHandler

__all__ = ["RecordHandler", "SynthesizeHandler"]

logger = logging.getLogger(__name__)


class SynthesizeHandler(MessageHandler):
    """Handle 'synthesize' WebSocket messages: TTS + enqueue playback."""

    __slots__ = (
        "_once_dedup",
        "_playback",
        "_synthesis",
    )

    _once_dedup: OnceDedup
    _playback: PlaybackQueue
    _synthesis: SynthesisPipeline

    def __new__(
        cls,
        *,
        synthesis: SynthesisPipeline,
        playback: PlaybackQueue,
        once_dedup: OnceDedup,
    ) -> Self:
        self = super().__new__(cls)
        self._synthesis = synthesis
        self._playback = playback
        self._once_dedup = once_dedup
        return self

    async def __call__(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Synthesize speech and enqueue for playback."""
        request_id = str(msg.get("id", ""))
        text = str(msg.get("text", ""))
        if not text:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": "empty text"}
            )
            return

        speaker_boost_raw = msg.get("speaker_boost")
        spec = SynthesisSpec(
            voice=parse_optional_str(msg, "voice"),
            provider=parse_optional_str(msg, "provider") or auto_detect_provider(),
            model=parse_optional_str(msg, "model"),
            rate=parse_optional_int(msg, "rate"),
            language=parse_optional_str(msg, "language"),
            vibe_tags=parse_optional_str(msg, "vibe_tags"),
            stability=parse_optional_float(msg, "stability"),
            similarity=parse_optional_float(msg, "similarity"),
            style=parse_optional_float(msg, "style"),
            speaker_boost=(
                bool(speaker_boost_raw) if speaker_boost_raw is not None else None
            ),
            api_key=parse_optional_str(msg, "api_key"),
        )
        once = parse_optional_int(msg, "once")

        provider_name = spec.provider or ""
        resolved_voice = spec.voice or ""

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
                text,
                spec,
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
                spec,
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
        await self._playback.enqueue(
            PlaybackItem(path=output_path, request_id=request_id, notify=done_event)
        )
        await websocket.send_json({"type": "playing", "id": request_id})
        await done_event.wait()
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_json({"type": "done", "id": request_id})

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


class RecordHandler(MessageHandler):
    """Handle 'record' WebSocket messages: TTS without playback."""

    __slots__ = ("_synthesis",)

    _synthesis: SynthesisPipeline

    def __new__(
        cls,
        *,
        synthesis: SynthesisPipeline,
    ) -> Self:
        self = super().__new__(cls)
        self._synthesis = synthesis
        return self

    async def __call__(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Synthesize speech and return audio bytes without playback."""
        request_id = str(msg.get("id", ""))
        text = str(msg.get("text", ""))
        if not text:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": "empty text"}
            )
            return

        speaker_boost_raw = msg.get("speaker_boost")
        spec = SynthesisSpec(
            voice=parse_optional_str(msg, "voice"),
            provider=parse_optional_str(msg, "provider") or auto_detect_provider(),
            model=parse_optional_str(msg, "model"),
            rate=parse_optional_int(msg, "rate"),
            language=parse_optional_str(msg, "language"),
            vibe_tags=parse_optional_str(msg, "vibe_tags"),
            stability=parse_optional_float(msg, "stability"),
            similarity=parse_optional_float(msg, "similarity"),
            style=parse_optional_float(msg, "style"),
            speaker_boost=(
                bool(speaker_boost_raw) if speaker_boost_raw is not None else None
            ),
            api_key=parse_optional_str(msg, "api_key"),
        )

        logger.info(
            "Record: id=%s provider=%s voice=%s chars=%d",
            request_id,
            spec.provider or "",
            spec.voice or "",
            len(text),
        )

        try:
            output_path = await self._synthesis.synthesize_to_file(
                text,
                spec,
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
