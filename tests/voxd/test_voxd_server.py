"""Tests for voxd's Uvicorn transport."""

from __future__ import annotations

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
