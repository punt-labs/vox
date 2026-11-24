"""WebSocket client for voxd audio daemon.

Lightweight -- imports only stdlib + websockets.
Used by the MCP server, hook handlers, and CLI commands.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from types import TracebackType

from punt_vox.album_export import AlbumExport as AlbumExport, ExportManifest
from punt_vox.client_errors import (
    VoxdProtocolError,
)
from punt_vox.client_recordings import (
    RecordingListing,
    RecordingSummary as RecordingSummary,
)
from punt_vox.client_results import (
    RecordResult as RecordResult,
    SynthesizeResult as SynthesizeResult,
)
from punt_vox.client_transport import VoxdTransport
from punt_vox.types_programs import (
    CommandOutcome,
    HealthStatus,
    JsonObject,
    ProgramStatus,
    ProgramSummary,
    PromptSet,
)
from punt_vox.types_provider import ProviderStatusPayload
from punt_vox.types_synthesis import SynthesisSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CacheStatus:
    """The daemon cache's size, described to a client.

    ``path`` is the daemon cache directory relativized to its data root
    (``cache``) -- a logical name with no absolute prefix, so a remote client
    learns the location, never the daemon's home or username. ``entries`` and
    ``size_bytes`` count the ``.mp3`` and orphaned ``.tmp`` files there, mirroring
    the daemon's own ``cache_status`` return.
    """

    entries: int
    size_bytes: int
    path: Path

    @classmethod
    def from_wire(cls, obj: JsonObject) -> Self:
        """Build a cache status from a ``cache_status`` reply, raising on a bad row.

        Raises:
            ValueError: If a supplied wire field is missing, malformed, or has the wrong
                type.
        """
        return cls(
            entries=obj.require_int("entries"),
            size_bytes=obj.require_int("size_bytes"),
            path=Path(obj.require_str("path")),
        )


# ---------------------------------------------------------------------------
# Timeouts — values in seconds
# ---------------------------------------------------------------------------

_TIMEOUT_SYNTHESIS = 30.0
_TIMEOUT_SHORT = 5.0
# provider_status with verify runs each provider's live check in parallel,
# each bounded by LiveCheck's 10s deadline on the daemon, after waiting out any
# synthesis holding the env lock; leave headroom for both.
_TIMEOUT_PROVIDER_CHECK = 30.0
# play waits for the host-side playback to FINISH (not just enqueue) so a
# failure surfaces, so its deadline must cover a full track. Bounded like the
# record cap so a wedged daemon is still detected within ten minutes.
_TIMEOUT_PLAYBACK = 600.0
# fetch reassembles the chunked stream (fetch_begin -> chunk* -> fetch_end); the
# 120s is a whole-stream deadline, not per-frame, so a large recording over the
# documented remote/SSH-tunnel path must finish within it. A cold one-shot
# retrieval affords the wait; raise it if a big transfer is abandoned mid-stream.
_TIMEOUT_FETCH = 120.0

# record synthesizes to a file that may take minutes for long text (a fresh
# 6000-char ElevenLabs synthesis was measured at ~2m). Scale the wait with the
# text length rather than a fixed cap so a legitimate long synthesis is never
# abandoned, while an absolute ceiling still detects a hung daemon within a
# bounded window regardless of input length.
_RECORD_TIMEOUT_BASE = 60.0
_RECORD_TIMEOUT_PER_CHAR = 0.05
_RECORD_TIMEOUT_MAX = 600.0

# music_new generates one ElevenLabs track (measured minutes for a fresh track);
# the daemon acks 'generating' first, but the client still bounds the wait so a
# wedged provider is detected. Generous, matching the record ceiling.
_TIMEOUT_MUSIC_NEW = 600.0

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
    requests. Connection and protocol validation failures raise a
    :class:`~punt_vox.VoxError`
    (:class:`~punt_vox.VoxdConnectionError` when the daemon is unreachable,
    :class:`~punt_vox.VoxdProtocolError` on an unexpected reply).

    With no *host*/*port*/*token*, the client resolves them from the
    ``VOXD_HOST``/``VOXD_PORT``/``VOXD_TOKEN`` environment variables and the
    daemon's run-directory files -- the usual local-daemon case.
    """

    __slots__ = ("_transport",)

    _transport: VoxdTransport

    def __new__(
        cls,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
    ) -> Self:
        """Create a Vox client."""
        self = super().__new__(cls)
        self._transport = VoxdTransport(host, port, token)
        return self

    # -- connection lifecycle ------------------------------------------------

    async def connect(self) -> None:
        """Connect to voxd WebSocket. Call once before sending messages.

        Raises:
            VoxdConnectionError: If the endpoint is unavailable, unreadable, or rejects
                the handshake.
        """
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
        timeout: float | None = None,
    ) -> SynthesizeResult:
        """Send synthesize request. Audio plays on server.

        *spec* bundles the voice/provider/rate parameters; *once* is the dedup
        TTL window. The daemon acknowledges only after synthesis finishes,
        so long text may need a longer deadline.

        Args:
            once: Deduplication window in seconds; None disables deduplication.
            timeout: Seconds to wait for the playing acknowledgement, including
                synthesis time. None uses the 30-second default.

        Returns:
            Request identity, cache-hit signal, and deduplication status. Playback
            may still be in progress when the call returns.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
            ValueError: If a deduplication timestamp is not numeric.
            TypeError: If a deduplication timestamp is null or cannot become seconds.
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
            timeout=timeout if timeout is not None else _TIMEOUT_SYNTHESIS,
            terminal_type="done",
            early_terminal="playing",
        )
        return SynthesizeResult.from_terminal(
            request_id, responses[-1] if responses else {}
        )

    async def chime(self, signal: str) -> None:
        """Play a bundled chime asset.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        msg: dict[str, object] = {"type": "chime", "signal": signal}
        await self._transport.send_and_drain(
            msg, timeout=_TIMEOUT_SHORT, terminal_type="done", early_terminal="playing"
        )

    async def record(
        self,
        text: str,
        spec: SynthesisSpec | None = None,
        *,
        name: str | None = None,
    ) -> RecordResult:
        """Synthesize into the daemon's store; return a locator, not a path.

        No audio crosses the wire and the client names no daemon path. The
        daemon stores the MP3 under a validated bare *name* or, when that is
        None, a content-addressed name, and returns a :class:`RecordResult`
        locator (store id/name, store path, byte count). *spec* bundles the
        voice/provider/rate parameters. The response deadline scales with the
        text length (bounded) so a long synthesis is not abandoned by a
        premature timeout.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        request_id = uuid.uuid4().hex[:12]
        msg: dict[str, object] = {
            "type": "record",
            "id": request_id,
            "text": text,
            **(spec or SynthesisSpec()).to_client_kwargs(),
        }
        # Send name whenever it is not None -- including an explicit "" -- so
        # the daemon is the single authority on name validity: it rejects an
        # empty name pre-ack. Only an absent name (None) is content-addressed.
        if name is not None:
            msg["name"] = name

        scaled = _RECORD_TIMEOUT_BASE + _RECORD_TIMEOUT_PER_CHAR * len(text)
        timeout = min(scaled, _RECORD_TIMEOUT_MAX)
        responses = await self._transport.send_and_drain(
            msg, timeout=timeout, terminal_type="audio"
        )
        return RecordResult.from_audio(responses[-1] if responses else {})

    async def play(self, ref: str) -> None:
        """Play a stored recording on the daemon host by its store reference.

        *ref* is a bare store name (never a path). The daemon resolves it inside
        its recordings root, refusing any absolute or traversing reference, and
        plays it through its serialized queue -- so audio comes out on the
        machine with speakers, not on a remote client.

        Waits for the terminal ``done`` after the ``playing`` ack, i.e. for
        playback to actually finish, so a host-side failure (missing player,
        unplayable file, played-nothing) arrives as an error frame -- the daemon
        turns it into a ``VoxdProtocolError`` here -- rather than a silent
        success. This mirrors local ``vox play <file>``, which blocks until the
        player exits.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        msg: dict[str, object] = {
            "type": "play",
            "id": uuid.uuid4().hex[:12],
            "ref": ref,
        }
        await self._transport.send_and_drain(
            msg, timeout=_TIMEOUT_PLAYBACK, terminal_type="done"
        )

    async def fetch(self, ref: str) -> bytes:
        """Return a stored recording's bytes, reassembled from the chunked stream.

        *ref* is a bare store name. The daemon streams the file as
        ``fetch_begin`` -> ``chunk``* -> ``fetch_end`` in bounded per-frame memory,
        so a recording of any size retrieves in full; the transport reassembles
        the chunks in order and verifies the byte count and sha256 before
        returning. A mid-stream fault raises a :class:`~punt_vox.VoxdProtocolError`
        and the partial is discarded.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        msg: dict[str, object] = {
            "type": "fetch",
            "id": uuid.uuid4().hex[:12],
            "ref": ref,
        }
        return await self._transport.fetch_stream(msg, timeout=_TIMEOUT_FETCH)

    async def fetch_part(self, album_id: str, part: str) -> bytes:
        """Return one album part's bytes, reassembled from the chunked stream.

        The album is addressed by its catalog id and the *part* by its bare name;
        the daemon catalog-resolves the album and validates the part inside it.
        Reassembly, ordering, and integrity checks are the recording ``fetch`` path.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        msg: dict[str, object] = {
            "type": "fetch",
            "id": uuid.uuid4().hex[:12],
            "album": album_id,
            "part": part,
        }
        return await self._transport.fetch_stream(msg, timeout=_TIMEOUT_FETCH)

    # -- recordings store (rec group) ---------------------------------------

    async def rec_list(self) -> tuple[RecordingSummary, ...]:
        """Return the store's recordings (name + bytes), the ``rec list`` view.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        msg: dict[str, object] = {
            "type": "rec_list",
            "id": uuid.uuid4().hex[:12],
        }
        with self._wire_guard():
            async with self._transport.exchange(msg) as exchange:
                return await RecordingListing.read(exchange, timeout=_TIMEOUT_SHORT)

    async def rec_remove(self, ref: str) -> None:
        """Delete recording *ref* from the store (a hostile/absent ref raises).

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        await self._command("rec_remove", ref=ref)

    # -- cache (daemon-owned MP3 quip cache) --------------------------------

    async def cache_status(self) -> CacheStatus:
        """Return the daemon cache's entry count, size, and path.

        Routes through the daemon so the numbers describe the daemon's *own*
        cache -- the one synthesis reads and writes -- not a client-local
        directory, honoring ``VOXD_HOST``/``PORT``/``TOKEN`` like every other
        command. ``path`` is the daemon-side cache path.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        resp = await self._command("cache_status")
        with self._wire_guard():
            return CacheStatus.from_wire(JsonObject.coerce(resp, "cache_status"))

    async def cache_clear(self) -> int:
        """Delete every entry in the daemon cache; return the count deleted.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        resp = await self._command("cache_clear")
        with self._wire_guard():
            return JsonObject.coerce(resp, "cache_clear").require_int("cleared")

    async def set_log_level(self, level: str) -> str:
        """Set the daemon's log level; return the effective level it applied.

        The daemon clamps *level* server-side to the INFO audit floor, so a
        request for a stricter level comes back as ``info`` -- the audit trail is
        never blinded. ``info`` or ``debug`` in normal use.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        resp = await self._command("set_log_level", level=level)
        with self._wire_guard():
            return JsonObject.coerce(resp, "set_log_level").require_str("level")

    # -- music catalog (music group) ----------------------------------------

    async def music_new(self, prompts: PromptSet, name: str | None = None) -> str:
        """Create one track into a fresh catalog album; return its bare album id.

        The daemon sends a ``generating`` ack before the long generation, then the
        terminal ``album`` frame; an empty/bad prompt raises. *prompts* is the
        one-track :class:`PromptSet` both surfaces build for ``music new``; its
        verbatim ``base`` rides the wire as ``base_prompt`` (the same authored-
        input key ``program_on`` uses) -- the client expands nothing.

        Raises:
            VoxdProtocolError: If the daemon does not return an album response.
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
        """
        fields: dict[str, object] = {"base_prompt": prompts.base}
        if name is not None:
            fields["name"] = name
        msg: dict[str, object] = {
            "type": "music_new",
            "id": uuid.uuid4().hex[:12],
            **fields,
        }
        responses = await self._transport.send_and_drain(
            msg, timeout=_TIMEOUT_MUSIC_NEW, terminal_type="album"
        )
        terminal = responses[-1] if responses else {}
        if terminal.get("type") != "album":
            raise VoxdProtocolError(f"expected 'album', got {terminal.get('type')!r}")
        with self._wire_guard():
            return JsonObject.coerce(terminal, "album").require_str("album_id")

    async def music_remove(self, album_id: str) -> None:
        """Delete a catalog album by id (a playing album is refused, D-2).

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        await self._command("music_remove", album=album_id)

    async def music_get(self, album_id: str, dest_dir: Path) -> AlbumExport:
        """Copy an album into *dest_dir* as ``<album-name>/`` of its parts.

        Fetches the manifest and refuses an album with no ready parts before
        touching *dest_dir* -- exporting an empty album would leave a hollow
        directory that then blocks a real export of the same album on the
        collision guard. With parts to write, it creates the album directory
        (refusing a collision, D-1), then chunk-fetches every part and writes it
        atomically. On any failure the half-written directory is removed, so a
        fault leaves nothing. Parts the daemon reports missing on disk (deleted
        out of band) are skipped and named on the returned export.

        Raises:
            ValueError: If the album has no parts to fetch.
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
            OSError: If the destination cannot be created or written.
            FileExistsError: If the destination album directory already exists.
        """
        reply = await self._command("music_manifest", album=album_id)
        with self._wire_guard():
            manifest = ExportManifest.parse(reply)  # names validated pre-mkdir
        manifest.require_parts(album_id)  # fail before mkdir: CWD left untouched
        target = dest_dir / manifest.album
        target.mkdir(parents=True)  # exist_ok=False: a collision raises (D-1)
        try:
            for part in manifest.parts:
                data = await self.fetch_part(album_id, part)
                self._write_atomically(target / part, data)
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return AlbumExport(target, manifest.missing)

    @staticmethod
    def _write_atomically(dest: Path, data: bytes) -> None:
        """Write *data* to a temp sibling then ``os.replace`` onto *dest*."""
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".part")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            tmp.replace(dest)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    async def voices(self, provider: str) -> list[str]:
        """List *provider*'s voice roster; a missing ``voices`` key is a protocol error.

        State is the sole authority on which provider voxd runs, so the
        caller names it explicitly on every roster fetch: the wire never
        omits ``provider`` and the daemon never has to guess. Defaulting
        to ``[]`` on a missing key would hide a misbehaving daemon behind
        a provider that genuinely offers no voices.

        Raises:
            VoxdProtocolError: If the response does not contain a voices list.
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
        """
        msg: dict[str, object] = {"type": "voices", "provider": provider}
        resp = await self._transport.send_and_recv(msg, timeout=_TIMEOUT_SHORT)
        if "voices" not in resp:
            raise VoxdProtocolError(f"'voices' response missing 'voices' key: {resp}")
        voice_list: list[str] = resp["voices"]
        return voice_list

    async def health(self) -> HealthStatus:
        """Return the daemon's health snapshot (liveness, port, version).

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        resp = await self._transport.send_and_recv(
            {"type": "health"}, timeout=_TIMEOUT_SHORT
        )
        return HealthStatus.from_wire(resp)

    async def provider_status(
        self, provider: str | None = None, *, verify: bool = False
    ) -> ProviderStatusPayload:
        """Return the daemon's per-provider readiness verdict and its preferred name.

        With *provider* omitted (or ``None``) the reply carries every
        registered provider's readiness plus the daemon's ``preferred``
        proposal -- the shape :mod:`punt_vox.doctor` renders and
        :mod:`punt_vox.enablement` consults.  With *provider* set, the
        reply carries just that one row (still a
        :class:`~punt_vox.types_provider.ProviderStatusPayload`, so the
        parsing path does not fork); ``preferred`` rides on both shapes
        because it is answered from the same requirement map and costs
        nothing to include.

        Never cached client-side: the readiness answer moves with the
        daemon's environment (a fresh ``AWS_PROFILE`` export, a key
        removed from ``keys.env``), so a status surface reading a
        cached value would tell the user "yes, ready" long after the
        credential vanished.  The daemon's check is cheap enough that
        one WebSocket round-trip per ``mic:status`` call is the right
        cost for a live answer.

        *verify* (``vox doctor``) asks the daemon to confirm each ready
        provider with an authenticated live call, so a present but
        rejected key reports ``check_failed`` rather than ``ready``.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        msg: dict[str, object] = {
            "type": "provider_status",
            "id": uuid.uuid4().hex[:12],
        }
        if provider is not None:
            msg["provider"] = provider
        if verify:
            msg["verify"] = True
        deadline = _TIMEOUT_PROVIDER_CHECK if verify else _TIMEOUT_SHORT
        resp = await self._transport.send_and_recv(msg, timeout=deadline)
        with self._wire_guard():
            return ProviderStatusPayload.from_wire(
                JsonObject.coerce(resp, "provider_status")
            )

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
        """Return the daemon's authoritative Program status, parsed from the wire.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        resp = await self._command("program_status")
        with self._wire_guard():
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
        """Turn a Program on, forwarding the session vibe and authored prompts.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
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

    async def program_stop(self) -> CommandOutcome:
        """Halt the active Program.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        return self._outcome(await self._command("program_stop"))

    async def program_next(self) -> CommandOutcome:
        """User transport next: step the replay cursor forward, or skip a Program.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        return self._outcome(await self._command("program_next"))

    async def program_prev(self) -> CommandOutcome:
        """User transport prev: step the replay cursor back one part.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        return self._outcome(await self._command("program_prev"))

    async def program_pause(self) -> CommandOutcome:
        """Suspend the active source in place (transport pause).

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        return self._outcome(await self._command("program_pause"))

    async def program_resume(self) -> CommandOutcome:
        """Continue a suspended source (transport resume).

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        return self._outcome(await self._command("program_resume"))

    async def program_select(
        self,
        *,
        style: str | None = None,
        vibe: str | None = None,
        name: str | None = None,
        album_id: str | None = None,
    ) -> CommandOutcome:
        """Replay a Selection resolved by album id (direct) or by tags.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
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
        """Return every album as a catalogue summary, parsed from the wire.

        Raises:
            VoxdConnectionError: If the daemon is unreachable or the connection is lost.
            VoxdRejectionError: If the daemon rejects the request.
            VoxdProtocolError: If the reply is not a JSON object, fields fail
                validation, or the response deadline expires.
        """
        resp = await self._command("program_list")
        with self._wire_guard():
            obj = JsonObject.coerce(resp, "program_list")
            return tuple(
                self._summary(JsonObject.coerce(item, "program_list.programs"))
                for item in obj.require_list("programs")
            )

    @staticmethod
    @contextlib.contextmanager
    def _wire_guard() -> Generator[None]:
        """Surface a wire-parse failure as a ``VoxdProtocolError``.

        ``JsonObject`` raises ``ValueError`` on a malformed or missing field,
        carrying its own field-path context. At the public client boundary that
        must become the client's own ``VoxdProtocolError``, so a caller catches
        every failure as a :class:`~punt_vox.VoxError` rather than a bare
        ``ValueError`` leaking the daemon's wire shape.
        """
        try:
            yield
        except ValueError as exc:
            raise VoxdProtocolError(f"malformed reply from voxd: {exc}") from exc

    @staticmethod
    def _outcome(resp: dict[str, Any]) -> CommandOutcome:
        """Read the applied/rejected result from a command reply.

        An absent ``applied`` flag means the command was accepted at enqueue.
        An explicit false flag means rejection; a missing or empty message
        receives a non-empty fallback so the refusal remains visible.
        """
        with VoxClient._wire_guard():
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
