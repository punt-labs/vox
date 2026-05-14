"""voxd -- audio server daemon.

Pure audio server. Receives synthesis requests over WebSocket,
synthesizes via configured providers, plays through speakers.
Knows nothing about MCP, hooks, projects, sessions, or Claude Code.
"""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import logging
import os
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from socket import socket
from typing import TYPE_CHECKING

import typer
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from punt_vox import cache as _cache_module
from punt_vox.paths import ensure_user_dirs, installed_version
from punt_vox.providers import auto_detect_provider
from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.config import (  # pyright: ignore[reportPrivateUsage]
    DaemonConfig,
    _config_dir,
    _install_token_redact_filter,
    _log_dir,
    _run_dir,
)
from punt_vox.voxd.dedup import ChimeDedup, OnceDedup
from punt_vox.voxd.music_scheduler import MusicScheduler
from punt_vox.voxd.playback import (  # pyright: ignore[reportPrivateUsage]
    _AUDIO_ENV_KEYS,
    PlaybackItem,
    PlaybackQueue,
    _player_binary_path,
)
from punt_vox.voxd.synthesis import (  # pyright: ignore[reportPrivateUsage]
    _LOCAL_PROVIDERS,
    SynthesisPipeline,
)
from punt_vox.voxd.track_generator import TrackGenerator

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8421
DEFAULT_HOST = "127.0.0.1"

# Module-level SynthesisPipeline, set by _get_pipeline() on first use.
_pipeline: SynthesisPipeline | None = None


# ---------------------------------------------------------------------------
# Free-standing convenience wrappers -- delegate to DaemonConfig classmethods
# ---------------------------------------------------------------------------


def _load_keys(config_dir: Path) -> frozenset[str]:  # pyright: ignore[reportUnusedFunction]
    """Load keys.env from config dir into os.environ."""
    cfg = DaemonConfig(run_dir=_run_dir(), config_dir=config_dir, log_dir=_log_dir())
    return cfg.load_keys()


def read_port_file() -> int | None:
    """Read the daemon port from the port file. Returns None if missing."""
    return DaemonConfig.read_port_file(_run_dir())


def read_token_file() -> str | None:
    """Read the daemon auth token. Returns None if missing."""
    return DaemonConfig.read_token_file(_run_dir())


# ---------------------------------------------------------------------------
# Playback -- delegated to punt_vox.voxd.playback
# ---------------------------------------------------------------------------


def _music_output_dir() -> Path:
    """Return the directory for generated music tracks."""
    from punt_vox.dirs import music_output_dir

    return music_output_dir()


def _get_track_generator() -> TrackGenerator:
    """Return the module-level TrackGenerator (created in main)."""
    if _track_generator is None:
        # Fallback for tests and handlers called before main().
        return TrackGenerator(_music_output_dir())
    return _track_generator


# Module-level TrackGenerator, set by main() at daemon startup.
_track_generator: TrackGenerator | None = None


def _record_playback_result(
    ctx: DaemonContext,
    *,
    path: Path,
    rc: int,
    elapsed: float,
    stderr: str,
) -> None:
    """Update ctx.last_playback with a freshly-observed playback result.

    Thin wrapper that delegates to the PlaybackQueue owned by ctx.
    Kept as a module-level function so _try_direct_play can call it
    without knowing about PlaybackQueue internals.
    """
    ctx.last_playback = {
        "file": str(path),
        "rc": rc,
        "elapsed_s": round(elapsed, 4),
        "stderr": stderr,
        "ts": time.time(),
    }


# ---------------------------------------------------------------------------
# Daemon context
# ---------------------------------------------------------------------------


class DaemonContext:
    """Shared mutable state for the voxd process."""

    def __init__(
        self,
        *,
        auth_token: str | None = None,
        port: int = DEFAULT_PORT,
        playback: PlaybackQueue | None = None,
        music: MusicScheduler | None = None,
    ) -> None:
        self._playback: PlaybackQueue = playback or PlaybackQueue()
        self._music: MusicScheduler = music or MusicScheduler(
            TrackGenerator(_music_output_dir())
        )
        self.start_time: float = time.monotonic()
        self.auth_token: str | None = auth_token
        self.port: int = port
        self.chime_dedup = ChimeDedup()
        self.once_dedup = OnceDedup()
        self.client_count: int = 0
        # Cached once at startup so /health does not hit importlib.metadata
        # on every request. See ``punt_vox.paths.installed_version`` for
        # fallback semantics when running from an uninstalled source tree.
        self.daemon_version: str = installed_version()

    # -- Delegation properties for PlaybackQueue -----------------------------

    @property
    def playback_queue(self) -> asyncio.Queue[PlaybackItem]:
        """Return the underlying asyncio.Queue from PlaybackQueue."""
        return self._playback._queue

    @property
    def last_playback(self) -> dict[str, object] | None:
        """Return the most recent playback result dict."""
        return self._playback.last_result

    @last_playback.setter
    def last_playback(self, value: dict[str, object] | None) -> None:
        self._playback.set_last_result(value)

    # -- Delegation properties for MusicScheduler ----------------------------

    @property
    def music_mode(self) -> str:
        """Return the current music mode."""
        return self._music.mode

    @music_mode.setter
    def music_mode(self, value: str) -> None:
        self._music.mode = value

    @property
    def music_style(self) -> str:
        """Return the current music style."""
        return self._music.style

    @music_style.setter
    def music_style(self, value: str) -> None:
        self._music.style = value

    @property
    def music_owner(self) -> str:
        """Return the current music owner session ID."""
        return self._music.owner

    @music_owner.setter
    def music_owner(self, value: str) -> None:
        self._music.owner = value

    @property
    def music_vibe(self) -> tuple[str, str]:
        """Return the current (vibe, vibe_tags) tuple."""
        return self._music.vibe

    @music_vibe.setter
    def music_vibe(self, value: tuple[str, str]) -> None:
        self._music.vibe = value

    @property
    def music_track(self) -> Path | None:
        """Return the current track path."""
        return self._music.track

    @music_track.setter
    def music_track(self, value: Path | None) -> None:
        self._music.track = value

    @property
    def music_track_name(self) -> str:
        """Return the current track name."""
        return self._music.track_name

    @music_track_name.setter
    def music_track_name(self, value: str) -> None:
        self._music.track_name = value

    @property
    def music_proc(self) -> asyncio.subprocess.Process | None:
        """Return the current music subprocess."""
        return self._music.proc

    @music_proc.setter
    def music_proc(self, value: asyncio.subprocess.Process | None) -> None:
        self._music.proc = value

    @property
    def music_state(self) -> str:
        """Return the current music state."""
        return self._music.state

    @music_state.setter
    def music_state(self, value: str) -> None:
        self._music.state = value

    @property
    def music_changed(self) -> asyncio.Event:
        """Return the music-changed event."""
        return self._music.changed

    @music_changed.setter
    def music_changed(self, value: asyncio.Event) -> None:
        self._music.changed = value

    @property
    def music_replay(self) -> bool:
        """Return whether replay mode is active."""
        return self._music.replay

    @music_replay.setter
    def music_replay(self, value: bool) -> None:
        self._music.replay = value


_chime_resolver = ChimeResolver()


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _check_auth(websocket: WebSocket, ctx: DaemonContext) -> bool:
    """Verify the auth token from query param or first message."""
    if ctx.auth_token is None:
        return True  # No auth configured (tests)
    token = websocket.query_params.get("token", "")
    return hmac.compare_digest(token, ctx.auth_token)


# ---------------------------------------------------------------------------
# WebSocket message handlers
# ---------------------------------------------------------------------------


def _parse_optional_float(msg: dict[str, object], key: str) -> float | None:
    """Extract an optional float field from a message dict."""
    raw = msg.get(key)
    if raw is None:
        return None
    return float(str(raw))


def _parse_optional_int(msg: dict[str, object], key: str) -> int | None:
    """Extract an optional int field from a message dict."""
    raw = msg.get(key)
    if raw is None:
        return None
    return int(str(raw))


def _parse_optional_str(msg: dict[str, object], key: str) -> str | None:
    """Extract an optional string field, returning None for empty strings."""
    raw = str(msg.get(key, ""))
    return raw or None


def _get_pipeline(ctx: DaemonContext) -> SynthesisPipeline:
    """Return the module-level SynthesisPipeline, creating one if needed."""
    global _pipeline
    if _pipeline is None:
        _pipeline = SynthesisPipeline(playback_mutex=ctx._playback.mutex)
    return _pipeline


# Backward-compatible aliases for names that moved to synthesis.py.
# Re-exported via __init__.py and referenced by existing tests.
_apply_vibe_for_synthesis = SynthesisPipeline.apply_vibe_for_synthesis
_model_supports_expressive_tags = SynthesisPipeline.model_supports_expressive_tags


async def _synthesize_to_file(
    text: str,
    voice: str | None,
    provider_name: str,
    model: str | None,
    language: str | None,
    rate: int | None,
    vibe_tags: str | None,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    *,
    speaker_boost: bool | None,
    api_key: str | None,
    request_id: str = "",
) -> Path:
    """Delegate to the SynthesisPipeline instance."""
    pipeline = _get_pipeline(DaemonContext(auth_token=None, port=0))
    return await pipeline.synthesize_to_file(
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


async def _try_direct_play(
    *,
    text: str,
    voice: str | None,
    provider_name: str,
    model: str | None,
    language: str | None,
    rate: int | None,
    vibe_tags: str | None,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    speaker_boost: bool | None,
    api_key: str | None,
    ctx: DaemonContext,
) -> int | None | Exception:
    """Delegate to the SynthesisPipeline instance."""
    pipeline = _get_pipeline(ctx)

    def _record(
        *,
        path: Path,
        rc: int,
        elapsed: float,
        stderr: str,
    ) -> None:
        _record_playback_result(ctx, path=path, rc=rc, elapsed=elapsed, stderr=stderr)

    return await pipeline.try_direct_play(
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
        record_result=_record,
    )


async def _handle_synthesize(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'synthesize' message: TTS + enqueue playback."""
    request_id = str(msg.get("id", ""))
    text = str(msg.get("text", ""))
    if not text:
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": "empty text"}
        )
        return

    voice = _parse_optional_str(msg, "voice")
    provider_name = _parse_optional_str(msg, "provider") or auto_detect_provider()
    model = _parse_optional_str(msg, "model")
    rate = _parse_optional_int(msg, "rate")
    language = _parse_optional_str(msg, "language")
    vibe_tags = _parse_optional_str(msg, "vibe_tags")
    stability = _parse_optional_float(msg, "stability")
    similarity = _parse_optional_float(msg, "similarity")
    style = _parse_optional_float(msg, "style")
    speaker_boost_raw = msg.get("speaker_boost")
    speaker_boost = bool(speaker_boost_raw) if speaker_boost_raw is not None else None
    api_key = _parse_optional_str(msg, "api_key")
    once = _parse_optional_int(msg, "once")

    resolved_voice = voice or ""

    # Opt-in dedup: only when the caller explicitly sets `once` to a
    # positive TTL. With `once` absent, null, or 0, every request plays
    # — the legacy always-on 5s dedup for speech was removed in vox-0e9.
    # When we record an entry, track that fact so we can roll it back
    # on synthesis/playback failure; otherwise a failed request would
    # leave a zombie dedup entry that incorrectly suppresses retries.
    dedup_recorded = False
    if once is not None and once > 0:
        hit = ctx.once_dedup.check_and_record(text, float(once))
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
        """Remove the dedup entry we recorded above, if any.

        Called on every failure path between the record call and the
        successful completion of playback. Without this, a failure
        would leave a zombie entry in ``ctx.once_dedup._seen`` that
        would incorrectly dedup the next retry of the same text.
        """
        if dedup_recorded:
            ctx.once_dedup.rollback(text)

    logger.info(
        "Synthesize: id=%s provider=%s voice=%s chars=%d",
        request_id,
        provider_name,
        resolved_voice,
        len(text),
    )

    # Local providers (espeak, say) play directly to the audio device,
    # bypassing the synthesize-cache-enqueue pipeline. Cloud providers
    # are skipped entirely so we don't pay for provider construction.
    if provider_name in _LOCAL_PROVIDERS:
        direct_result = await _try_direct_play(
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
            ctx=ctx,
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
        output_path = await _synthesize_to_file(
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
    await ctx.playback_queue.put(
        PlaybackItem(path=output_path, request_id=request_id, notify=done_event)
    )
    await websocket.send_json({"type": "playing", "id": request_id})
    await done_event.wait()
    # Client may have already closed the connection after receiving 'playing'.
    # Suppress any send failure — the audio has already played.
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        await websocket.send_json({"type": "done", "id": request_id})


async def _handle_record(
    msg: dict[str, object],
    websocket: WebSocket,
    _ctx: DaemonContext,
) -> None:
    """Handle a 'record' message: TTS without playback, return audio bytes."""
    request_id = str(msg.get("id", ""))
    text = str(msg.get("text", ""))
    if not text:
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": "empty text"}
        )
        return

    voice = _parse_optional_str(msg, "voice")
    provider_name = _parse_optional_str(msg, "provider") or auto_detect_provider()
    model = _parse_optional_str(msg, "model")
    rate = _parse_optional_int(msg, "rate")
    language = _parse_optional_str(msg, "language")
    vibe_tags = _parse_optional_str(msg, "vibe_tags")
    stability = _parse_optional_float(msg, "stability")
    similarity = _parse_optional_float(msg, "similarity")
    style = _parse_optional_float(msg, "style")
    speaker_boost_raw = msg.get("speaker_boost")
    speaker_boost = bool(speaker_boost_raw) if speaker_boost_raw is not None else None
    api_key = _parse_optional_str(msg, "api_key")

    logger.info(
        "Record: id=%s provider=%s voice=%s chars=%d",
        request_id,
        provider_name,
        voice or "",
        len(text),
    )

    try:
        output_path = await _synthesize_to_file(
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
    # Only unlink tempfiles produced by a fresh synthesis. Cache-hit
    # paths return the on-disk entry directly, and removing it would
    # poison every subsequent identical request — including the
    # anti-poison invariant TestCacheApiKeyBypass exercises. The
    # CACHE_DIR lookup goes through the module (``_cache_module``)
    # instead of a bound import so tests that monkey-patch
    # ``punt_vox.cache.CACHE_DIR`` to a tmp dir stay in sync with the
    # handler's view of what counts as a cache-owned path.
    is_cache_owned = output_path.is_relative_to(_cache_module.CACHE_DIR)
    if not is_cache_owned:
        output_path.unlink(missing_ok=True)  # clean up temp file
    encoded = base64.b64encode(audio_data).decode("ascii")
    await websocket.send_json({"type": "audio", "id": request_id, "data": encoded})


async def _handle_chime(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'chime' message: play a bundled chime sound."""
    signal = str(msg.get("signal", "done"))
    path = _chime_resolver.resolve(signal)
    if path is None:
        logger.warning("Unknown chime signal: %s", signal)
        await websocket.send_json(
            {"type": "error", "id": "", "message": f"unknown chime: {signal}"}
        )
        return

    # Chimes are always deduped with a fixed window — user explicitly
    # confirmed this behavior is desired in vox-0e9 scoping. Unlike
    # speech, chimes do not opt in via a `once` flag.
    if not ctx.chime_dedup.should_play(signal):
        logger.info("Dedup: skipping duplicate chime %s", signal)
        await websocket.send_json({"type": "done", "id": ""})
        return

    logger.info("Chime: %s", signal)
    done_event = asyncio.Event()
    await ctx.playback_queue.put(
        PlaybackItem(path=path, request_id=f"chime:{signal}", notify=done_event)
    )
    await websocket.send_json({"type": "playing", "id": f"chime:{signal}"})
    await done_event.wait()
    # Client may have already closed the connection after receiving 'playing'.
    # Suppress any send failure — the audio has already played.
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        await websocket.send_json({"type": "done", "id": f"chime:{signal}"})


async def _handle_voices(
    msg: dict[str, object],
    websocket: WebSocket,
    _ctx: DaemonContext,
) -> None:
    """Handle a 'voices' message: list available voices."""
    from punt_vox.providers import get_provider

    provider_name = _parse_optional_str(msg, "provider") or auto_detect_provider()

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


def _health_payload_minimal(ctx: DaemonContext) -> dict[str, object]:
    """Return the public health payload safe for unauthenticated callers.

    Excludes ``audio_env``, ``player_binary``, and ``last_playback`` so the
    HTTP ``/health`` route can never leak environment variables or stderr
    contents to non-localhost listeners.
    """
    from punt_vox.providers import auto_detect_provider

    uptime = time.monotonic() - ctx.start_time
    return {
        "status": "ok",
        "uptime_seconds": round(uptime, 1),
        "queued": ctx.playback_queue.qsize(),
        "port": ctx.port,
        "active_sessions": ctx.client_count,
        "provider": auto_detect_provider(),
    }


def _health_payload_full(ctx: DaemonContext) -> dict[str, object]:
    """Return the full diagnostic health payload for authenticated callers.

    Adds the audio environment snapshot, the resolved player binary, the
    last playback result, the running process id, and the cached daemon
    version. Used only by the WebSocket health handler, which is gated
    by the auth token.

    The ``pid`` field is used by ``vox daemon restart`` to confirm the
    daemon has come back up as a fresh process. The ``daemon_version``
    field is used by ``vox doctor`` to warn when the running daemon
    does not match the wheel installed on disk (vox-nmb). Neither is
    exposed on the unauthenticated HTTP ``/health`` route — version
    info is a fingerprinting aid for targeted exploitation, and the
    minimal payload stays minimal.
    """
    payload = _health_payload_minimal(ctx)
    payload["audio_env"] = {k: os.environ.get(k, "<unset>") for k in _AUDIO_ENV_KEYS}
    payload["player_binary"] = _player_binary_path()
    payload["last_playback"] = ctx.last_playback
    payload["pid"] = os.getpid()
    payload["daemon_version"] = ctx.daemon_version
    return payload


async def _handle_health(
    _msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'health' message over the authenticated WebSocket."""
    payload = _health_payload_full(ctx)
    payload["type"] = "health"
    await websocket.send_json(payload)


# ---------------------------------------------------------------------------
# Music loop and handlers
# ---------------------------------------------------------------------------


def _slugify(text: str, max_len: int = 40) -> str:
    """Slugify a string for use in filenames. Delegates to TrackGenerator."""
    return TrackGenerator.slugify(text, max_len)


async def _kill_music_proc(ctx: DaemonContext) -> None:
    """Kill the current music subprocess. Delegates to MusicScheduler."""
    await ctx._music.kill_proc()


def _auto_track_name(ctx: DaemonContext) -> str:  # pyright: ignore[reportUnusedFunction]
    """Derive a short auto-name from vibe + style + YYYYMMDD-HHMM."""
    vibe, _ = ctx.music_vibe
    style = ctx.music_style
    return _get_track_generator().auto_track_name(vibe, style)


async def _music_loop(ctx: DaemonContext) -> None:  # pyright: ignore[reportUnusedFunction]
    """Background task: generate and loop music tracks. Delegates to MusicScheduler."""
    await ctx._music.loop()


async def _handle_music_on(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'music_on' message: start or transfer music ownership.

    When the same owner re-sends music_on while already playing, the
    current subprocess is kept alive and music_changed is signaled —
    the MusicLoop's gapless handoff path generates the new track while
    the old one keeps playing.

    Ownership transfer is atomic: kill existing subprocess, update all
    state fields, then signal MusicLoop. No interleaving.

    When a ``name`` field is present and a track with that name already
    exists on disk, the existing file is replayed without generation
    (zero credits).
    """
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
        safe_name = _slugify(name, max_len=60)
        if not safe_name:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": "invalid track name"}
            )
            return
        existing_path = _music_output_dir() / f"{safe_name}.mp3"
        if existing_path.exists():
            await _kill_music_proc(ctx)
            ctx.music_mode = "on"
            if style:
                ctx.music_style = style
            ctx.music_owner = owner_id
            ctx.music_vibe = (vibe, vibe_tags)
            ctx.music_track = existing_path
            ctx.music_track_name = safe_name
            ctx.music_state = "playing"
            ctx.music_replay = True
            ctx.music_changed.set()

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
    # playback so the new owner starts fresh.  When the *same* owner
    # re-sends music_on (e.g. style change), skip the kill — let the
    # MusicLoop's vibe-change path handle gapless handoff while the
    # current track keeps looping.
    is_already_playing = ctx.music_mode == "on" and ctx.music_proc is not None
    if not is_already_playing or ctx.music_owner != owner_id:
        await _kill_music_proc(ctx)

    ctx.music_mode = "on"
    if style:
        ctx.music_style = style
    ctx.music_owner = owner_id
    ctx.music_vibe = (vibe, vibe_tags)
    ctx.music_track_name = _slugify(name, max_len=60) if name else ""
    ctx.music_replay = False
    ctx.music_state = "generating"
    ctx.music_changed.set()

    logger.info(
        "Music on: owner=%s style=%s vibe=%s name=%s",
        owner_id,
        ctx.music_style,
        vibe,
        ctx.music_track_name,
    )
    await websocket.send_json(
        {"type": "music_on", "id": request_id, "status": "generating"}
    )


async def _handle_music_off(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'music_off' message: stop music playback."""
    request_id = str(msg.get("id", ""))

    await _kill_music_proc(ctx)
    ctx.music_mode = "off"
    ctx.music_state = "idle"
    ctx.music_replay = False
    ctx.music_changed.set()

    logger.info("Music off")
    await websocket.send_json(
        {"type": "music_off", "id": request_id, "status": "stopped"}
    )


async def _handle_music_play(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
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

    safe_name = _slugify(name, max_len=60)
    if not safe_name:
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": "invalid track name"}
        )
        return
    track_path = _music_output_dir() / f"{safe_name}.mp3"

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
    await _kill_music_proc(ctx)
    ctx.music_mode = "on"
    ctx.music_owner = owner_id
    ctx.music_track = track_path
    ctx.music_track_name = safe_name
    ctx.music_state = "playing"
    ctx.music_replay = True
    ctx.music_changed.set()

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
    msg: dict[str, object],
    websocket: WebSocket,
    _ctx: DaemonContext,
) -> None:
    """Handle a 'music_list' message: return saved tracks with metadata."""
    request_id = str(msg.get("id", ""))
    tracks = _get_track_generator().list_tracks()

    await websocket.send_json(
        {
            "type": "music_list",
            "id": request_id,
            "tracks": tracks,
        }
    )


async def _handle_music_vibe(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
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

    if owner_id != ctx.music_owner:
        await websocket.send_json(
            {"type": "music_vibe", "id": request_id, "status": "ignored"}
        )
        return

    new_vibe = (vibe, vibe_tags)
    if new_vibe == ctx.music_vibe:
        await websocket.send_json(
            {"type": "music_vibe", "id": request_id, "status": "ignored"}
        )
        return

    ctx.music_vibe = new_vibe
    ctx.music_changed.set()

    logger.info("Music vibe changed: vibe=%s tags=%s", vibe, vibe_tags)
    await websocket.send_json(
        {"type": "music_vibe", "id": request_id, "status": "generating"}
    )


async def _handle_music_next(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'music_next' message: skip to a new track.

    Signals MusicLoop to regenerate without changing vibe or style.
    The current track keeps playing until the new one is ready
    (gapless handoff via the vibe-change path).
    """
    request_id = str(msg.get("id", ""))
    owner_id = str(msg.get("owner_id", ""))

    if not owner_id:
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": "owner_id is required"}
        )
        return

    if ctx.music_mode != "on":
        await websocket.send_json(
            {"type": "music_next", "id": request_id, "status": "ignored"}
        )
        return

    ctx.music_track_name = ""
    ctx.music_replay = False
    ctx.music_changed.set()

    logger.info("Music next: owner=%s", owner_id)
    await websocket.send_json(
        {"type": "music_next", "id": request_id, "status": "generating"}
    )


# ---------------------------------------------------------------------------
# WebSocket route
# ---------------------------------------------------------------------------


_HANDLERS: dict[
    str,
    Callable[
        [dict[str, object], WebSocket, DaemonContext],
        object,
    ],
] = {
    "synthesize": _handle_synthesize,
    "chime": _handle_chime,
    "record": _handle_record,
    "voices": _handle_voices,
    "health": _handle_health,
    "music_on": _handle_music_on,
    "music_off": _handle_music_off,
    "music_play": _handle_music_play,
    "music_list": _handle_music_list,
    "music_vibe": _handle_music_vibe,
    "music_next": _handle_music_next,
}


async def _ws_route(websocket: WebSocket) -> None:
    """Main WebSocket route at /ws."""
    ctx: DaemonContext = websocket.app.state.ctx

    if not _check_auth(websocket, ctx):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    ctx.client_count += 1
    logger.info("Client connected (total: %d)", ctx.client_count)

    try:
        while True:
            # Preempt Starlette's RuntimeError on a peer-closed socket.
            # After the vox-ehf fix in 4.3.0, chime/unmute clients return
            # on the ``"playing"`` ack and close the WebSocket while this
            # loop is still awaiting the next ``receive_text()``. The
            # handler's trailing ``contextlib.suppress(WebSocketDisconnect,
            # RuntimeError)`` send of the stale ``"done"`` message lands
            # on the peer-closed socket and Starlette transitions the
            # ``application_state`` to ``DISCONNECTED`` synchronously. If
            # we did not check the state here, the next ``receive_text()``
            # would raise ``RuntimeError('WebSocket is not connected. Need
            # to call "accept" first.')`` — not ``WebSocketDisconnect`` —
            # and fall through to ``except Exception`` below, logging a
            # full traceback on every chime/unmute/recap. Checking the
            # state ourselves lets us break out cleanly without widening
            # the outer exception surface (vox-ewh).
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
            handler = _HANDLERS.get(msg_type)
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

            # Each message handler is awaited in the receive loop.
            # Multiple clients are concurrent (each has its own receive loop),
            # but messages from a single client are processed sequentially.
            await handler(msg, websocket, ctx)  # type: ignore[misc]
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error")
    finally:
        ctx.client_count -= 1
        logger.info("Client disconnected (total: %d)", ctx.client_count)


# ---------------------------------------------------------------------------
# HTTP health route
# ---------------------------------------------------------------------------


async def _health_route(request: Request) -> JSONResponse:
    """Unauthenticated HTTP health endpoint -- minimal payload only."""
    ctx: DaemonContext = request.app.state.ctx
    return JSONResponse(_health_payload_minimal(ctx))


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(
    ctx: DaemonContext | None = None,
    *,
    lifespan: (Callable[[Starlette], AbstractAsyncContextManager[None]] | None) = None,
) -> Starlette:
    """Build the Starlette ASGI application.

    Exposed as a factory so tests can construct the app without starting
    uvicorn.
    """
    if ctx is None:
        ctx = DaemonContext()

    routes: list[Route | WebSocketRoute] = [
        Route("/health", _health_route, methods=["GET"]),
        WebSocketRoute("/ws", _ws_route),
    ]

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.ctx = ctx
    return app


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

cli = typer.Typer(add_completion=False)


@cli.callback(invoke_without_command=True)
def main(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Listen port"),
    host: str = typer.Option(
        DEFAULT_HOST, "--host", envvar="VOXD_BIND", help="Listen host"
    ),
) -> None:
    """Start the voxd audio server daemon."""
    # Create (or tighten) per-user state dirs before anything else
    # touches the filesystem. ``ensure_user_dirs`` forces mode 0700 on
    # ``~/.punt-labs/vox`` and its ``logs``/``run``/``cache``
    # subdirectories, including pre-existing dirs that were created
    # under a looser umask in earlier versions.
    ensure_user_dirs()

    daemon_cfg = DaemonConfig(
        run_dir=_run_dir(), config_dir=_config_dir(), log_dir=_log_dir()
    )

    # Configure logging
    daemon_cfg.configure_logging()
    daemon_cfg.log_environment()

    # Load provider keys
    loaded_keys = daemon_cfg.load_keys()
    if loaded_keys:
        logger.info(
            "Loaded provider keys from %s: %s",
            daemon_cfg.config_dir,
            sorted(loaded_keys),
        )
    else:
        logger.info("No provider keys loaded from %s", daemon_cfg.config_dir)

    # Auth token
    auth_token = daemon_cfg.read_or_create_token()

    # Track generator for music -- set module-level for handler access.
    global _track_generator
    _track_generator = TrackGenerator(_music_output_dir())

    # Music scheduler owns the background loop and all music state.
    scheduler = MusicScheduler(_track_generator)
    ctx = DaemonContext(auth_token=auth_token, port=port, music=scheduler)

    logger.info("Starting voxd on %s:%d", host, port)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        # Start playback consumer
        consumer_task = asyncio.create_task(ctx._playback.consumer())
        logger.info("Playback consumer started")
        # Start music loop
        music_task = asyncio.create_task(scheduler.loop())
        logger.info("Music loop started")
        try:
            yield
        finally:
            music_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await music_task
            # Kill any lingering music subprocess.
            await scheduler.kill_proc()
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
            daemon_cfg.remove_port_file()
            logger.info("voxd stopped")

    app = build_app(ctx, lifespan=lifespan)

    if host not in ("127.0.0.1", "::1", "localhost"):
        logger.warning(
            "Binding to %s — voxd is accessible from the network. "
            "Ensure VOXD_TOKEN is set on all clients.",
            host,
        )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,
        log_level="warning",
        access_log=True,
    )
    _install_token_redact_filter()
    server = uvicorn.Server(config)

    # Write port file after bind
    original_startup = server.startup

    async def _startup_with_port_file(
        sockets: list[socket] | None = None,
    ) -> None:
        await original_startup(sockets=sockets)
        if server.servers and server.servers[0].sockets:
            actual_port = server.servers[0].sockets[0].getsockname()[1]
            daemon_cfg.write_port_file(actual_port)
            logger.info("voxd listening on http://%s:%d", host, actual_port)
        else:
            logger.error("Server started but no bound sockets; shutting down")
            server.should_exit = True

    server.startup = _startup_with_port_file  # type: ignore[method-assign]

    server.run()


def entrypoint() -> None:
    """Console script entry point — invokes the typer CLI."""
    cli()


if __name__ == "__main__":
    cli()
