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
import re
import tempfile
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from socket import socket
from typing import TYPE_CHECKING, cast

import typer
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from punt_vox import cache as _cache_module
from punt_vox.cache import cache_get, cache_put
from punt_vox.core import TTSClient
from punt_vox.normalize import VIBE_TAG_RE, normalize_for_speech
from punt_vox.paths import ensure_user_dirs, installed_version
from punt_vox.providers import auto_detect_provider, get_provider
from punt_vox.types import (
    AudioProviderId,
    AudioRequest,
    DirectPlayProvider,
    TTSProvider,
)
from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.config import (  # pyright: ignore[reportPrivateUsage]
    DaemonConfig,
    _config_dir,
    _install_token_redact_filter,
    _log_dir,
    _run_dir,
)
from punt_vox.voxd.dedup import ChimeDedup, OnceDedup
from punt_vox.voxd.playback import (  # pyright: ignore[reportPrivateUsage]
    _AUDIO_ENV_KEYS,
    PlaybackItem,
    PlaybackQueue,
    _monotonic,
    _music_player_command,
    _player_binary_path,
)
from punt_vox.voxd.track_generator import TrackGenerator

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8421
DEFAULT_HOST = "127.0.0.1"

# Lock to serialize os.environ mutation during synthesis with per-request API keys.
_env_lock = asyncio.Lock()


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
    ) -> None:
        self._playback: PlaybackQueue = playback or PlaybackQueue()
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
        # Music state — daemon-wide, one set of speakers, one loop.
        self.music_mode: str = "off"
        self.music_style: str = ""
        self.music_owner: str = ""
        self.music_vibe: tuple[str, str] = ("", "")  # (vibe, vibe_tags)
        self.music_track: Path | None = None
        self.music_track_name: str = ""
        self.music_proc: asyncio.subprocess.Process | None = None
        self.music_state: str = "idle"  # "idle" | "generating" | "playing"
        self.music_changed: asyncio.Event = asyncio.Event()
        self.music_replay: bool = False

    # -- Delegation properties for backward compatibility --------------------

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


def _build_audio_request(
    normalized_text: str,
    voice: str | None,
    language: str | None,
    rate: int | None,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    *,
    speaker_boost: bool | None,
    provider_id: str,
) -> AudioRequest:
    """Build an AudioRequest from parsed message fields."""
    return AudioRequest(
        text=normalized_text,
        voice=voice,
        language=language,
        rate=rate,
        stability=stability,
        similarity=similarity,
        style=style,
        speaker_boost=speaker_boost,
        provider=AudioProviderId(provider_id)
        if provider_id in AudioProviderId.__members__
        else None,
    )


def _model_supports_expressive_tags(provider_name: str, model: str | None) -> bool:
    """Whether the given provider+model combo interprets bracket-style tags.

    Pure lookup: does NOT construct the provider or touch any SDK client,
    so it can run before voxd enters the env-mutation lock that the real
    synthesize path needs. ElevenLabs is the only provider whose answer
    depends on the model — all others return False unconditionally.

    The ``ElevenLabsProvider`` import is deferred inside the function so
    voxd does not eagerly load the ElevenLabs SDK at module import time
    on systems whose users only ever run espeak/say. Mirrors the lazy
    pattern in :mod:`punt_vox.providers`.
    """
    if provider_name == "elevenlabs":
        from punt_vox.providers.elevenlabs import ElevenLabsProvider

        return ElevenLabsProvider.model_supports_expressive_tags(model)
    return False


def _apply_vibe_for_synthesis(
    raw_text: str,
    vibe_tags: str | None,
    provider_name: str,
    model: str | None,
) -> str:
    """Compose the final synthesis text from raw input + vibe + capability.

    Takes the user's RAW ``raw_text`` (NOT yet normalized). The order of
    operations matters because :func:`punt_vox.normalize.normalize_for_speech`
    discards brackets via its non-prosody-punctuation filter. If we let
    normalization run first, ``[serious] hello`` becomes ``serious hello``
    and the literal word survives into TTS input.

    Non-expressive path:
        Strip ALL vibe tags (any position) via ``VIBE_TAG_RE.sub(...)``,
        collapse/trim whitespace, then normalize the cleaned text. If the
        input contains only tags, return the empty string.

    Expressive path:
        Split text into tag / non-tag segments via ``VIBE_TAG_RE``,
        normalize only the non-tag segments, reassemble with tags intact,
        then prepend session ``vibe_tags``.
    """
    expressive = _model_supports_expressive_tags(provider_name, model)

    if not expressive:
        # Remove tags at ANY position via the regex directly — bypass
        # strip_vibe_tags's guard (which returns original text when
        # stripping leaves nothing). For tags-only input like "[serious]"
        # the correct result is empty, not the bare word "serious".
        cleaned = VIBE_TAG_RE.sub("", raw_text)
        cleaned = re.sub(r"  +", " ", cleaned).strip()
        return normalize_for_speech(cleaned) if cleaned else ""

    # Expressive: normalize around tags so they survive at any position.
    # VIBE_TAG_RE has one capturing group, so split() interleaves:
    #   even indices → plain text, odd indices → tag word (no brackets).
    segments = VIBE_TAG_RE.split(raw_text)
    rebuilt: list[str] = []
    for i, seg in enumerate(segments):
        if i % 2 == 0:
            # Plain text segment — normalize it.
            normed = normalize_for_speech(seg)
            if normed:
                rebuilt.append(normed)
        else:
            # Captured tag word — restore brackets.
            rebuilt.append(f"[{seg}]")

    body = " ".join(rebuilt)

    parts: list[str] = []
    if vibe_tags:
        parts.append(vibe_tags.strip())
    if body:
        parts.append(body)
    return " ".join(parts)


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
    """Run TTS synthesis and return the output path.

    Handles API key injection, provider construction, and caching.
    Raises on failure.

    When ``api_key`` is set, the cache is bypassed on both the lookup
    and the store so the per-call billing scope (vox-a3e) never reads
    bytes that were synthesized under a different key and never leaves
    bytes behind that a later call on a different key could reuse. The
    anonymous path (``api_key is None``) uses the MD5-keyed on-disk
    cache unchanged. See ``src/punt_vox/cache.py`` for the rationale.
    """
    resolved_voice = voice or ""

    # _apply_vibe_for_synthesis takes RAW text and runs normalize_for_speech
    # on the body itself (after splitting leading bracket tags off, which
    # would otherwise be eaten by normalization).
    normalized = _apply_vibe_for_synthesis(text, vibe_tags, provider_name, model)

    # Cache lookup: anonymous calls only. Per-call api_key scopes
    # bypass the cache entirely so a billing-isolated call never
    # reads bytes synthesized under a different key (or no key).
    # CodeQL py/weak-sensitive-data-hashing also required that we
    # never feed the api_key into any digest in cache.py.
    if api_key is None:
        cached = cache_get(normalized, resolved_voice, provider_name)
        if cached is not None:
            return cached
    else:
        logger.debug(
            "Per-call api_key set; bypassing cache for this request (id=%s)",
            request_id,
        )

    # Serialize env mutation + synthesis to avoid concurrent os.environ races.
    async with _env_lock:
        old_key: str | None = None
        env_key_name: str | None = None
        if api_key:
            if provider_name == "elevenlabs":
                env_key_name = "ELEVENLABS_API_KEY"
            elif provider_name == "openai":
                env_key_name = "OPENAI_API_KEY"
            if env_key_name:
                old_key = os.environ.get(env_key_name)
                os.environ[env_key_name] = api_key

        try:
            provider = get_provider(provider_name, config_dir=None, model=model)
            request = _build_audio_request(
                normalized,
                voice,
                language,
                rate,
                stability,
                similarity,
                style,
                speaker_boost=speaker_boost,
                provider_id=provider_name,
            )
            client = TTSClient(provider)

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                output_path = Path(tmp.name)

            await asyncio.to_thread(client.synthesize, request, output_path)

            try:
                synth_size = output_path.stat().st_size
            except OSError:
                synth_size = -1
            if synth_size <= 0:
                logger.error(
                    "synthesize FAILED: provider=%s voice=%s file=%s "
                    "size=%d chars_in=%d -- zero-byte or missing output",
                    provider_name,
                    resolved_voice,
                    output_path,
                    synth_size,
                    len(text),
                )
                # Delete the broken temp file and fail fast. Caching it
                # would poison every subsequent identical request.
                output_path.unlink(missing_ok=True)
                msg = (
                    f"synthesis produced missing or empty output file: "
                    f"{output_path} (provider={provider_name}, "
                    f"voice={resolved_voice}, chars_in={len(text)})"
                )
                raise RuntimeError(msg)

            logger.info(
                "synthesize done: provider=%s voice=%s file=%s size=%d chars_in=%d",
                provider_name,
                resolved_voice,
                output_path,
                synth_size,
                len(text),
            )

            # Only cache verified-good output, and only on the
            # anonymous path. Per-call api_key scopes skip cache_put
            # so a billing-isolated call can never leave bytes behind
            # that a later call on a different key could reuse.
            if api_key is None:
                cache_put(normalized, resolved_voice, provider_name, output_path)
            return output_path
        finally:
            # Restore API key
            if env_key_name and old_key is not None:
                os.environ[env_key_name] = old_key
            elif env_key_name and api_key:
                os.environ.pop(env_key_name, None)


# Providers that synthesize audio directly to the default device. Cloud
# providers are skipped entirely so we don't pay for provider construction
# only to discover they don't implement play_directly.
_LOCAL_PROVIDERS: frozenset[str] = frozenset({"espeak", "say"})

# Map of provider name to its expected API key env var. Used by the
# direct-play env-injection helper.
_PROVIDER_API_KEY_VAR: dict[str, str] = {
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def _run_play_directly_sync(
    provider_name: str,
    api_key: str | None,
    provider_factory: Callable[[], TTSProvider],
    request: AudioRequest,
) -> int | None:
    """Construct provider and call ``play_directly`` on a worker thread.

    Returns ``None`` if the provider does not implement the
    ``DirectPlayProvider`` protocol -- the caller will fall back to the
    synthesize-and-queue path. Mutates ``os.environ`` only if an API key
    is supplied; restoration happens on the same thread so the env-lock
    contract is preserved without holding the lock during audio playback.
    """
    env_var = _PROVIDER_API_KEY_VAR.get(provider_name) if api_key else None
    old_value: str | None = None
    if env_var and api_key:
        old_value = os.environ.get(env_var)
        os.environ[env_var] = api_key
    try:
        provider = provider_factory()
        if not isinstance(provider, DirectPlayProvider):
            return None
        return provider.play_directly(request)
    finally:
        if env_var:
            if old_value is not None:
                os.environ[env_var] = old_value
            else:
                os.environ.pop(env_var, None)


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
    """Attempt direct-to-device playback via the provider.

    Returns one of:
      * an ``int`` exit code (0 on success) when ``play_directly`` ran,
      * ``None`` when the provider opts out of direct play, or
      * an ``Exception`` instance when provider construction or playback
        raised. The caller is responsible for translating the exception
        into a websocket error response.

    The ``_env_lock`` is only acquired when an API key needs to be
    injected. Local providers (espeak, say) take a fast path with no
    cross-request blocking. Audio playback never holds the lock.
    """
    # _apply_vibe_for_synthesis takes RAW text and runs normalize_for_speech
    # on the body itself (after splitting leading bracket tags off, which
    # would otherwise be eaten by normalization).
    normalized = _apply_vibe_for_synthesis(text, vibe_tags, provider_name, model)

    request = _build_audio_request(
        normalized,
        voice,
        language,
        rate,
        stability,
        similarity,
        style,
        speaker_boost=speaker_boost,
        provider_id=provider_name,
    )

    def _factory() -> TTSProvider:
        return get_provider(provider_name, config_dir=None, model=model)

    start = _monotonic()
    try:
        # ctx._playback.mutex serializes audible output across all paths --
        # the queue consumer holds it too. Without this, two hooks firing
        # at once would overlap because direct-play bypasses the queue.
        if api_key and provider_name in _PROVIDER_API_KEY_VAR:
            async with _env_lock, ctx._playback.mutex:
                rc = await asyncio.to_thread(
                    _run_play_directly_sync,
                    provider_name,
                    api_key,
                    _factory,
                    request,
                )
        else:
            async with ctx._playback.mutex:
                rc = await asyncio.to_thread(
                    _run_play_directly_sync,
                    provider_name,
                    None,
                    _factory,
                    request,
                )
    except Exception as exc:
        logger.exception("Direct-play raised for provider=%s", provider_name)
        return exc

    if rc is None:
        return None

    elapsed = _monotonic() - start
    _record_playback_result(
        ctx,
        path=Path(f"<direct:{provider_name}>"),
        rc=rc,
        elapsed=elapsed,
        stderr="" if rc == 0 else f"play_directly rc={rc}",
    )
    if rc == 0:
        logger.info(
            "Direct-play ok: provider=%s voice=%s elapsed=%.3fs chars=%d",
            provider_name,
            voice or "",
            elapsed,
            len(text),
        )
    else:
        logger.error(
            "Direct-play FAILED: provider=%s voice=%s elapsed=%.3fs rc=%d",
            provider_name,
            voice or "",
            elapsed,
            rc,
        )
    return rc


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

_MUSIC_DURATION_MS = 120_000
_MUSIC_MAX_RETRIES = 3


@dataclass(frozen=True)
class _PlaybackWaitResult:
    """Outcome of one iteration of the inner playback-wait loop.

    Returned by :func:`_playback_wait_loop` to tell :func:`_music_loop`
    what state transitions occurred while waiting on a subprocess.
    """

    current_track: Path | None
    gen_task: asyncio.Task[Path] | None
    retry_count: int
    handoff_occurred: bool


async def _playback_wait_loop(
    ctx: DaemonContext,
    proc: asyncio.subprocess.Process,
    current_track: Path,
    gen_task: asyncio.Task[Path] | None,
    retry_count: int,
) -> _PlaybackWaitResult:
    """Wait on a music subprocess, handling events until it should stop.

    Races the subprocess against ``ctx.music_changed`` and an optional
    in-flight generation task.  Handles music-off, generation completion
    (success and failure with retry), vibe changes, replay, and natural
    subprocess termination.

    Returns a :class:`_PlaybackWaitResult` describing the new state.
    The caller uses this to decide whether to respawn the subprocess,
    break out of the playback loop, or continue with a new track.
    """

    while True:
        wait_task = asyncio.create_task(proc.wait())
        changed_task: asyncio.Task[bool] = asyncio.create_task(
            ctx.music_changed.wait(),
        )
        waitables: set[asyncio.Future[object]] = {
            cast("asyncio.Future[object]", wait_task),
            cast("asyncio.Future[object]", changed_task),
        }
        if gen_task is not None:
            waitables.add(cast("asyncio.Future[object]", gen_task))

        _done, pending = await asyncio.wait(
            waitables,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            # Don't cancel the generation task — it may
            # still be running and we want it to finish.
            if t is gen_task:
                continue
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t

        # --- /music off: kill everything immediately ----------
        if ctx.music_mode != "on":
            if gen_task is not None and not gen_task.done():
                gen_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await gen_task
                gen_task = None
            await _kill_music_proc(ctx)
            return _PlaybackWaitResult(
                current_track=None,
                gen_task=None,
                retry_count=retry_count,
                handoff_occurred=False,
            )

        # --- Generation task completed: handoff ---------------
        if gen_task is not None and gen_task.done():
            exc: BaseException | None = gen_task.exception()
            if exc is not None:
                # Generation failed — handle inline so the old track
                # keeps looping.  Only disable music when max retries
                # are exceeded.
                gen_task = None
                retry_count += 1
                logger.error(
                    "Generation failed during playback "
                    "(attempt %d/%d), old track continues",
                    retry_count,
                    _MUSIC_MAX_RETRIES,
                    exc_info=exc,
                )
                if retry_count >= _MUSIC_MAX_RETRIES:
                    logger.error(
                        "Music generation failed %d times, disabling music",
                        _MUSIC_MAX_RETRIES,
                    )
                    ctx.music_mode = "off"
                    retry_count = 0
                    await _kill_music_proc(ctx)
                    ctx.music_state = "idle"
                    return _PlaybackWaitResult(
                        current_track=None,
                        gen_task=None,
                        retry_count=retry_count,
                        handoff_occurred=False,
                    )
                # Under max retries: start a new gen_task after backoff.
                # The old track keeps looping.
                await _music_backoff_sleep(2 ** (retry_count - 1), ctx)
                gen_task = asyncio.create_task(
                    _generate_music_track(ctx),
                )
                ctx.music_state = "generating"
                continue
            new_track: Path = gen_task.result()
            gen_task = None
            ctx.music_track = new_track
            retry_count = 0
            await _kill_music_proc(ctx)
            # Don't clear music_changed here — a vibe change may have
            # arrived during generation.  The next iteration's is_set()
            # check will catch it.
            return _PlaybackWaitResult(
                current_track=new_track,
                gen_task=None,
                retry_count=retry_count,
                handoff_occurred=True,
            )

        # --- Vibe changed: start/restart generation -----------
        if ctx.music_changed.is_set():
            ctx.music_changed.clear()
            if gen_task is not None and not gen_task.done():
                gen_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await gen_task
                gen_task = None

            # Replay: handler pre-set ctx.music_track.
            if ctx.music_replay:
                ctx.music_replay = False
                if ctx.music_track is None:
                    msg = "music_replay set but music_track is None"
                    raise RuntimeError(msg)
                replay_track: Path = ctx.music_track
                await _kill_music_proc(ctx)
                return _PlaybackWaitResult(
                    current_track=replay_track,
                    gen_task=None,
                    retry_count=0,
                    handoff_occurred=False,
                )

            ctx.music_state = "generating"
            gen_task = asyncio.create_task(
                _generate_music_track(ctx),
            )
            # Don't kill the proc — let it keep looping.
            # Re-enter this wait loop so we race the same proc
            # against the new gen_task.
            continue

        # --- Subprocess ended naturally -----------------------
        # Return the same current_track so the caller respawns it.
        return _PlaybackWaitResult(
            current_track=current_track,
            gen_task=gen_task,
            retry_count=retry_count,
            handoff_occurred=False,
        )


async def _music_backoff_sleep(seconds: float, ctx: DaemonContext) -> None:
    """Sleep for backoff in the music loop, interruptible by music_changed.

    Returns immediately if ``music_changed`` fires or ``music_mode``
    becomes ``"off"`` during the wait.  This lets ``/music off`` and
    vibe changes break out of exponential backoff without blocking
    for the full sleep duration.
    """
    sleep_task = asyncio.create_task(asyncio.sleep(seconds))
    changed_task = asyncio.create_task(ctx.music_changed.wait())
    _done, pending = await asyncio.wait(
        {sleep_task, changed_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t


def _slugify(text: str, max_len: int = 40) -> str:
    """Slugify a string for use in filenames. Delegates to TrackGenerator."""
    return TrackGenerator.slugify(text, max_len)


async def _kill_music_proc(ctx: DaemonContext) -> None:
    """Kill the current music subprocess if running."""
    proc = ctx.music_proc
    if proc is not None and proc.returncode is None:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
    ctx.music_proc = None


def _auto_track_name(ctx: DaemonContext) -> str:  # pyright: ignore[reportUnusedFunction]
    """Derive a short auto-name from vibe + style + YYYYMMDD-HHMM."""
    vibe, _ = ctx.music_vibe
    style = ctx.music_style
    return _get_track_generator().auto_track_name(vibe, style)


async def _generate_music_track(ctx: DaemonContext) -> Path:
    """Generate a music track from the current vibe and style.

    Thin wrapper around TrackGenerator.generate that reads from and
    writes back to DaemonContext fields.
    """
    generator = _get_track_generator()
    track_path, resolved_name = await generator.generate(
        ctx.music_vibe, ctx.music_style, ctx.music_track_name
    )
    ctx.music_track_name = resolved_name
    return track_path


async def _music_loop(  # noqa: C901 -- TODO(vox-wy2g): reduce complexity in OO refactor
    ctx: DaemonContext,
) -> None:
    """Background task: generate and loop music tracks.

    Runs for the lifetime of the daemon.  When ``music_mode`` is "on",
    derives a prompt from the current vibe, generates a track via
    :class:`~punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider`,
    and loops it via ffplay at reduced volume.

    The key invariant is **gapless handoff**: the old track keeps
    looping in its own playback subprocess while generation runs as a
    concurrent ``asyncio.Task``.  The old subprocess is killed only
    once the new track is ready.  If the vibe changes again during
    generation, the in-flight generation task is cancelled and a fresh
    one starts — the old track keeps looping throughout.

    Crash recovery: generation failures during playback are handled
    inline — the old track keeps looping while the loop retries with
    exponential backoff (up to 3 attempts).  After 3 consecutive
    failures, ``music_mode`` is set to "off" and the old track is
    killed.  Initial generation failures (no old track yet) propagate
    to the outer handler which retries the entire cycle.
    """
    retry_count = 0
    current_track: Path | None = None
    gen_task: asyncio.Task[Path] | None = None

    while True:
        # Wait until music is turned on.
        while ctx.music_mode != "on":
            ctx.music_changed.clear()
            # Re-check after clear to avoid lost wakeup: a handler may
            # have set music_mode between our check and the clear().
            if ctx.music_mode == "on":
                break
            await ctx.music_changed.wait()

        try:
            # --- Initial generation (no old track to loop) ----------------
            if current_track is None:
                # Replay: a handler already placed a track in ctx.music_track.
                if ctx.music_replay:
                    ctx.music_replay = False
                    ctx.music_changed.clear()
                    if ctx.music_track is None:
                        msg = "music_replay set but music_track is None"
                        raise RuntimeError(msg)
                    current_track = ctx.music_track
                    retry_count = 0
                else:
                    ctx.music_state = "generating"
                    ctx.music_changed.clear()
                    current_track = await _generate_music_track(ctx)
                    ctx.music_track = current_track
                    retry_count = 0

                    # Vibe changed during initial generation — regenerate
                    # immediately (no old track to keep looping).
                    if ctx.music_changed.is_set():
                        logger.info(
                            "Vibe changed during initial generation, regenerating",
                        )
                        current_track = None
                        continue

            # --- Playback loop: loop current_track, generate in parallel --
            # current_track is guaranteed non-None by the initial generation
            # block above — the only path that sets it to None also continues
            # back to the top of the while loop.
            gen_task = None
            while ctx.music_mode == "on":
                ctx.music_state = "playing" if gen_task is None else "generating"
                cmd = _music_player_command(current_track)
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
                ctx.music_proc = proc

                result = await _playback_wait_loop(
                    ctx,
                    proc,
                    current_track,
                    gen_task,
                    retry_count,
                )
                gen_task = result.gen_task
                retry_count = result.retry_count

                # Music was turned off or max retries exceeded.
                if result.current_track is None:
                    current_track = None
                    break

                # Handoff, replay, or natural subprocess end.
                current_track = result.current_track

                # After handoff, the old proc was killed intentionally --
                # non-zero rc is expected, not worth warning about.
                if not result.handoff_occurred:
                    rc = proc.returncode
                    if rc is not None and rc != 0:
                        logger.warning(
                            "Music playback ended with rc=%s for %s",
                            rc,
                            current_track.name,
                        )

        except asyncio.CancelledError:
            if gen_task is not None and not gen_task.done():
                gen_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await gen_task
                gen_task = None
            await _kill_music_proc(ctx)
            ctx.music_state = "idle"
            current_track = None
            raise
        except Exception:
            logger.exception(
                "Music loop error (attempt %d/%d)",
                retry_count + 1,
                _MUSIC_MAX_RETRIES,
            )
            if gen_task is not None and not gen_task.done():
                gen_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await gen_task
                gen_task = None
            await _kill_music_proc(ctx)
            ctx.music_state = "idle"
            current_track = None
            retry_count += 1
            if retry_count >= _MUSIC_MAX_RETRIES:
                logger.error(
                    "Music loop failed %d times, disabling music", _MUSIC_MAX_RETRIES
                )
                ctx.music_mode = "off"
                retry_count = 0
            else:
                # Exponential backoff: 1s, 2s, 4s...
                await _music_backoff_sleep(2 ** (retry_count - 1), ctx)


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
    ctx = DaemonContext(auth_token=auth_token, port=port)

    # Track generator for music -- set module-level for handler access.
    global _track_generator
    _track_generator = TrackGenerator(_music_output_dir())

    logger.info("Starting voxd on %s:%d", host, port)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        # Start playback consumer
        consumer_task = asyncio.create_task(ctx._playback.consumer())
        logger.info("Playback consumer started")
        # Start music loop
        music_task = asyncio.create_task(_music_loop(ctx))
        logger.info("Music loop started")
        try:
            yield
        finally:
            music_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await music_task
            # Kill any lingering music subprocess.
            await _kill_music_proc(ctx)
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
