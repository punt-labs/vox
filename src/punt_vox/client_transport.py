"""The voxd WebSocket transport: one connection and its framing.

:class:`VoxdTransport` owns one connection to voxd -- connecting to the URI
its :class:`~punt_vox.client_endpoint.VoxdEndpoint` resolves, and closing;
and the request/reply exchanges the RPC surface in :mod:`punt_vox.client` is
built on. The frames of one exchange are read by
:class:`~punt_vox.client_exchange.VoxdExchange`.
Split out of :mod:`punt_vox.client` so connection lifecycle and wire framing
live apart from the request vocabulary.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from typing import Any, Self

import websockets
import websockets.asyncio.client

from punt_vox.client_connect_failure import ConnectFailure
from punt_vox.client_endpoint import VoxdEndpoint
from punt_vox.client_exchange import VoxdExchange

logger = logging.getLogger(__name__)

__all__ = ["VoxdTransport"]


class VoxdTransport:
    """Own the voxd WebSocket: connect/close and framed send/recv.

    The URI comes from :class:`~punt_vox.client_endpoint.VoxdEndpoint`; the
    RPC surface that frames requests lives in :class:`VoxClient`.
    """

    __slots__ = ("_endpoint", "_ws")

    _endpoint: VoxdEndpoint
    _ws: websockets.asyncio.client.ClientConnection | None

    def __new__(
        cls,
        host: str | None,
        port: int | None,
        token: str | None,
    ) -> Self:
        """Create a voxd transport."""
        self = super().__new__(cls)
        self._endpoint = VoxdEndpoint(host, port, token)
        self._ws = None
        return self

    async def connect(self) -> websockets.asyncio.client.ClientConnection:
        """Connect to voxd and return the connection, which this transport keeps.

        Never through a proxy: the URI carries the token, and websockets
        otherwise honours ``http_proxy`` / ``all_proxy`` from the environment,
        loopback included, so ``GET /ws?token=...`` would cross the proxy.

        Raises:
            VoxdConnectionError: If the endpoint is unavailable, unreadable, or rejects
                the handshake.
        """
        uri = self._endpoint.uri()
        try:
            # all_errors: a host with several addresses fails as one
            # ExceptionGroup of per-address errors, so a refusal on every
            # address can be told from one that timed out or was unreachable.
            self._ws = await websockets.asyncio.client.connect(
                uri, all_errors=True, proxy=None
            )
        except (
            OSError,
            ExceptionGroup,
            ValueError,
            websockets.exceptions.WebSocketException,
        ) as exc:
            # A WebSocketException is voxd (or whatever holds the port)
            # answering the handshake with a refusal of its own -- HTTP 403 for
            # a bad token or Origin, or not speaking HTTP at all. A ValueError
            # is urllib failing to parse the URI (a port out of range).
            failure = ConnectFailure(uri.split("?")[0], exc)
            raise failure.error() from failure.chainable(exc)
        return self._ws

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    def _discard(self, ws: websockets.asyncio.client.ClientConnection) -> None:
        """Drop *ws* and, if it is still the active connection, forget it.

        The socket is aborted, not closed: a closing handshake against a peer
        that stopped answering would wait out the close timeout after the
        caller's own deadline has passed, and the connection is abandoned
        either way. Only the failed exchange's own socket is dropped: if
        another request has already reconnected, its connection is left alone.
        """
        if self._ws is ws:
            self._ws = None
        ws.transport.abort()

    async def _ensure_connected(self) -> websockets.asyncio.client.ClientConnection:
        """Return the active connection, reconnecting if needed.

        On reconnect, re-reads port and token files (daemon may have
        restarted on a different port).
        """
        ws = self._ws
        if ws is not None:
            try:
                await ws.ping()
                return ws
            except (OSError, websockets.exceptions.WebSocketException):
                # Connection is dead; fall through to reconnect.
                self._ws = None

        # The endpoint re-reads port and token from disk unless given.
        return await self.connect()

    @contextlib.asynccontextmanager
    async def _exchange(self, msg: dict[str, object]) -> AsyncGenerator[VoxdExchange]:
        """Send *msg* and yield the exchange its replies are read through.

        If the exchange fails -- the deadline expires, the caller cancels, a
        frame is malformed or out of order -- the connection is discarded before
        the error propagates. voxd still answers a request the client gave up
        on, and on a reused connection that late reply (or the rest of a chunk
        stream) would be the first frame the NEXT request reads, handing it
        another request's answer. The next request reconnects to a clean
        connection via :meth:`_ensure_connected` instead.
        """
        exchange = VoxdExchange(await self._ensure_connected(), msg)
        try:
            await exchange.send()
            yield exchange
        except BaseException:
            self._discard(exchange.connection)
            raise

    async def send_and_recv(
        self,
        msg: dict[str, object],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """Send a JSON message and wait for a single JSON response.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        async with self._exchange(msg) as exchange:
            return await exchange.reply(timeout)

    async def fetch_stream(self, msg: dict[str, object], *, timeout: float) -> bytes:
        """Send a fetch request and reassemble the chunked stream into bytes.

        Consumes ``fetch_begin`` -> ordered ``chunk``* -> ``fetch_end``, appending
        one frame at a time so client memory holds only the reassembled file plus
        a single chunk. The running total is checked against the declared byte
        count and the sha256 is verified, so a short, reordered, or corrupted
        stream raises. A mid-stream ``error`` frame surfaces as a
        :class:`VoxdRejectionError`, and the partial buffer is discarded.

        On ANY mid-stream fault the connection is discarded (see
        :meth:`_exchange`): the daemon may still be sending this fetch's
        remaining chunk frames, which would otherwise desync the next request
        on the reused connection. A fault leaves no partial file and no stale
        frames behind.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        async with self._exchange(msg) as exchange:
            deadline = asyncio.get_running_loop().time() + timeout
            return await exchange.reassemble(deadline)

    async def send_and_drain(
        self,
        msg: dict[str, object],
        *,
        timeout: float,
        terminal_type: str = "done",
        early_terminal: str | None = None,
    ) -> list[dict[str, Any]]:
        """Send a message and collect responses until a terminal response arrives.

        Stops when the response type matches *terminal_type* or, when set,
        *early_terminal*. Used for synthesize/chime: the server sends
        'playing' once synthesis is enqueued, then 'done' when playback
        finishes. Passing early_terminal='playing' lets the client return
        as soon as the audio is queued without waiting for playback.
        Dedup short-circuits still send 'done' directly (no 'playing'),
        so terminal_type='done' handles that path correctly.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        responses: list[dict[str, Any]] = []
        expected = early_terminal or terminal_type
        async with self._exchange(msg) as exchange:
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                resp = await exchange.recv_before(deadline, expected)
                responses.append(resp)
                resp_type = resp.get("type")
                if resp_type == terminal_type:
                    return responses
                if resp_type == expected:
                    # Server will still send terminal_type (e.g., 'done' after
                    # 'playing'). Discard the connection so that stale message
                    # cannot be consumed by the next request on a reused one.
                    self._discard(exchange.connection)
                    return responses
