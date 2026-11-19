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
from typing import Any, Self, final

import websockets
import websockets.asyncio.client

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdProtocolError,
    VoxdRejectionError,
)
from punt_vox.client_fetch import FetchAssembly

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

    async def reply(self, timeout: float) -> dict[str, Any]:
        """Receive and decode the request's single reply within *timeout*.

        Raises:
            VoxdConnectionError: If the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            AttributeError: If the daemon sends a non-object JSON reply.
            VoxdProtocolError: If JSON is invalid or the deadline expires.
        """
        timeout_msg = f"Timeout waiting for response to '{self.op}'"
        return self.decode(await self._recv_within(timeout, timeout_msg))

    async def recv_before(self, deadline: float, expected: str) -> object:
        """Receive one frame before *deadline*, or raise a timeout protocol error.

        *expected* names the frame awaited; the timeout names it in the op.

        Raises:
            VoxdConnectionError: If the connection is lost.
            VoxdProtocolError: If the deadline expires.
        """
        timeout_msg = f"Timeout waiting for '{expected}' in '{self.op}'"
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise VoxdProtocolError(timeout_msg)
        return await self._recv_within(remaining, timeout_msg)

    async def reassemble(self, deadline: float) -> bytes:
        """Consume ``fetch_begin`` -> ordered ``chunk``* -> ``fetch_end`` into bytes.

        Verifies the frame order, the declared byte count, and the sha256 before
        returning, so a short, reordered, or corrupted stream raises.

        Raises:
            VoxdConnectionError: If the connection is lost.
            VoxdRejectionError: If the daemon sends an error frame.
            AttributeError: If the daemon sends a non-object JSON frame.
            VoxdProtocolError: If a frame is invalid or out of order, the stream
                fails verification, or the deadline expires.
        """
        begin = self.decode(await self.recv_before(deadline, "fetch_begin"))
        assembly = FetchAssembly.begin(begin)
        for seq in range(assembly.chunks):
            frame = self.decode(await self.recv_before(deadline, "chunk"))
            assembly.append(frame, seq)
        end = self.decode(await self.recv_before(deadline, "fetch_end"))
        return assembly.finish(end)

    async def _recv_within(self, timeout: float, timeout_msg: str) -> object:
        """Receive one frame within *timeout*, wrapping every transport failure.

        A timeout is a protocol failure; a dropped socket is a connection one.
        """
        try:
            return await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        except TimeoutError as exc:
            raise VoxdProtocolError(timeout_msg) from exc
        except (websockets.exceptions.WebSocketException, OSError) as exc:
            raise VoxdConnectionError(self._failure(exc)) from exc

    def _failure(self, exc: Exception) -> str:
        """Describe a lost/closed connection for a wrapped transport error."""
        return f"connection to voxd lost during '{self.op}': {exc}"

    @staticmethod
    def decode(raw: object) -> dict[str, Any]:
        """Parse an object frame, rejecting invalid JSON and daemon error replies.

        Raises:
            VoxdProtocolError: If the frame is not valid JSON.
            VoxdRejectionError: If the frame is a daemon ``error`` reply.
            AttributeError: If the frame is valid JSON but not an object.
        """
        try:
            resp: dict[str, Any] = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise VoxdProtocolError(f"invalid JSON from voxd: {exc}") from exc
        if resp.get("type") == "error":
            raise VoxdRejectionError(str(resp.get("message", "unknown error")))
        return resp
