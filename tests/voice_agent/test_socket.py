from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from websockets.asyncio.server import serve

from punt_vox.voxd.voice_agent import ConvAIError
from punt_vox.voxd.voice_agent._socket import Socket

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection


@pytest.mark.parametrize(
    ("url", "diagnostic"),
    [
        ("wss://[bad", "Invalid ElevenLabs signed URL"),
        ("ws://127.0.0.1:invalid/convai", "handshake failed"),
    ],
)
async def test_socket_sanitizes_invalid_urls(url: str, diagnostic: str) -> None:
    with pytest.raises(ConvAIError, match=diagnostic) as failure:
        await Socket.open(f"{url}?signature=private", allow_loopback=True)
    assert "private" not in str(failure.value)


async def test_remote_close_reports_code_but_not_vendor_reason() -> None:
    async def close_with_error(socket: ServerConnection) -> None:
        await socket.close(code=1011, reason="private vendor reason")

    async with serve(close_with_error, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        socket = await Socket.open(f"ws://127.0.0.1:{port}", allow_loopback=True)
        async with socket:
            with pytest.raises(ConvAIError, match="close code 1011") as failure:
                await Socket.receive(socket)
            assert "private" not in str(failure.value)
