"""Synchronous facade over :class:`~punt_vox.client.VoxClient` for hooks and CLI.

``VoxClientSync`` creates a fresh connection per call and drives the async client
to completion -- simple and correct, because hooks and CLI commands are
short-lived so connection pooling adds no value. It is a thin humble object: each
method delegates to the matching :class:`VoxClient` coroutine through ``_call``,
which connects, invokes, and closes. Splitting it out of ``client`` keeps the
async transport (``VoxClient``) and the sync facade in separate, single-purpose
modules.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Self

from punt_vox.client import SynthesizeResult, VoxClient
from punt_vox.client_env import DaemonEnv
from punt_vox.music_prompts import PromptSet
from punt_vox.types_synthesis import SynthesisSpec

__all__ = ["VoxClientSync"]


class VoxClientSync:
    """Synchronous wrapper around :class:`VoxClient` for hooks and CLI.

    Creates a fresh connection per call. Simple and correct -- hooks and
    CLI commands are short-lived, so connection pooling adds no value.
    """

    __slots__ = ("_host", "_port", "_token")

    _host: str
    _port: int | None
    _token: str | None

    def __new__(
        cls,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
    ) -> Self:
        self = super().__new__(cls)
        self._host = host if host is not None else DaemonEnv.host()
        self._port = port
        self._token = token
        return self

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

    def synthesize(
        self, text: str, spec: SynthesisSpec | None = None, *, once: int | None = None
    ) -> SynthesizeResult:
        """Send synthesize request. Audio plays on server.

        *spec* bundles the voice/provider/rate parameters; *once* is the dedup
        TTL. See :class:`SynthesizeResult` for the returned fields -- in
        particular the ``deduped`` flag that surfaces when ``once=<ttl>`` matches
        an identical text already played within the window.
        """
        return self._run(  # type: ignore[no-any-return]
            self._call("synthesize", text, spec, once=once)
        )

    def chime(self, signal: str) -> None:
        """Play a bundled chime asset."""
        self._run(self._call("chime", signal))

    def record(self, text: str, spec: SynthesisSpec | None = None) -> bytes:
        """Synthesize and return MP3 bytes (no playback)."""
        return self._run(self._call("record", text, spec))  # type: ignore[no-any-return]

    def voices(self, provider: str | None = None) -> list[str]:
        """List available voices."""
        return self._run(self._call("voices", provider))  # type: ignore[no-any-return]

    def health(self) -> dict[str, object]:
        """Check daemon health."""
        return self._run(self._call("health"))  # type: ignore[no-any-return]

    def music(
        self,
        mode: str,
        *,
        style: str | None = None,
        vibe: str | None = None,
        vibe_tags: str | None = None,
        owner_id: str | None = None,
        name: str | None = None,
        prompts: PromptSet | None = None,
    ) -> dict[str, Any]:
        """Start or stop music playback (legacy owner-gated wire; see program_*)."""
        return self._run(  # type: ignore[no-any-return]
            self._call(
                "music",
                mode,
                style=style,
                vibe=vibe,
                vibe_tags=vibe_tags,
                owner_id=owner_id,
                name=name,
                prompts=prompts,
            )
        )

    def music_play(self, name: str, *, owner_id: str | None = None) -> dict[str, Any]:
        """Replay a saved track by name (legacy wire)."""
        return self._run(  # type: ignore[no-any-return]
            self._call("music_play", name, owner_id=owner_id)
        )

    def music_list(self) -> dict[str, Any]:
        """List saved music tracks (legacy wire)."""
        return self._run(self._call("music_list"))  # type: ignore[no-any-return]

    def music_vibe(
        self,
        *,
        vibe: str | None = None,
        vibe_tags: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a vibe update for the currently playing music (legacy wire)."""
        return self._run(  # type: ignore[no-any-return]
            self._call("music_vibe", vibe=vibe, vibe_tags=vibe_tags, owner_id=owner_id)
        )

    def music_next(self, *, owner_id: str | None = None) -> dict[str, Any]:
        """Skip to the next generated track (legacy wire)."""
        return self._run(  # type: ignore[no-any-return]
            self._call("music_next", owner_id=owner_id)
        )

    # -- program surface (session-free; the daemon-facing wire, design section 4)

    def program_status(self) -> dict[str, Any]:
        """Return the daemon's authoritative Program status."""
        return self._run(self._call("program_status"))  # type: ignore[no-any-return]

    def program_on(
        self,
        *,
        style: str | None = None,
        name: str | None = None,
        prompts: PromptSet | None = None,
    ) -> dict[str, Any]:
        """Turn a Program on from authored prompts."""
        return self._run(  # type: ignore[no-any-return]
            self._call("program_on", style=style, name=name, prompts=prompts)
        )

    def program_off(self) -> dict[str, Any]:
        """Turn the active Program off."""
        return self._run(self._call("program_off"))  # type: ignore[no-any-return]

    def program_next(self) -> dict[str, Any]:
        """Advance to another Part."""
        return self._run(self._call("program_next"))  # type: ignore[no-any-return]

    def program_play(self, name: str, *, part: int | None = None) -> dict[str, Any]:
        """Play a saved Program from disk, optionally at a specific 1-based part."""
        return self._run(  # type: ignore[no-any-return]
            self._call("program_play", name, part=part)
        )

    def program_loop(self, name: str) -> dict[str, Any]:
        """Play a saved Program and rotate on every track end."""
        return self._run(self._call("program_loop", name))  # type: ignore[no-any-return]

    def program_list(self) -> dict[str, Any]:
        """List every saved Program, grouped."""
        return self._run(self._call("program_list"))  # type: ignore[no-any-return]
