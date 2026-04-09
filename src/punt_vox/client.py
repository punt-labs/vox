"""WebSocket client for voxd audio daemon.

Lightweight -- imports only stdlib + websockets.
Used by the MCP server, hook handlers, and CLI commands.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import websockets
import websockets.asyncio.client

from punt_vox.paths import run_dir as _user_run_dir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SynthesizeResult:
    """Result of a ``synthesize`` call on voxd.

    When ``deduped`` is False, the request was sent to TTS and played
    through the audio device. When ``deduped`` is True, the caller passed
    an ``once=<ttl>`` window and the server found an identical text
    already played within that window — the audio was NOT re-played.
    The user heard the message from a prior call. The caller should
    treat this as success, not an error.

    Non-deduped results have ``original_played_at`` and
    ``ttl_seconds_remaining`` both set to ``None``.

    See bead vox-0e9 for the biff-wall use case that motivated this.
    """

    request_id: str
    deduped: bool = False
    original_played_at: float | None = None
    ttl_seconds_remaining: float | None = None


# ---------------------------------------------------------------------------
# Timeouts (seconds)
# ---------------------------------------------------------------------------

_TIMEOUT_SYNTHESIS = 30.0
_TIMEOUT_SHORT = 5.0

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class VoxdConnectionError(Exception):
    """Raised when the client cannot connect to voxd."""


class VoxdProtocolError(Exception):
    """Raised when voxd returns an unexpected response."""


# ---------------------------------------------------------------------------
# Path resolution — delegated to punt_vox.paths so every module agrees.
# ---------------------------------------------------------------------------


def _run_dir() -> Path:
    """Return ``~/.punt-labs/vox/run`` — holds ``serve.port`` / ``serve.token``."""
    return _user_run_dir()


def read_port_file() -> int | None:
    """Read the daemon port from the port file. Returns None if missing."""
    port_file = _run_dir() / "serve.port"
    try:
        return int(port_file.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def read_token_file() -> str | None:
    """Read the daemon auth token. Returns None if missing."""
    token_file = _run_dir() / "serve.token"
    try:
        return token_file.read_text().strip()
    except (FileNotFoundError, OSError):
        return None


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


class VoxClient:
    """Async WebSocket client for voxd."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int | None = None,
        token: str | None = None,
    ) -> None:
        self._host = host
        self._explicit_port = port
        self._explicit_token = token
        self._port = port
        self._token = token
        self._ws: websockets.asyncio.client.ClientConnection | None = None

    # -- connection lifecycle ------------------------------------------------

    def _resolve_port(self) -> int:
        """Return the port, reading from file if needed."""
        if self._port is not None:
            return self._port
        port = read_port_file()
        if port is None:
            msg = "voxd port file not found. Is the daemon running? Start it with: voxd"
            raise VoxdConnectionError(msg)
        return port

    def _resolve_token(self) -> str | None:
        """Return the token, reading from file if needed."""
        if self._token is not None:
            return self._token
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
            self._ws = await websockets.asyncio.client.connect(uri)
        except OSError as exc:
            msg = f"Cannot connect to voxd at {uri.split('?')[0]}: {exc}"
            raise VoxdConnectionError(msg) from exc

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
            except Exception:
                # Connection is dead; fall through to reconnect.
                self._ws = None

        # Restore explicit values; re-read from disk only if None.
        self._port = self._explicit_port
        self._token = self._explicit_token
        await self.connect()
        # connect() sets self._ws or raises VoxdConnectionError.
        ws = self._ws
        assert ws is not None
        return ws

    # -- request helpers -----------------------------------------------------

    async def _send_and_recv(
        self,
        msg: dict[str, object],
        *,
        timeout: float = _TIMEOUT_SHORT,
    ) -> dict[str, Any]:
        """Send a JSON message and wait for a single JSON response."""
        ws = await self._ensure_connected()
        await ws.send(json.dumps(msg))
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except TimeoutError as exc:
            msg_type = str(msg.get("type", ""))
            raise VoxdProtocolError(
                f"Timeout waiting for response to '{msg_type}'"
            ) from exc
        resp: dict[str, Any] = json.loads(str(raw))
        if resp.get("type") == "error":
            raise VoxdProtocolError(str(resp.get("message", "unknown error")))
        return resp

    async def _send_and_drain(
        self,
        msg: dict[str, object],
        *,
        timeout: float = _TIMEOUT_SYNTHESIS,
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
        await ws.send(json.dumps(msg))
        responses: list[dict[str, Any]] = []
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                msg_type = str(msg.get("type", ""))
                raise VoxdProtocolError(
                    f"Timeout waiting for '{terminal_type}' in '{msg_type}'"
                )
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError as exc:
                msg_type = str(msg.get("type", ""))
                raise VoxdProtocolError(
                    f"Timeout waiting for '{terminal_type}' in '{msg_type}'"
                ) from exc
            resp: dict[str, Any] = json.loads(str(raw))
            responses.append(resp)
            if resp.get("type") == "error":
                raise VoxdProtocolError(str(resp.get("message", "unknown error")))
            resp_type = resp.get("type")
            if resp_type == terminal_type:
                return responses
            if early_terminal is not None and resp_type == early_terminal:
                # Server will still send terminal_type (e.g., 'done' after
                # 'playing'). Close the connection so that stale message
                # cannot be consumed by the next request on a reused
                # VoxClient connection.
                await ws.close()
                self._ws = None
                return responses

    # -- public API ----------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        rate: int = 90,
        language: str | None = None,
        vibe_tags: str | None = None,
        stability: float | None = None,
        similarity: float | None = None,
        style: float | None = None,
        speaker_boost: bool | None = None,
        api_key: str | None = None,
        once: int | None = None,
    ) -> SynthesizeResult:
        """Send synthesize request. Audio plays on server.

        Returns a ``SynthesizeResult`` carrying the request id and any
        dedup status. On a dedup hit (caller passed ``once=<ttl>`` and
        the server found an identical text within the window), the
        returned ``deduped`` field is True and the audio is NOT played
        a second time. The caller should treat a deduped result as
        success, not failure.
        """
        request_id = uuid.uuid4().hex[:12]
        msg: dict[str, object] = {
            "type": "synthesize",
            "id": request_id,
            "text": text,
            "rate": rate,
        }
        if voice is not None:
            msg["voice"] = voice
        if provider is not None:
            msg["provider"] = provider
        if model is not None:
            msg["model"] = model
        if language is not None:
            msg["language"] = language
        if vibe_tags is not None:
            msg["vibe_tags"] = vibe_tags
        if stability is not None:
            msg["stability"] = stability
        if similarity is not None:
            msg["similarity"] = similarity
        if style is not None:
            msg["style"] = style
        if speaker_boost is not None:
            msg["speaker_boost"] = speaker_boost
        if api_key is not None:
            msg["api_key"] = api_key
        if once is not None:
            msg["once"] = once

        responses = await self._send_and_drain(
            msg,
            timeout=_TIMEOUT_SYNTHESIS,
            terminal_type="done",
            early_terminal="playing",
        )
        terminal = responses[-1] if responses else {}
        if terminal.get("deduped"):
            return SynthesizeResult(
                request_id=request_id,
                deduped=True,
                original_played_at=(
                    float(terminal["original_played_at"])
                    if "original_played_at" in terminal
                    else None
                ),
                ttl_seconds_remaining=(
                    float(terminal["ttl_seconds_remaining"])
                    if "ttl_seconds_remaining" in terminal
                    else None
                ),
            )
        return SynthesizeResult(request_id=request_id)

    async def chime(self, signal: str) -> None:
        """Play a bundled chime asset."""
        msg: dict[str, object] = {"type": "chime", "signal": signal}
        await self._send_and_drain(
            msg, timeout=_TIMEOUT_SHORT, terminal_type="done", early_terminal="playing"
        )

    async def record(
        self,
        text: str,
        *,
        voice: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        rate: int = 90,
        language: str | None = None,
        vibe_tags: str | None = None,
        stability: float | None = None,
        similarity: float | None = None,
        style: float | None = None,
        speaker_boost: bool | None = None,
        api_key: str | None = None,
    ) -> bytes:
        """Synthesize and return MP3 bytes (no playback)."""
        request_id = uuid.uuid4().hex[:12]
        msg: dict[str, object] = {
            "type": "record",
            "id": request_id,
            "text": text,
            "rate": rate,
        }
        if voice is not None:
            msg["voice"] = voice
        if provider is not None:
            msg["provider"] = provider
        if model is not None:
            msg["model"] = model
        if language is not None:
            msg["language"] = language
        if vibe_tags is not None:
            msg["vibe_tags"] = vibe_tags
        if stability is not None:
            msg["stability"] = stability
        if similarity is not None:
            msg["similarity"] = similarity
        if style is not None:
            msg["style"] = style
        if speaker_boost is not None:
            msg["speaker_boost"] = speaker_boost
        if api_key is not None:
            msg["api_key"] = api_key

        resp = await self._send_and_recv(msg, timeout=_TIMEOUT_SYNTHESIS)
        if resp.get("type") != "audio":
            raise VoxdProtocolError(
                f"Expected 'audio' response, got '{resp.get('type')}'"
            )
        data = resp.get("data", "")
        try:
            return base64.b64decode(str(data))
        except (binascii.Error, ValueError) as exc:
            raise VoxdProtocolError(f"Invalid audio data from voxd: {exc}") from exc

    async def voices(self, provider: str | None = None) -> list[str]:
        """List available voices."""
        msg: dict[str, object] = {"type": "voices"}
        if provider is not None:
            msg["provider"] = provider
        resp = await self._send_and_recv(msg, timeout=_TIMEOUT_SHORT)
        voice_list: list[str] = resp.get("voices", [])
        return voice_list

    async def health(self) -> dict[str, object]:
        """Check daemon health."""
        resp = await self._send_and_recv({"type": "health"}, timeout=_TIMEOUT_SHORT)
        return resp


# ---------------------------------------------------------------------------
# Sync wrapper
# ---------------------------------------------------------------------------


class VoxClientSync:
    """Synchronous wrapper around VoxClient for hooks and CLI.

    Creates a fresh connection per call. Simple and correct -- hooks and
    CLI commands are short-lived, so connection pooling adds no value.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int | None = None,
        token: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._token = token

    def _make_client(self) -> VoxClient:
        return VoxClient(host=self._host, port=self._port, token=self._token)

    def _run(self, coro: Any) -> Any:
        """Run an async coroutine synchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # Already inside an event loop (e.g., MCP server context).
            # Create a new loop in a thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Connect, call method, close."""
        client = self._make_client()
        await client.connect()
        try:
            func = getattr(client, method)
            return await func(*args, **kwargs)
        finally:
            await client.close()

    def synthesize(self, text: str, **kwargs: Any) -> SynthesizeResult:
        """Send synthesize request. Audio plays on server.

        See :class:`SynthesizeResult` for the returned fields — in
        particular the ``deduped`` flag that surfaces when the caller
        passed ``once=<ttl>`` and the server found an identical text
        already played within the window.
        """
        return self._run(  # type: ignore[no-any-return]
            self._call("synthesize", text, **kwargs)
        )

    def chime(self, signal: str) -> None:
        """Play a bundled chime asset."""
        self._run(self._call("chime", signal))

    def record(self, text: str, **kwargs: Any) -> bytes:
        """Synthesize and return MP3 bytes (no playback)."""
        return self._run(self._call("record", text, **kwargs))  # type: ignore[no-any-return]

    def voices(self, provider: str | None = None) -> list[str]:
        """List available voices."""
        return self._run(self._call("voices", provider))  # type: ignore[no-any-return]

    def health(self) -> dict[str, object]:
        """Check daemon health."""
        return self._run(self._call("health"))  # type: ignore[no-any-return]
