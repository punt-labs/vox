"""The voxd WebSocket transport, and the run files that locate the daemon.

:class:`VoxdTransport` owns one connection to voxd -- resolving the URI from
explicit values, the ``VOXD_*`` environment, and the run directory's
``serve.port`` / ``serve.token``; connecting and closing; and the framed
send/receive primitives the RPC surface in :mod:`punt_vox.client` is built on.
Split out of :mod:`punt_vox.client` so connection lifecycle and wire framing
live apart from the request vocabulary.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
from typing import Any, Self

import websockets
import websockets.asyncio.client

from punt_vox.client_env import DaemonEnv
from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdProtocolError,
    VoxdRejectionError,
)
from punt_vox.paths import run_dir as _user_run_dir

logger = logging.getLogger(__name__)

__all__ = ["VoxdTransport", "read_port_file", "read_token_file"]


# ---------------------------------------------------------------------------
# Path resolution — delegated to punt_vox.paths so every module agrees.
# ``~/.punt-labs/vox/run`` holds ``serve.port`` / ``serve.token``.
# ---------------------------------------------------------------------------


def read_port_file() -> int | None:
    """Read the daemon port from the port file. Returns None if missing."""
    port_file = _user_run_dir() / "serve.port"
    try:
        return int(port_file.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def read_token_file() -> str | None:
    """Read the daemon auth token. Returns None if missing."""
    token_file = _user_run_dir() / "serve.token"
    try:
        return token_file.read_text().strip()
    except (FileNotFoundError, OSError):
        return None


# ---------------------------------------------------------------------------
# Transport — owns the WebSocket connection and the wire I/O primitives.
# ---------------------------------------------------------------------------


class VoxdTransport:
    """Own the voxd WebSocket: URI resolution, connect/close, send/recv.

    Split out of :class:`VoxClient` so the transport (connection lifecycle
    and framing) and the RPC surface (synthesize, program, ...) are two
    single-purpose objects rather than one class doing both jobs.
    """

    __slots__ = (
        "_explicit_port",
        "_explicit_token",
        "_host",
        "_port",
        "_token",
        "_ws",
    )

    _host: str
    _explicit_port: int | None
    _explicit_token: str | None
    _port: int | None
    _token: str | None
    _ws: websockets.asyncio.client.ClientConnection | None

    def __new__(
        cls,
        host: str | None,
        port: int | None,
        token: str | None,
    ) -> Self:
        self = super().__new__(cls)
        self._host = host if host is not None else DaemonEnv.host()
        self._explicit_port = port
        self._explicit_token = token
        self._port = port
        self._token = token
        self._ws = None
        return self

    def _resolve_port(self) -> int:
        """Return the port, reading from file if needed."""
        if self._port is not None:
            return self._port
        env = DaemonEnv.port()
        if env is not None:
            return env
        port = read_port_file()
        if port is None:
            msg = "voxd port file not found. Is the daemon running? Start it with: voxd"
            raise VoxdConnectionError(msg)
        return port

    def _resolve_token(self) -> str | None:
        """Return the token, reading from file if needed."""
        if self._token is not None:
            return self._token
        env = DaemonEnv.token()
        if env is not None:
            return env
        return read_token_file()

    def _build_uri(self) -> str:
        """Build the WebSocket URI with auth token."""
        port = self._resolve_port()
        token = self._resolve_token()
        uri = f"ws://{self._host}:{port}/ws"
        if token:
            uri += f"?token={token}"
        return uri

    async def connect(self) -> None:
        """Connect to voxd WebSocket. Call once before sending messages."""
        uri = self._build_uri()
        try:
            # all_errors: a host with several addresses fails as one
            # ExceptionGroup of per-address errors, so a refusal on every
            # address can be told from one that timed out or was unreachable.
            self._ws = await websockets.asyncio.client.connect(uri, all_errors=True)
        except (
            OSError,
            ExceptionGroup,
            websockets.exceptions.WebSocketException,
        ) as exc:
            # A WebSocketException is voxd (or whatever holds the port)
            # answering the handshake with a refusal of its own -- HTTP 403 for
            # a bad token or Origin, or not speaking HTTP at all.
            endpoint = uri.split("?")[0]
            raise VoxdConnectionError.of_connect(endpoint, exc) from exc

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

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

        # Restore explicit values; re-read from disk only if None.
        self._port = self._explicit_port
        self._token = self._explicit_token
        await self.connect()
        # connect() sets self._ws or raises VoxdConnectionError.
        ws = self._ws
        if ws is None:
            msg = "connect() succeeded but self._ws is None"
            raise VoxdConnectionError(msg)
        return ws

    async def send_and_recv(
        self,
        msg: dict[str, object],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """Send a JSON message and wait for a single JSON response."""
        ws = await self._ensure_connected()
        await self._send_frame(ws, msg)
        msg_type = str(msg.get("type", ""))
        raw = await self._recv_frame(
            ws, timeout, f"Timeout waiting for response to '{msg_type}'", msg
        )
        return self._decode(raw)

    async def _send_frame(
        self, ws: websockets.asyncio.client.ClientConnection, msg: dict[str, object]
    ) -> None:
        """Send one JSON frame, wrapping a lost connection as a ``VoxError``."""
        try:
            await ws.send(json.dumps(msg))
        except (websockets.exceptions.WebSocketException, OSError) as exc:
            raise VoxdConnectionError(self._transport_failure(msg, exc)) from exc

    async def _recv_frame(
        self,
        ws: websockets.asyncio.client.ClientConnection,
        timeout: float,
        timeout_msg: str,
        msg: dict[str, object],
    ) -> object:
        """Receive one frame within *timeout*, wrapping every transport failure.

        A ``ConnectionClosed`` or ``OSError`` from ``ws.recv()`` must surface as
        a :class:`VoxError` -- otherwise it escapes a CLI command's
        ``(VoxdConnectionError, VoxdProtocolError)`` catch as a raw traceback.
        A timeout is a protocol failure; a dropped socket is a connection one.
        """
        try:
            return await asyncio.wait_for(ws.recv(), timeout=timeout)
        except TimeoutError as exc:
            raise VoxdProtocolError(timeout_msg) from exc
        except (websockets.exceptions.WebSocketException, OSError) as exc:
            raise VoxdConnectionError(self._transport_failure(msg, exc)) from exc

    @staticmethod
    def _transport_failure(msg: dict[str, object], exc: Exception) -> str:
        """Describe a lost/closed connection for a wrapped transport error."""
        msg_type = str(msg.get("type", ""))
        return f"connection to voxd lost during '{msg_type}': {exc}"

    @staticmethod
    def _decode(raw: object) -> dict[str, Any]:
        """Parse a wire frame, raising ``VoxdProtocolError`` on bad JSON or error.

        The one place both drain and single-response paths turn a malformed or
        truncated frame, and ``voxd``'s ``{"type": "error", "message": ...}``,
        into a raised protocol error -- so a non-JSON frame surfaces as a
        one-line ``VoxError`` instead of a raw ``JSONDecodeError`` traceback, and
        the error contract lives in a single spot rather than at every site.
        """
        try:
            resp: dict[str, Any] = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise VoxdProtocolError(f"invalid JSON from voxd: {exc}") from exc
        if resp.get("type") == "error":
            raise VoxdRejectionError(str(resp.get("message", "unknown error")))
        return resp

    async def fetch_stream(self, msg: dict[str, object], *, timeout: float) -> bytes:
        """Send a fetch request and reassemble the chunked stream into bytes.

        Consumes ``fetch_begin`` -> ordered ``chunk``* -> ``fetch_end``, appending
        one frame at a time so client memory holds only the reassembled file plus
        a single chunk. The running total is checked against the declared byte
        count and the sha256 is verified, so a short, reordered, or corrupted
        stream raises. A mid-stream ``error`` frame surfaces through ``_decode`` as
        a :class:`VoxdProtocolError`, and the partial buffer is discarded.

        On ANY mid-stream fault the connection is *tainted and closed* before the
        error propagates: the daemon may still be sending this fetch's remaining
        chunk frames, and on a reused persistent connection those stale frames
        would be read by the NEXT request and desync the protocol (a
        sha256/byte-count mismatch or an out-of-order frame on a later, unrelated
        fetch or command). Discarding the poisoned socket makes the next request
        reconnect to a clean one via :meth:`_ensure_connected`, so a fault leaves
        no partial file and no stale frames behind.
        """
        ws = await self._ensure_connected()
        await self._send_frame(ws, msg)
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            return await self._reassemble(ws, deadline, msg)
        except BaseException:
            # Poison the connection: stale chunk frames from this failed fetch
            # must not be read by the next request. Suppress close errors -- the
            # socket may already be gone; nulling _ws forces a clean reconnect.
            with contextlib.suppress(Exception):
                await ws.close()
            self._ws = None
            raise

    async def _reassemble(
        self,
        ws: websockets.asyncio.client.ClientConnection,
        deadline: float,
        msg: dict[str, object],
    ) -> bytes:
        """Consume ``fetch_begin`` -> ordered ``chunk``* -> ``fetch_end`` into bytes.

        Verifies the frame order, the declared byte count, and the sha256 before
        returning, so a short, reordered, or corrupted stream raises. The caller
        owns the connection-tainting on any raise from here.
        """
        begin = self._decode(await self._recv(ws, deadline, "fetch_begin", msg))
        if begin.get("type") != "fetch_begin":
            got = begin.get("type")
            raise VoxdProtocolError(f"expected 'fetch_begin', got {got!r}")
        declared, expected_chunks, digest = self._begin_fields(begin)
        buffer, hasher = bytearray(), hashlib.sha256()
        for seq in range(expected_chunks):
            frame = self._decode(await self._recv(ws, deadline, "chunk", msg))
            self._append_chunk(frame, seq, buffer, hasher)
        end = self._decode(await self._recv(ws, deadline, "fetch_end", msg))
        if end.get("type") != "fetch_end":
            raise VoxdProtocolError(f"expected 'fetch_end', got {end.get('type')!r}")
        self._verify(buffer, declared, hasher, digest)
        return bytes(buffer)

    async def _recv(
        self,
        ws: websockets.asyncio.client.ClientConnection,
        deadline: float,
        expected: str,
        msg: dict[str, object],
    ) -> object:
        """Receive one frame before *deadline*, or raise a timeout protocol error."""
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise VoxdProtocolError(f"timeout waiting for '{expected}' in a fetch")
        return await self._recv_frame(
            ws, remaining, f"timeout waiting for '{expected}' in a fetch", msg
        )

    @staticmethod
    def _begin_fields(begin: dict[str, Any]) -> tuple[int, int, str]:
        """Return ``(declared_bytes, chunk_count, sha256)`` from a fetch_begin frame."""
        try:
            return int(begin["bytes"]), int(begin["chunks"]), str(begin["sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VoxdProtocolError(f"malformed 'fetch_begin': {exc}") from exc

    @staticmethod
    def _append_chunk(
        frame: dict[str, Any], seq: int, buffer: bytearray, hasher: Any
    ) -> None:
        """Validate one chunk's order and append its decoded bytes to *buffer*."""
        if frame.get("type") != "chunk" or frame.get("seq") != seq:
            raise VoxdProtocolError(
                f"expected chunk seq {seq}, got "
                f"{frame.get('type')!r} seq {frame.get('seq')!r}"
            )
        try:
            data = base64.b64decode(str(frame["data"]), validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise VoxdProtocolError(f"chunk {seq} has invalid base64: {exc}") from exc
        buffer.extend(data)
        hasher.update(data)

    @staticmethod
    def _verify(buffer: bytearray, declared: int, hasher: Any, digest: str) -> None:
        """Raise unless the reassembly matches the declared byte count and sha256."""
        if len(buffer) != declared:
            raise VoxdProtocolError(
                f"fetch byte-count mismatch: got {len(buffer)}, declared {declared}"
            )
        if hasher.hexdigest() != digest:
            raise VoxdProtocolError("fetch sha256 mismatch: stream corrupted")

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
        """
        ws = await self._ensure_connected()
        await self._send_frame(ws, msg)
        responses: list[dict[str, Any]] = []
        deadline = asyncio.get_running_loop().time() + timeout
        # One message serves both timeout paths (deadline exceeded, recv timeout).
        expected = early_terminal or terminal_type
        timeout_msg = f"Timeout waiting for '{expected}' in '{msg.get('type', '')}'"
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise VoxdProtocolError(timeout_msg)
            raw = await self._recv_frame(ws, remaining, timeout_msg, msg)
            resp = self._decode(raw)  # wraps bad JSON + error frames as VoxError
            responses.append(resp)
            resp_type = resp.get("type")
            if resp_type == terminal_type:
                return responses
            if early_terminal is not None and resp_type == early_terminal:
                # Server will still send terminal_type (e.g., 'done' after
                # 'playing'). Close the connection so that stale message
                # cannot be consumed by the next request on a reused
                # connection. Suppress close errors — the connection may
                # already be gone, but the audio is queued.
                with contextlib.suppress(Exception):
                    await ws.close()
                self._ws = None
                return responses
