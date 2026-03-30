"""voxd -- audio server daemon.

Pure audio server. Receives synthesis requests over WebSocket,
synthesizes via configured providers, plays through speakers.
Knows nothing about MCP, hooks, projects, sessions, or Claude Code.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import importlib.resources
import json
import logging
import logging.config
import os
import secrets
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from socket import socket
from typing import TYPE_CHECKING

import typer
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from punt_vox.cache import cache_get, cache_put
from punt_vox.core import TTSClient
from punt_vox.normalize import normalize_for_speech
from punt_vox.providers import auto_detect_provider, get_provider
from punt_vox.types import AudioProviderId, AudioRequest

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8421
DEFAULT_HOST = "127.0.0.1"

# Audio deduplication window: skip identical audio within this many seconds.
_DEDUP_WINDOW_SECONDS = 5.0

# Lock to serialize os.environ mutation during synthesis with per-request API keys.
_env_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# System paths
# ---------------------------------------------------------------------------


def _data_root() -> Path:
    """Resolve system data root: Homebrew prefix on macOS, / on Linux."""
    if sys.platform == "darwin":
        try:
            prefix = subprocess.check_output(
                ["brew", "--prefix"], text=True, timeout=5
            ).strip()
            return Path(prefix)
        except (subprocess.SubprocessError, FileNotFoundError):
            return Path("/usr/local")  # fallback for non-Homebrew macOS
    return Path("/")  # type: ignore[unreachable,unused-ignore]


def _config_dir() -> Path:
    return _data_root() / "etc" / "vox"


def _log_dir() -> Path:
    return _data_root() / "var" / "log" / "vox"


def _run_dir() -> Path:
    return _data_root() / "var" / "run" / "vox"


# ---------------------------------------------------------------------------
# Logging (inline -- not importing logging_config.py)
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_MAX_BYTES = 5_242_880  # 5 MB
_LOG_BACKUP_COUNT = 5


def _configure_logging(log_dir: Path) -> None:
    """Configure logging with rotating file and stderr handlers."""
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    log_file = log_dir / "voxd.log"

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": _LOG_FORMAT,
                    "datefmt": _LOG_DATE_FORMAT,
                },
            },
            "handlers": {
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "filename": str(log_file),
                    "maxBytes": _LOG_MAX_BYTES,
                    "backupCount": _LOG_BACKUP_COUNT,
                    "encoding": "utf-8",
                    "formatter": "standard",
                    "level": "INFO",
                },
                "stderr": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "standard",
                    "level": "INFO",
                },
            },
            "root": {
                "level": "INFO",
                "handlers": ["file", "stderr"],
            },
            "loggers": {
                "boto3": {"level": "WARNING"},
                "botocore": {"level": "WARNING"},
                "urllib3": {"level": "WARNING"},
                "s3transfer": {"level": "WARNING"},
                "httpx": {"level": "WARNING"},
            },
        }
    )


# ---------------------------------------------------------------------------
# Key loading (inline -- not importing keys.py)
# ---------------------------------------------------------------------------

_PROVIDER_KEY_NAMES: frozenset[str] = frozenset(
    {
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION",
        "TTS_PROVIDER",
        "TTS_MODEL",
    }
)


def _load_keys(config_dir: Path) -> frozenset[str]:
    """Load keys.env from config dir into os.environ.

    Returns the names of variables that were loaded.
    """
    keys_file = config_dir / "keys.env"
    if not keys_file.exists():
        return frozenset()
    try:
        text = keys_file.read_text()
    except OSError as exc:
        logger.warning(
            "Could not read %s: %s -- daemon will use system TTS only",
            keys_file,
            exc,
        )
        return frozenset()
    loaded: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if key in _PROVIDER_KEY_NAMES and value and key not in os.environ:
            os.environ[key] = value
            loaded.add(key)
    return frozenset(loaded)


# ---------------------------------------------------------------------------
# Auth token management
# ---------------------------------------------------------------------------


def _read_or_create_token(run_dir: Path) -> str:
    """Read auth token from run dir, or generate a new one."""
    token_file = run_dir / "serve.token"
    if token_file.exists():
        try:
            token = token_file.read_text().strip()
        except (PermissionError, OSError) as exc:
            msg = (
                f"Cannot read auth token from {token_file}: {exc}. "
                "Fix file permissions or remove the file."
            )
            raise SystemExit(msg) from exc
        if not token:
            msg = f"Auth token file {token_file} is empty. Remove it to regenerate."
            raise SystemExit(msg)
        token_file.chmod(0o600)
        logger.info("Loaded auth token from %s", token_file)
        return token

    token = secrets.token_urlsafe(32)
    token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(token_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode())
    finally:
        os.close(fd)
    logger.info("Generated auth token at %s", token_file)
    return token


# ---------------------------------------------------------------------------
# Port file helpers
# ---------------------------------------------------------------------------


def _write_port_file(run_dir: Path, port: int) -> None:
    port_file = run_dir / "serve.port"
    port_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.write_text(str(port))
    logger.info("Wrote port file: %s (port %d)", port_file, port)


def _remove_port_file(run_dir: Path) -> None:
    port_file = run_dir / "serve.port"
    try:
        port_file.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove %s", port_file)
    logger.info("Removed port file")


def read_port_file() -> int | None:
    """Read the daemon port from the port file. Returns None if missing."""
    port_file = _run_dir() / "serve.port"
    try:
        return int(port_file.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def read_token_file() -> str | None:
    """Read the daemon auth token. Returns None if missing."""
    token_file = _run_dir() / "serve.token"
    try:
        return token_file.read_text().strip()
    except (FileNotFoundError, OSError):
        return None


# ---------------------------------------------------------------------------
# Playback item and queue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaybackItem:
    """An item in the playback queue."""

    path: Path
    request_id: str
    notify: asyncio.Event


async def _play_audio(path: Path) -> None:
    """Play an audio file using the platform player."""
    if sys.platform == "darwin":
        cmd = ["afplay", str(path)]
    else:
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        logger.warning("Audio player not found: %s", cmd[0])
        return
    except OSError as exc:
        logger.warning("Playback failed for %s: %s", path.name, exc)
        return

    try:
        await asyncio.wait_for(proc.wait(), timeout=30)
    except TimeoutError:
        logger.warning("Playback timed out after 30s for %s", path.name)
        proc.kill()


async def _playback_consumer(ctx: DaemonContext) -> None:
    """Single consumer: plays audio sequentially."""
    while True:
        item = await ctx.playback_queue.get()
        logger.info("Playback start: %s", item.path.name)
        await _play_audio(item.path)
        logger.info("Playback done: %s", item.path.name)
        item.notify.set()
        ctx.playback_queue.task_done()


# ---------------------------------------------------------------------------
# Audio deduplication
# ---------------------------------------------------------------------------


class AudioDedup:
    """In-memory dedup: skip identical audio within a time window."""

    def __init__(self, window: float = _DEDUP_WINDOW_SECONDS) -> None:
        self._window = window
        self._seen: dict[str, float] = {}

    def should_play(self, text: str, voice: str, provider: str) -> bool:
        """Return True if this audio should play (not a duplicate)."""
        payload = f"{text}\0{voice}\0{provider}"
        key = hashlib.md5(payload.encode()).hexdigest()
        now = time.monotonic()
        last = self._seen.get(key)
        if last is not None and (now - last) < self._window:
            return False
        self._seen[key] = now
        # Prune old entries
        cutoff = now - self._window * 2
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        return True


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
    ) -> None:
        self.start_time: float = time.monotonic()
        self.auth_token: str | None = auth_token
        self.port: int = port
        self.dedup = AudioDedup()
        self.client_count: int = 0
        self.playback_queue: asyncio.Queue[PlaybackItem] = asyncio.Queue()


# ---------------------------------------------------------------------------
# Chime asset resolution
# ---------------------------------------------------------------------------

_CHIME_MAP: dict[str, str] = {
    "done": "chime_done.mp3",
    "prompt": "chime_prompt.mp3",
    "acknowledge": "chime_done.mp3",
    "compact": "chime_done.mp3",
    "subagent": "chime_done.mp3",
    "farewell": "chime_done.mp3",
    "tests-pass": "chime_tests_pass.mp3",
    "tests-fail": "chime_tests_fail.mp3",
    "lint-pass": "chime_lint_pass.mp3",
    "lint-fail": "chime_lint_fail.mp3",
    "git-push-ok": "chime_git_push_ok.mp3",
    "merge-conflict": "chime_merge_conflict.mp3",
}


def _resolve_chime(signal: str) -> Path | None:
    """Resolve a chime signal name to a bundled asset path."""
    filename = _CHIME_MAP.get(signal)
    if filename is None:
        return None
    try:
        ref = importlib.resources.files("punt_vox.assets").joinpath(filename)
        # as_file returns a context manager; we need the actual path.
        # For installed packages the file is already on disk.
        path = Path(str(ref))
        if path.exists():
            return path
    except (TypeError, FileNotFoundError):
        pass
    return None


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
    speaker_boost: bool | None,
    api_key: str | None,
) -> Path:
    """Run TTS synthesis and return the output path.

    Handles API key injection, provider construction, and caching.
    Raises on failure.
    """
    resolved_voice = voice or ""

    normalized = normalize_for_speech(text)
    if vibe_tags:
        normalized = f"[{vibe_tags}] {normalized}"

    # Check cache first
    cached = cache_get(normalized, resolved_voice, provider_name)
    if cached is not None:
        return cached

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
            provider = get_provider(provider_name, config_path=None, model=model)
            request = _build_audio_request(
                normalized,
                voice,
                language,
                rate,
                stability,
                similarity,
                style,
                speaker_boost,
                provider_name,
            )
            client = TTSClient(provider)

            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                output_path = Path(tmp.name)

            await asyncio.to_thread(client.synthesize, request, output_path)

            # Cache the result
            cache_put(normalized, resolved_voice, provider_name, output_path)
            return output_path
        finally:
            # Restore API key
            if env_key_name and old_key is not None:
                os.environ[env_key_name] = old_key
            elif env_key_name and api_key:
                os.environ.pop(env_key_name, None)


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

    # Dedup check
    resolved_voice = voice or ""
    if not ctx.dedup.should_play(text, resolved_voice, provider_name):
        logger.info("Dedup: skipping duplicate synthesis for id=%s", request_id)
        await websocket.send_json({"type": "done", "id": request_id})
        return

    logger.info(
        "Synthesize: id=%s provider=%s voice=%s chars=%d",
        request_id,
        provider_name,
        resolved_voice,
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
            speaker_boost,
            api_key,
        )
    except Exception as exc:
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
    await websocket.send_json({"type": "done", "id": request_id})


async def _handle_record(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
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
            speaker_boost,
            api_key,
        )
    except Exception as exc:
        logger.exception("Record synthesis failed for id=%s", request_id)
        await websocket.send_json(
            {"type": "error", "id": request_id, "message": str(exc)}
        )
        return

    audio_data = output_path.read_bytes()
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
    path = _resolve_chime(signal)
    if path is None:
        logger.warning("Unknown chime signal: %s", signal)
        await websocket.send_json(
            {"type": "error", "id": "", "message": f"unknown chime: {signal}"}
        )
        return

    # Dedup chimes too
    if not ctx.dedup.should_play(f"chime:{signal}", "", ""):
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
    await websocket.send_json({"type": "done", "id": f"chime:{signal}"})


async def _handle_voices(
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'voices' message: list available voices."""
    provider_name = _parse_optional_str(msg, "provider") or auto_detect_provider()

    try:
        provider = get_provider(provider_name, config_path=None)
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
    msg: dict[str, object],
    websocket: WebSocket,
    ctx: DaemonContext,
) -> None:
    """Handle a 'health' message over WebSocket."""
    from punt_vox.providers import auto_detect_provider

    uptime = time.monotonic() - ctx.start_time
    await websocket.send_json(
        {
            "type": "health",
            "status": "ok",
            "uptime_seconds": round(uptime, 1),
            "queued": ctx.playback_queue.qsize(),
            "port": ctx.port,
            "active_sessions": ctx.client_count,
            "provider": auto_detect_provider(),
        }
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
    """Health check endpoint."""
    from punt_vox.providers import auto_detect_provider

    ctx: DaemonContext = request.app.state.ctx
    uptime = time.monotonic() - ctx.start_time
    return JSONResponse(
        {
            "status": "ok",
            "uptime_seconds": round(uptime, 1),
            "queued": ctx.playback_queue.qsize(),
            "port": ctx.port,
            "active_sessions": ctx.client_count,
            "provider": auto_detect_provider(),
        }
    )


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
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Listen host"),
) -> None:
    """Start the voxd audio server daemon."""
    run_dir = _run_dir()
    config_dir = _config_dir()
    log_dir = _log_dir()

    # Configure logging
    _configure_logging(log_dir)

    # Load provider keys
    loaded_keys = _load_keys(config_dir)
    if loaded_keys:
        logger.info("Loaded provider keys from %s: %s", config_dir, sorted(loaded_keys))
    else:
        logger.info("No provider keys loaded from %s", config_dir)

    # Auth token
    auth_token = _read_or_create_token(run_dir)
    ctx = DaemonContext(auth_token=auth_token, port=port)

    logger.info("Starting voxd on %s:%d", host, port)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        # Start playback consumer
        consumer_task = asyncio.create_task(_playback_consumer(ctx))
        logger.info("Playback consumer started")
        try:
            yield
        finally:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
            _remove_port_file(run_dir)
            logger.info("voxd stopped")

    app = build_app(ctx, lifespan=lifespan)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    # Write port file after bind
    original_startup = server.startup

    async def _startup_with_port_file(
        sockets: list[socket] | None = None,
    ) -> None:
        await original_startup(sockets=sockets)
        if server.servers and server.servers[0].sockets:
            actual_port = server.servers[0].sockets[0].getsockname()[1]
            _write_port_file(run_dir, actual_port)
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
