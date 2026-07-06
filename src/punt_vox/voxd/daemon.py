"""VoxDaemon -- composition root for the voxd audio server."""
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
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from starlette.requests import Request

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
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.programs.wiring import ProgramSubsystem
from punt_vox.voxd.router import WebSocketRouter
from punt_vox.voxd.speech_handlers import RecordHandler, SynthesizeHandler
from punt_vox.voxd.synthesis import SynthesisPipeline
from punt_vox.voxd.system_handlers import ChimeHandler, HealthHandler, VoicesHandler
from punt_vox.voxd.types import MessageHandler

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8421
DEFAULT_HOST = "127.0.0.1"

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "VoxDaemon",
    "build_app",
    "cli",
    "entrypoint",
    "read_port_file",
    "read_token_file",
]


class VoxDaemon:
    """Composition root that wires all daemon subsystems and runs the server."""

    __slots__ = (
        "_config",
        "_health",
        "_playback",
        "_programs",
        "_router",
        "_synthesis",
    )

    _config: DaemonConfig
    _health: DaemonHealth
    _playback: PlaybackQueue
    _programs: ProgramSubsystem
    _router: WebSocketRouter
    _synthesis: SynthesisPipeline

    def __new__(
        cls,
        config: DaemonConfig,
        playback: PlaybackQueue,
        synthesis: SynthesisPipeline,
        programs: ProgramSubsystem,
        health: DaemonHealth,
        router: WebSocketRouter,
    ) -> Self:
        self = super().__new__(cls)
        self._config = config
        self._playback = playback
        self._synthesis = synthesis
        self._programs = programs
        self._health = health
        self._router = router
        return self

    def build_app(self) -> Starlette:
        """Build the Starlette ASGI application with lifespan management."""
        return VoxDaemon._starlette(
            health=self._health,
            router=self._router,
            lifespan=self._lifespan,
        )

    async def run(self, host: str, port: int) -> None:
        """Start uvicorn and serve until shutdown."""
        app = self.build_app()

        if host not in ("127.0.0.1", "::1", "localhost"):
            logger.warning(
                "Binding to %s -- voxd is accessible from the network. "
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

        original_startup = server.startup
        daemon_cfg = self._config

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
        await server.serve()

    @asynccontextmanager
    async def _lifespan(self, _app: Starlette) -> AsyncGenerator[None]:
        """Run the playback consumer, the control writer, and the playback loop.

        The Programs subsystem contributes two background tasks: the single
        control-channel writer (the sole mutator of the Program, O2) and the
        playback loop that plays and advances Parts. Both ride the daemon's
        lifetime and are cancelled on shutdown.
        """
        self._programs.log_legacy_hint(VoxDaemon._legacy_tracks_dir())
        service = self._programs.service
        consumer_task = asyncio.create_task(self._playback.consumer())
        control_task = asyncio.create_task(service.serve_control())
        playback_task = asyncio.create_task(service.run_playback())
        logger.info("Playback consumer, control writer, and playback loop started")
        try:
            yield
        finally:
            await VoxDaemon._cancel(playback_task)
            await VoxDaemon._cancel(control_task)
            service.shutdown()
            await VoxDaemon._cancel(consumer_task)
            with contextlib.suppress(Exception):
                self._config.remove_port_file()
            logger.info("voxd stopped")

    @staticmethod
    async def _cancel(task: asyncio.Task[None]) -> None:
        """Cancel a background task and await its exit, swallowing the teardown."""
        task.cancel()
        with contextlib.suppress(Exception):
            await task

    @staticmethod
    def read_port_file() -> int | None:
        """Read the daemon port from the port file."""
        return DaemonConfig.read_port_file(_run_dir())

    @staticmethod
    def read_token_file() -> str | None:
        """Read the daemon auth token."""
        return DaemonConfig.read_token_file(_run_dir())

    @staticmethod
    def _programs_root() -> Path:
        """Return the root directory under which saved Programs live."""
        from punt_vox.dirs import default_output_dir

        return default_output_dir() / "programs"

    @staticmethod
    def _legacy_tracks_dir() -> Path:
        """Return the legacy flat ``tracks/`` directory (migration source)."""
        from punt_vox.dirs import music_output_dir

        return music_output_dir()

    @staticmethod
    def _health_handler(
        health: DaemonHealth,
    ) -> Callable[[Request], object]:
        """Return an async handler that serves the minimal health payload."""

        async def _handler(_request: Request) -> JSONResponse:
            return JSONResponse(health.minimal_payload())

        return _handler

    @staticmethod
    def _starlette(
        *,
        health: DaemonHealth,
        router: WebSocketRouter,
        lifespan: (
            Callable[[Starlette], AbstractAsyncContextManager[None]] | None
        ) = None,
    ) -> Starlette:
        """Build a Starlette app from pre-wired components."""
        routes: list[Route | WebSocketRoute] = [
            Route(
                "/health",
                VoxDaemon._health_handler(health),
                methods=["GET"],
            ),
            WebSocketRoute("/ws", router.handle_connection),
        ]
        return Starlette(routes=routes, lifespan=lifespan)

    @staticmethod
    def entrypoint() -> None:
        """Console script entry point -- invokes the typer CLI."""
        cli()

    @staticmethod
    def _build_handler_dict(
        *,
        synthesis: SynthesisPipeline,
        playback: PlaybackQueue,
        programs: ProgramSubsystem,
        health: DaemonHealth,
    ) -> dict[str, MessageHandler]:
        """Build the canonical handler dispatch dict (speech + system + programs)."""
        return {
            "synthesize": SynthesizeHandler(
                synthesis=synthesis,
                playback=playback,
                once_dedup=OnceDedup(),
            ),
            "record": RecordHandler(synthesis=synthesis),
            "chime": ChimeHandler(
                chimes=ChimeResolver(),
                chime_dedup=ChimeDedup(),
                playback=playback,
            ),
            "voices": VoicesHandler(),
            "health": HealthHandler(health=health),
            **programs.handlers(),
        }

    @staticmethod
    def create_app(
        *,
        playback: PlaybackQueue | None = None,
        programs: ProgramSubsystem | None = None,
        health: DaemonHealth | None = None,
        synthesis: SynthesisPipeline | None = None,
        router: WebSocketRouter | None = None,
        auth_token: str | None = None,
    ) -> Starlette:
        """Build the Starlette ASGI app for tests.

        Accepts pre-constructed subsystems. Creates defaults for anything
        not provided, wiring them together as the real daemon would.
        """
        pb = playback or PlaybackQueue()
        syn = synthesis or SynthesisPipeline(playback_mutex=pb.mutex)
        progs = programs or ProgramSubsystem(VoxDaemon._programs_root())
        hlth = health or DaemonHealth(pb, lambda: 0, 0)

        if router is None:
            handlers = VoxDaemon._build_handler_dict(
                synthesis=syn,
                playback=pb,
                programs=progs,
                health=hlth,
            )
            router = WebSocketRouter(
                handlers=handlers,
                auth_token=auth_token,
            )

        return VoxDaemon._starlette(health=hlth, router=router)


# Module-level aliases for public API backward compatibility.
read_port_file = VoxDaemon.read_port_file
read_token_file = VoxDaemon.read_token_file
build_app = VoxDaemon.create_app
entrypoint = VoxDaemon.entrypoint


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

    daemon_cfg.configure_logging()
    daemon_cfg.log_environment()

    loaded_keys = daemon_cfg.load_keys()
    if loaded_keys:
        logger.info(
            "Loaded provider keys from %s: %s",
            daemon_cfg.config_dir,
            sorted(loaded_keys),
        )
    else:
        logger.info("No provider keys loaded from %s", daemon_cfg.config_dir)

    auth_token = daemon_cfg.read_or_create_token()

    programs = ProgramSubsystem(VoxDaemon._programs_root())
    playback = PlaybackQueue()
    synthesis = SynthesisPipeline(playback_mutex=playback.mutex)

    # Health needs the router's client_count, but router needs health.
    # Use a lambda to defer the lookup.
    health = DaemonHealth(playback, lambda: ws_router.client_count, port)

    handlers = VoxDaemon._build_handler_dict(
        synthesis=synthesis,
        playback=playback,
        programs=programs,
        health=health,
    )
    ws_router = WebSocketRouter(
        handlers=handlers,
        auth_token=auth_token,
    )

    daemon = VoxDaemon(
        config=daemon_cfg,
        playback=playback,
        synthesis=synthesis,
        programs=programs,
        health=health,
        router=ws_router,
    )

    logger.info("Starting voxd on %s:%d", host, port)
    asyncio.run(daemon.run(host, port))


if __name__ == "__main__":
    cli()
