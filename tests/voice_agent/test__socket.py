from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from websockets.asyncio.server import serve

from punt_vox.voxd.voice_agent import ConvAIError
from punt_vox.voxd.voice_agent._socket import Socket

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Request, Response


async def test_signed_websocket_redirect_is_rejected() -> None:
    requests: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        await socket.close()

    def redirect(socket: ServerConnection, request: Request) -> Response:
        requests.append(request.path)
        response = socket.respond(302, "Redirect")
        response.headers["Location"] = "/redirected?signature=credential"
        return response

    async with (
        asyncio.timeout(5),
        serve(peer, "127.0.0.1", 0, process_request=redirect) as server,
    ):
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(ConvAIError, match="WebSocket handshake failed") as error:
            await Socket.open(f"ws://127.0.0.1:{port}/convai?signature=credential")
    assert requests == ["/convai?signature=credential"]
    assert "credential" not in str(error.value)
    assert error.value.__suppress_context__
