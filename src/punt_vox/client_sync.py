"""Synchronous facade over :class:`~punt_vox.client.VoxClient` for hooks and CLI.

``VoxClientSync`` creates a fresh connection per call and drives the async client
to completion -- simple and correct, because hooks and CLI commands are
short-lived so connection pooling adds no value. It is a thin humble object: each
method delegates to the matching :class:`VoxClient` coroutine through ``_call``,
which connects, invokes, and closes. The event-loop plumbing lives in a composed
:class:`_SyncRunner` so the facade owns only the "which method, which args"
concern.
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


class _SyncRunner:
    """Drive an async coroutine to completion from synchronous code.

    When the caller is already inside a running event loop (e.g. the MCP
    server), ``asyncio.run`` would raise, so the coroutine is driven on a
    fresh loop in a worker thread instead.
    """

    __slots__ = ()

    def run(self, coro: Any) -> Any:
        """Run *coro* to completion, on this loop or a worker-thread loop."""
        if self._loop_is_running():
            return self._run_in_thread(coro)
        return asyncio.run(coro)

    @staticmethod
    def _loop_is_running() -> bool:
        """Return True when called from within a running event loop."""
        try:
            return asyncio.get_running_loop().is_running()
        except RuntimeError:
            return False

    @staticmethod
    def _run_in_thread(coro: Any) -> Any:
        """Drive *coro* to completion on a fresh loop in a worker thread."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()


class VoxClientSync:
    """Synchronous wrapper around :class:`VoxClient` for hooks and CLI.

    Creates a fresh connection per call. Simple and correct -- hooks and
    CLI commands are short-lived, so connection pooling adds no value.
    """

    __slots__ = ("_host", "_port", "_runner", "_token")

    _host: str
    _port: int | None
    _token: str | None
    _runner: _SyncRunner

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
        self._runner = _SyncRunner()
        return self

    def _make_client(self) -> VoxClient:
        return VoxClient(host=self._host, port=self._port, token=self._token)

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
        return self._runner.run(  # type: ignore[no-any-return]
            self._call("synthesize", text, spec, once=once)
        )

    def chime(self, signal: str) -> None:
        """Play a bundled chime asset."""
        self._runner.run(self._call("chime", signal))

    def record(self, text: str, spec: SynthesisSpec | None = None) -> bytes:
        """Synthesize and return MP3 bytes (no playback)."""
        return self._runner.run(self._call("record", text, spec))  # type: ignore[no-any-return]

    def voices(self, provider: str | None = None) -> list[str]:
        """List available voices."""
        return self._runner.run(self._call("voices", provider))  # type: ignore[no-any-return]

    def health(self) -> dict[str, object]:
        """Check daemon health."""
        return self._runner.run(self._call("health"))  # type: ignore[no-any-return]

    # -- program surface (session-free; the daemon-facing wire, design section 4)

    def program_status(self) -> dict[str, Any]:
        """Return the daemon's authoritative Program status."""
        return self._runner.run(self._call("program_status"))  # type: ignore[no-any-return]

    def program_on(
        self,
        *,
        style: str | None = None,
        vibe: str | None = None,
        name: str | None = None,
        prompts: PromptSet | None = None,
    ) -> dict[str, Any]:
        """Turn a Program on from the session vibe and authored prompts."""
        return self._runner.run(  # type: ignore[no-any-return]
            self._call("program_on", style=style, vibe=vibe, name=name, prompts=prompts)
        )

    def program_off(self) -> dict[str, Any]:
        """Turn the active Program off."""
        return self._runner.run(self._call("program_off"))  # type: ignore[no-any-return]

    def program_next(self) -> dict[str, Any]:
        """Advance to another Part."""
        return self._runner.run(self._call("program_next"))  # type: ignore[no-any-return]

    def program_select(
        self,
        *,
        style: str | None = None,
        vibe: str | None = None,
        name: str | None = None,
        album_id: str | None = None,
    ) -> dict[str, Any]:
        """Replay a Selection resolved by album id (direct) or by tags (F#7)."""
        return self._runner.run(  # type: ignore[no-any-return]
            self._call(
                "program_select", style=style, vibe=vibe, name=name, album_id=album_id
            )
        )

    def program_list(self) -> dict[str, Any]:
        """List every album, grouped."""
        return self._runner.run(self._call("program_list"))  # type: ignore[no-any-return]
