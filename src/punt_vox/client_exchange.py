"""One request's frames on a voxd connection.

:class:`VoxdExchange` pairs a connection with the request it carries: it sends
the request, then reads that request's replies within a deadline. Every
transport failure is wrapped as a :class:`~punt_vox.VoxError` naming the op, so
a dropped socket or a silent daemon never escapes a CLI command's
``(VoxdConnectionError, VoxdProtocolError)`` catch as a raw traceback. The
connection itself -- connecting, reconnecting, discarding -- is owned by
:class:`~punt_vox.client_transport.VoxdTransport`.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Self, cast, final

import websockets
import websockets.asyncio.client

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdProtocolError,
    VoxdRejectionError,
)
from punt_vox.client_fetch import FetchVerifier

if TYPE_CHECKING:
    from typing import IO

__all__ = ["VoxdExchange"]


@final
class VoxdExchange:
    """Send one request on a voxd connection and read its replies."""

    __slots__ = ("_msg", "_ws")

    _ws: websockets.asyncio.client.ClientConnection
    _msg: dict[str, object]

    def __new__(
        cls,
        ws: websockets.asyncio.client.ClientConnection,
        msg: dict[str, object],
    ) -> Self:
        """Create an exchange carrying *msg* on *ws*."""
        self = super().__new__(cls)
        self._ws = ws
        self._msg = msg
        return self

    @property
    def connection(self) -> websockets.asyncio.client.ClientConnection:
        """The connection this exchange's request and replies travel on."""
        return self._ws

    @property
    def op(self) -> str:
        """The request's ``type``, as error messages name it."""
        return str(self._msg.get("type", ""))

    async def send(self) -> None:
        """Send the request as one JSON frame, wrapping a lost connection.

        Raises:
            VoxdConnectionError: If the connection is lost.
        """
        try:
            await self._ws.send(json.dumps(self._msg))
        except (websockets.exceptions.WebSocketException, OSError) as exc:
            raise VoxdConnectionError(self._failure(exc)) from exc

    # Reply fields vary by request; this boundary only validates the envelope.
    async def reply(self, timeout: float) -> dict[str, object]:
        """Receive and decode the request's single reply within *timeout*.

        Raises:
            VoxdConnectionError: If the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object or times out.
        """
        timeout_msg = f"Timeout waiting for response to '{self.op}'"
        return await self._recv_within(timeout, timeout_msg)

    # Stream frames have different fields depending on the frame type.
    async def recv_before(self, deadline: float, expected: str) -> dict[str, object]:
        """Receive and decode one frame before *deadline*.

        *expected* names the frame awaited; the timeout names it in the op.

        Raises:
            VoxdConnectionError: If the connection is lost.
            VoxdRejectionError: If the daemon sends an error frame.
            VoxdProtocolError: If the frame is not a JSON object or times out.
        """
        timeout_msg = f"Timeout waiting for '{expected}' in '{self.op}'"
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise VoxdProtocolError(timeout_msg)
        return await self._recv_within(remaining, timeout_msg)

    async def receive_fetch(self, deadline: float, sink: IO[bytes]) -> int:
        """Stream ``fetch_begin`` -> ordered ``chunk``* -> ``fetch_end`` to *sink*.

        Verifies the frame order, the declared byte count, and the sha256 before
        returning the byte count. A failed fetch may leave partial data in *sink*.

        Raises:
            VoxdConnectionError: If the connection is lost.
            VoxdRejectionError: If the daemon sends an error frame.
            VoxdProtocolError: If a frame is invalid or out of order, the stream
                fails verification, or the deadline expires.
            OSError: If writing the chunk fails or is incomplete.
        """
        verifier = FetchVerifier.begin(await self.recv_before(deadline, "fetch_begin"))
        for seq in range(verifier.chunks):
            chunk = verifier.decode_chunk(
                await self.recv_before(deadline, "chunk"), seq
            )
            if sink.write(chunk) != len(chunk):
                raise OSError(f"incomplete write for fetch chunk {seq}")
        return verifier.finish(await self.recv_before(deadline, "fetch_end"))

    # The receive path is shared by single replies and streamed frames.
    async def _recv_within(self, timeout: float, timeout_msg: str) -> dict[str, object]:
        """Receive and decode one frame, wrapping every transport failure.

        A timeout is a protocol failure; a dropped socket is a connection one.
        """
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        except TimeoutError as exc:
            raise VoxdProtocolError(timeout_msg) from exc
        except (websockets.exceptions.WebSocketException, OSError) as exc:
            raise VoxdConnectionError(self._failure(exc)) from exc
        return self.decode(raw)

    def _failure(self, exc: Exception) -> str:
        """Describe a lost/closed connection for a wrapped transport error."""
        return f"connection to voxd lost during '{self.op}': {exc}"

    @staticmethod
    # Wire objects have string keys, but field schemas depend on the operation.
    def decode(raw: object) -> dict[str, object]:
        """Parse an object frame, rejecting invalid JSON and daemon error replies.

        Raises:
            VoxdProtocolError: If the frame is not a JSON object.
            VoxdRejectionError: If the frame is a daemon ``error`` reply.
        """
        try:
            parsed: object = json.loads(str(raw))
        except (ValueError, RecursionError) as exc:
            raise VoxdProtocolError(f"invalid JSON from voxd: {exc}") from exc
        if not isinstance(parsed, dict):
            raise VoxdProtocolError("expected a JSON object from voxd")
        resp = cast("dict[str, object]", parsed)  # JSON object keys are always strings.
        if resp.get("type") == "error":
            raise VoxdRejectionError(str(resp.get("message", "unknown error")))
        return resp
