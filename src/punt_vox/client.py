"""WebSocket client for voxd audio daemon.

Lightweight -- imports only stdlib + websockets.
Used by the MCP server, hook handlers, and CLI commands.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

import websockets
import websockets.asyncio.client

if TYPE_CHECKING:
    from types import TracebackType

from punt_vox.client_env import DaemonEnv
from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.music_prompts import PromptSet
from punt_vox.paths import run_dir as _user_run_dir
from punt_vox.program_control import CommandOutcome, ProgramSummary
from punt_vox.types_health import HealthStatus
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.programs.status import ProgramStatus
from punt_vox.voxd.programs.wire import JsonObject

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SynthesizeResult:
    """Result of a ``synthesize`` call on voxd.

    ``deduped`` is True when the caller passed an ``once=<ttl>`` window and
    the server found an identical text already played within it -- the
    audio was NOT re-played; treat it as success, not an error. Non-deduped
    results leave ``original_played_at`` and ``ttl_seconds_remaining`` None.

    ``cached`` reports whether voxd served the audio from its
    content-addressed cache (True) or synthesized it fresh (False) -- the
    observability signal a caller asserts to confirm a cache hit. A dedup
    short-circuit leaves it False since no synthesis path ran.
    """

    request_id: str
    deduped: bool = False
    original_played_at: float | None = None
    ttl_seconds_remaining: float | None = None
    cached: bool = False


# ---------------------------------------------------------------------------
# Timeouts — values in seconds
# ---------------------------------------------------------------------------

_TIMEOUT_SYNTHESIS = 30.0
_TIMEOUT_SHORT = 5.0

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


class _VoxdTransport:
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
        return self._decode(raw)

    @staticmethod
    def _decode(raw: object) -> dict[str, Any]:
        """Parse a wire frame, raising ``VoxdProtocolError`` on an error frame.

        The one place both drain and single-response paths turn ``voxd``'s
        ``{"type": "error", "message": ...}`` into a raised protocol error, so
        the error contract lives in a single spot rather than duplicated at
        every receive site.
        """
        resp: dict[str, Any] = json.loads(str(raw))
        if resp.get("type") == "error":
            raise VoxdProtocolError(str(resp.get("message", "unknown error")))
        return resp

    async def send_and_drain(
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
        # One message serves both timeout paths (deadline exceeded, recv timeout).
        expected = early_terminal or terminal_type
        timeout_msg = f"Timeout waiting for '{expected}' in '{msg.get('type', '')}'"
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise VoxdProtocolError(timeout_msg)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError as exc:
                raise VoxdProtocolError(timeout_msg) from exc
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
                # connection. Suppress close errors — the connection may
                # already be gone, but the audio is queued.
                with contextlib.suppress(Exception):
                    await ws.close()
                self._ws = None
                return responses


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


class VoxClient:
    """Asynchronous client for the voxd audio daemon.

    Speaks voxd's RPC surface over one WebSocket connection: play speech
    (:meth:`synthesize`), play a bundled chime (:meth:`chime`), synthesize
    to MP3 bytes (:meth:`record`), list voices (:meth:`voices`), read daemon
    health (:meth:`health`), and drive the audio *program* controls
    (:meth:`program_on`, :meth:`program_status`, and the rest).

    Lifecycle: call :meth:`connect` once before the first request and
    :meth:`close` when finished, or use the client as an async context
    manager, which connects on entry and closes on exit::

        async with VoxClient() as vox:
            await vox.synthesize("build finished")

    A single client is meant to be reused across many calls; it re-reads the
    daemon's port and reconnects on its own if voxd restarts between
    requests. Every failure raises a :class:`~punt_vox.VoxError`
    (:class:`~punt_vox.VoxdConnectionError` when the daemon is unreachable,
    :class:`~punt_vox.VoxdProtocolError` on an unexpected reply).

    With no *host*/*port*/*token*, the client resolves them from the
    ``VOXD_HOST``/``VOXD_PORT``/``VOXD_TOKEN`` environment variables and the
    daemon's run-directory files -- the usual local-daemon case.
    """

    __slots__ = ("_transport",)

    _transport: _VoxdTransport

    def __new__(
        cls,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
    ) -> Self:
        self = super().__new__(cls)
        self._transport = _VoxdTransport(host, port, token)
        return self

    # -- connection lifecycle ------------------------------------------------

    async def connect(self) -> None:
        """Connect to voxd WebSocket. Call once before sending messages."""
        await self._transport.connect()

    async def close(self) -> None:
        """Close the WebSocket connection."""
        await self._transport.close()

    async def __aenter__(self) -> Self:
        """Connect on entry so the client is ready to send."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the connection on exit, whether or not the body raised."""
        await self.close()

    # -- public API ----------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        spec: SynthesisSpec | None = None,
        *,
        once: int | None = None,
    ) -> SynthesizeResult:
        """Send synthesize request. Audio plays on server.

        *spec* bundles the voice/provider/rate parameters; *once* is the dedup
        TTL window (seconds). Returns a ``SynthesizeResult`` carrying the request
        id, ``cached`` (cache-hit signal), and any dedup status.
        """
        request_id = uuid.uuid4().hex[:12]
        msg: dict[str, object] = {
            "type": "synthesize",
            "id": request_id,
            "text": text,
            **(spec or SynthesisSpec()).to_client_kwargs(),
        }
        if once is not None:
            msg["once"] = once

        responses = await self._transport.send_and_drain(
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
        return SynthesizeResult(
            request_id=request_id,
            cached=bool(terminal.get("cached", False)),
        )

    async def chime(self, signal: str) -> None:
        """Play a bundled chime asset."""
        msg: dict[str, object] = {"type": "chime", "signal": signal}
        await self._transport.send_and_drain(
            msg, timeout=_TIMEOUT_SHORT, terminal_type="done", early_terminal="playing"
        )

    async def record(self, text: str, spec: SynthesisSpec | None = None) -> bytes:
        """Synthesize and return MP3 bytes (no playback).

        *spec* bundles the voice/provider/rate parameters.
        """
        request_id = uuid.uuid4().hex[:12]
        msg: dict[str, object] = {
            "type": "record",
            "id": request_id,
            "text": text,
            **(spec or SynthesisSpec()).to_client_kwargs(),
        }

        resp = await self._transport.send_and_recv(msg, timeout=_TIMEOUT_SYNTHESIS)
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
        """List available voices; a missing ``voices`` key is a protocol error.

        Defaulting to ``[]`` would hide a misbehaving daemon behind a
        provider that genuinely offers no voices.
        """
        msg: dict[str, object] = {"type": "voices"}
        if provider is not None:
            msg["provider"] = provider
        resp = await self._transport.send_and_recv(msg, timeout=_TIMEOUT_SHORT)
        if "voices" not in resp:
            raise VoxdProtocolError(f"'voices' response missing 'voices' key: {resp}")
        voice_list: list[str] = resp["voices"]
        return voice_list

    async def health(self) -> HealthStatus:
        """Return the daemon's health snapshot (liveness, port, version)."""
        resp = await self._transport.send_and_recv(
            {"type": "health"}, timeout=_TIMEOUT_SHORT
        )
        return HealthStatus.from_wire(resp)

    # -- program surface (session-free; the daemon-facing wire, design section 4)

    async def _command(self, msg_type: str, **fields: object) -> dict[str, Any]:
        """Send a session-free program command and return the single response.

        The one primitive behind every ``program_*`` method: it stamps the wire
        type and a fresh request id, so a new command needs no bespoke message
        assembly. No ``owner_id`` -- ``voxd`` state is machine-universal.
        """
        msg: dict[str, object] = {
            "type": msg_type,
            "id": uuid.uuid4().hex[:12],
            **fields,
        }
        return await self._transport.send_and_recv(msg, timeout=_TIMEOUT_SHORT)

    async def program_status(self) -> ProgramStatus:
        """Return the daemon's authoritative Program status, parsed from the wire."""
        resp = await self._command("program_status")
        obj = JsonObject.coerce(resp, "program_status")
        return ProgramStatus.from_wire(obj.require_object("status"))

    async def program_on(
        self,
        *,
        style: str | None = None,
        vibe: str | None = None,
        name: str | None = None,
        prompts: PromptSet | None = None,
    ) -> CommandOutcome:
        """Turn a Program on, forwarding the session vibe and authored prompts."""
        fields: dict[str, object] = {}
        if style is not None:
            fields["style"] = style
        if vibe is not None:
            fields["vibe"] = vibe
        if name is not None:
            fields["name"] = name
        if prompts is not None:
            fields["base_prompt"] = prompts.base
            fields["variations"] = list(prompts.variations)
        return self._outcome(await self._command("program_on", **fields))

    async def program_off(self) -> CommandOutcome:
        """Turn the active Program off."""
        return self._outcome(await self._command("program_off"))

    async def program_next(self) -> CommandOutcome:
        """Advance to another Part (the one ungated skip/next/loop transition)."""
        return self._outcome(await self._command("program_next"))

    async def program_select(
        self,
        *,
        style: str | None = None,
        vibe: str | None = None,
        name: str | None = None,
        album_id: str | None = None,
    ) -> CommandOutcome:
        """Replay a Selection resolved by album id (direct) or by tags."""
        fields: dict[str, object] = {}
        if album_id is not None:
            fields["album_id"] = album_id
        if style is not None:
            fields["style"] = style
        if vibe is not None:
            fields["vibe"] = vibe
        if name is not None:
            fields["name"] = name
        return self._outcome(await self._command("program_select", **fields))

    async def program_list(self) -> tuple[ProgramSummary, ...]:
        """Return every album as a catalogue summary, parsed from the wire."""
        obj = JsonObject.coerce(await self._command("program_list"), "program_list")
        return tuple(
            self._summary(JsonObject.coerce(item, "program_list.programs"))
            for item in obj.require_list("programs")
        )

    @staticmethod
    def _outcome(resp: dict[str, Any]) -> CommandOutcome:
        """Read the applied/rejected result (design F7) from a command reply.

        A reply omitting ``applied`` is treated as applied -- the daemon only
        writes ``applied: false`` to flag a lost race (PY-EH-8: absence is the
        documented "it went through" contract). A rejection is guaranteed a
        non-empty ``message`` so a surface never renders a blank line for a
        refused command; an applied command may carry none.
        """
        obj = JsonObject.coerce(resp, "command")
        applied = obj.opt_bool("applied") is not False
        message = obj.opt_str("message") or ("" if applied else "command rejected")
        return CommandOutcome(applied=applied, message=message)

    @staticmethod
    def _summary(obj: JsonObject) -> ProgramSummary:
        """Parse one catalogue entry (id, tags, counts) from the wire."""
        return ProgramSummary(
            id=obj.require_str("id"),
            style=obj.require_str("style"),
            vibe=obj.require_str("vibe"),
            format=obj.require_str("format"),
            ready=obj.require_int("ready"),
            total=obj.opt_int("total") or 0,
            name=obj.opt_str("name"),
        )
