"""voxd -- audio server daemon.

Pure audio server. Receives synthesis requests over WebSocket,
synthesizes via configured providers, plays through speakers.
Knows nothing about MCP, hooks, projects, sessions, or Claude Code.
"""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from socket import socket
from typing import TYPE_CHECKING, cast

import typer
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute

from punt_vox.paths import ensure_user_dirs
from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.config import (  # pyright: ignore[reportPrivateUsage]
    DaemonConfig,
    _config_dir,
    _install_token_redact_filter,
    _log_dir,
    _run_dir,
)
from punt_vox.voxd.dedup import ChimeDedup, OnceDedup
from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.music_scheduler import MusicScheduler
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.router import WebSocketRouter
from punt_vox.voxd.synthesis import SynthesisPipeline
from punt_vox.voxd.track_generator import TrackGenerator

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8421
DEFAULT_HOST = "127.0.0.1"

# Module-level SynthesisPipeline, set by main() on startup.
_pipeline: SynthesisPipeline | None = None

# Module-level TrackGenerator, set by main() at daemon startup.
_track_generator: TrackGenerator | None = None

# Module-level WebSocketRouter, set by main() at daemon startup.
_router: WebSocketRouter | None = None


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
        health: DaemonHealth | None = None,
    ) -> None:
        self._playback: PlaybackQueue = playback or PlaybackQueue()
        self._music: MusicScheduler = music or MusicScheduler(
            TrackGenerator(_music_output_dir())
        )
        self.auth_token: str | None = auth_token
        self.port: int = port
        self.chime_dedup = ChimeDedup()
        self.once_dedup = OnceDedup()
        self.client_count: int = 0
        self._health: DaemonHealth = health or DaemonHealth(
            self._playback, lambda: self.client_count, port
        )

    # -- Delegation properties for DaemonHealth ------------------------------

    @property
    def start_time(self) -> float:
        """Return the monotonic timestamp when the daemon started."""
        return self._health.start_time

    @property
    def daemon_version(self) -> str:
        """Return the cached daemon version string."""
        return self._health.daemon_version

    @daemon_version.setter
    def daemon_version(self, val: str) -> None:
        self._health.set_daemon_version(val)

    # -- Delegation properties for PlaybackQueue -----------------------------

    @property
    def playback_queue(self) -> asyncio.Queue[PlaybackQueue]:
        """Return the underlying asyncio.Queue from PlaybackQueue."""
        return self._playback._queue  # type: ignore[return-value]

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


# Backward-compatible aliases for names that moved to synthesis.py.
# Re-exported via __init__.py and referenced by existing tests.
_apply_vibe_for_synthesis = SynthesisPipeline.apply_vibe_for_synthesis
_model_supports_expressive_tags = SynthesisPipeline.model_supports_expressive_tags


def _get_pipeline(ctx: DaemonContext | None = None) -> SynthesisPipeline:
    """Return the module-level SynthesisPipeline.

    Initialized eagerly in main(). Falls back to ctx if called before main()
    (e.g., in tests).
    """
    global _pipeline
    if _pipeline is None:
        if ctx is None:
            msg = "_pipeline not initialized — call main() first"
            raise RuntimeError(msg)
        _pipeline = SynthesisPipeline(playback_mutex=ctx._playback.mutex)
    return _pipeline


def _record_playback_result(
    ctx: DaemonContext,
    *,
    path: Path,
    rc: int,
    elapsed: float,
    stderr: str,
) -> None:
    """Update ctx.last_playback with a freshly-observed playback result."""
    import time

    ctx.last_playback = {
        "file": str(path),
        "rc": rc,
        "elapsed_s": round(elapsed, 4),
        "stderr": stderr,
        "ts": time.time(),
    }


async def _synthesize_to_file(  # pyright: ignore[reportUnusedFunction]
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
    pipeline = _get_pipeline()
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


async def _try_direct_play(  # pyright: ignore[reportUnusedFunction]
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


async def _handle_music_on(  # pyright: ignore[reportUnusedFunction]
    msg: dict[str, object],
    websocket: object,
    ctx: DaemonContext,
) -> None:
    """Backward-compat wrapper: delegates to the router's handler via MusicScheduler."""
    # Build a minimal router on the fly for backward-compat callers.
    tg = _get_track_generator()
    pipeline = _get_pipeline(ctx)
    router = WebSocketRouter(
        synthesis=pipeline,
        playback=ctx._playback,
        music=ctx._music,
        chime_dedup=ctx.chime_dedup,
        once_dedup=ctx.once_dedup,
        chimes=ChimeResolver(),
        health=ctx._health,
        auth_token=ctx.auth_token,
        track_generator=tg,
    )
    await router._handle_music_on(msg, cast("WebSocket", websocket))


def _auto_track_name(ctx: DaemonContext) -> str:  # pyright: ignore[reportUnusedFunction]
    """Derive a short auto-name from vibe + style + YYYYMMDD-HHMM."""
    vibe, _ = ctx.music_vibe
    style = ctx.music_style
    return _get_track_generator().auto_track_name(vibe, style)


async def _kill_music_proc(ctx: DaemonContext) -> None:  # pyright: ignore[reportUnusedFunction]
    """Kill the current music subprocess. Delegates to MusicScheduler."""
    await ctx._music.kill_proc()


async def _music_loop(ctx: DaemonContext) -> None:  # pyright: ignore[reportUnusedFunction]
    """Background task: generate and loop music tracks. Delegates to MusicScheduler."""
    await ctx._music.loop()


def _health_payload_minimal(ctx: DaemonContext) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
    """Return the public health payload safe for unauthenticated callers."""
    return ctx._health.minimal_payload()


def _health_payload_full(ctx: DaemonContext) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
    """Return the full diagnostic health payload for authenticated callers."""
    return ctx._health.full_payload()


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
    router: WebSocketRouter | None = None,
) -> Starlette:
    """Build the Starlette ASGI application.

    Exposed as a factory so tests can construct the app without starting
    uvicorn.
    """
    if ctx is None:
        ctx = DaemonContext()

    if router is None:
        # Create a minimal router for backward compatibility (tests).
        pipeline = _pipeline or SynthesisPipeline(playback_mutex=ctx._playback.mutex)
        tg = _track_generator or TrackGenerator(_music_output_dir())
        router = WebSocketRouter(
            synthesis=pipeline,
            playback=ctx._playback,
            music=ctx._music,
            chime_dedup=ctx.chime_dedup,
            once_dedup=ctx.once_dedup,
            chimes=ChimeResolver(),
            health=ctx._health,
            auth_token=ctx.auth_token,
            track_generator=tg,
        )

    routes: list[Route | WebSocketRoute] = [
        Route("/health", _health_route, methods=["GET"]),
        WebSocketRoute("/ws", router.handle_connection),
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
    playback = PlaybackQueue()
    health = DaemonHealth(playback, lambda: router.client_count, port)
    ctx = DaemonContext(
        auth_token=auth_token,
        port=port,
        playback=playback,
        music=scheduler,
        health=health,
    )

    # Initialize synthesis pipeline eagerly with the real playback mutex.
    global _pipeline
    _pipeline = SynthesisPipeline(playback_mutex=ctx._playback.mutex)

    # Build the WebSocket router with all dependencies.
    global _router
    router = WebSocketRouter(
        synthesis=_pipeline,
        playback=playback,
        music=scheduler,
        chime_dedup=ctx.chime_dedup,
        once_dedup=ctx.once_dedup,
        chimes=ChimeResolver(),
        health=health,
        auth_token=auth_token,
        track_generator=_track_generator,
    )
    _router = router

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

    app = build_app(ctx, lifespan=lifespan, router=router)

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
