"""Tests for voxd's Uvicorn transport."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
import uvicorn
from starlette.applications import Starlette

from punt_vox.voxd.server import DaemonServer

if TYPE_CHECKING:
    from socket import socket


@pytest.mark.parametrize("startup_aborted", [False, True])
async def test_no_port_published_when_startup_does_not_bind(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    startup_aborted: bool,
) -> None:
    async def start_without_binding(
        server: uvicorn.Server, sockets: list[socket] | None = None
    ) -> None:
        server.should_exit = startup_aborted
        if not startup_aborted:
            server.servers = []

    monkeypatch.setattr(uvicorn.Server, "startup", start_without_binding)
    published: list[int] = []
    await DaemonServer(Starlette(), "127.0.0.1", 0, published.append).serve()

    assert not published
    assert "Server started but no bound sockets; shutting down" in caplog.text


async def test_a_stop_signal_during_serve_ends_the_process_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGTERM shuts uvicorn down and is then ignored, not re-raised fatally.

    uvicorn re-raises the signal it stopped on after its shutdown, under
    whatever handler it found installed. Serve installs SIG_IGN for the
    duration so that re-raise is harmless, then restores the handler.
    """
    before = signal.getsignal(signal.SIGTERM)
    seen: list[object] = []

    async def stop_on_a_signal(server: uvicorn.Server, sockets: object = None) -> None:
        del sockets
        seen.append(signal.getsignal(signal.SIGTERM))
        server.handle_exit(signal.SIGTERM, None)

    monkeypatch.setattr(uvicorn.Server, "startup", stop_on_a_signal)
    await DaemonServer(Starlette(), "127.0.0.1", 0, lambda _port: None).serve()

    # uvicorn held the signal while serving (its own bound handler), the
    # process is still here after the re-raise, and the handler from before
    # serve() is back.
    assert len(seen) == 1
    assert getattr(seen[0], "__func__", None) is uvicorn.Server.handle_exit
    assert signal.getsignal(signal.SIGTERM) == before


async def test_a_stop_signal_before_uvicorn_installs_its_handler_still_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signal in the window before uvicorn's handler is recorded, not lost.

    The handler serve installs is uvicorn's own, so the stop lands in the
    server's state and startup sees it; main_loop never runs.
    """
    looped: list[bool] = []

    async def main_loop(server: uvicorn.Server) -> None:
        del server
        looped.append(True)

    async def startup(server: uvicorn.Server, sockets: object = None) -> None:
        del server, sockets

    monkeypatch.setattr(uvicorn.Server, "main_loop", main_loop)
    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    daemon = DaemonServer(Starlette(), "127.0.0.1", 0, lambda _port: None)
    with daemon._stop_signals_end_normally():  # pyright: ignore[reportPrivateUsage]
        os.kill(os.getpid(), signal.SIGTERM)
        await daemon._server.serve()  # pyright: ignore[reportPrivateUsage]
    assert looped == []


async def test_a_child_spawned_during_serve_can_be_stopped_by_sigterm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stop handler is a caught one, which resets to default across exec."""
    seen: list[str] = []

    async def spawn_then_stop(server: uvicorn.Server, sockets: object = None) -> None:
        del sockets
        probe = subprocess.run(
            [sys.executable, "-c", "import signal; print(int(signal.getsignal(15)))"],
            capture_output=True,
            text=True,
            check=True,
        )
        seen.append(probe.stdout.strip())
        server.handle_exit(signal.SIGTERM, None)

    monkeypatch.setattr(uvicorn.Server, "startup", spawn_then_stop)
    await DaemonServer(Starlette(), "127.0.0.1", 0, lambda _port: None).serve()
    assert seen == [str(int(signal.SIG_DFL))]
