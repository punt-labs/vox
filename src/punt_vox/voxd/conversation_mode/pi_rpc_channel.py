"""The pipe pair to one ``pi --mode rpc`` process: JSONL commands in, records out.

:class:`PiRpcChannel` owns the subprocess and nothing above the wire: it
writes one command per line, reads one :class:`~.pi_rpc_record.PiRpcRecord`
per line under a caller-supplied inactivity bound, drains stderr in the
background (a child blocked on a full stderr pipe stops writing stdout, the
classic deadlock), and tears the process down. Every way the process can
stop being usable -- it exited, it went silent past the bound, it wrote a
line that is not JSON, its stdin broke -- surfaces as one exception type,
:class:`~.call_agent_errors.CallAgentEndedError`, because to the call each
of them means the same thing: this agent is gone.

The process is held by ``asyncio.create_subprocess_exec`` rather than a
blocking ``subprocess.Popen``: the call's whole loop is a single asyncio
event loop, and a blocking read here would freeze microphone capture for the
length of every reply.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections import deque
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.call_agent_errors import (
    CallAgentEndedError,
    CallAgentSpawnError,
)
from punt_vox.voxd.conversation_mode.pi_rpc_record import (
    MalformedRecordError,
    PiRpcRecord,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from punt_vox.voxd.conversation_mode.call_agent_launch import CallAgentLaunch

__all__ = ["PiRpcChannel"]

# One RPC record per line, and some records are large: ``agent_end`` repeats
# every message of the run, tool results included. asyncio's default 64KB
# line limit would end a call over one long file read.
_LINE_LIMIT = 32 * 1024 * 1024

# How long an orderly shutdown (stdin closed) may take before the process is
# killed outright.
_SHUTDOWN_GRACE_S = 5.0

_STDERR_TAIL_LINES = 20

# How long to wait for stderr's last lines once stdout has closed.
_STDERR_SETTLE_S = 1.0


@final
class PiRpcChannel:
    """One live ``pi --mode rpc`` process, spoken to over its stdin and stdout."""

    __slots__ = ("_drain", "_process", "_stderr_tail")
    _process: asyncio.subprocess.Process
    _stderr_tail: deque[str]
    _drain: asyncio.Task[None]

    def __new__(cls, process: asyncio.subprocess.Process) -> Self:
        self = super().__new__(cls)
        self._process = process
        self._stderr_tail = deque(maxlen=_STDERR_TAIL_LINES)
        self._drain = asyncio.ensure_future(self._drain_stderr())
        return self

    @classmethod
    async def open(cls, launch: CallAgentLaunch, cwd: Path) -> Self:
        """Start the process *launch* describes, in *cwd*, with its stripped env."""
        try:
            process = await asyncio.create_subprocess_exec(
                *launch.argv(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=launch.env(os.environ),
                limit=_LINE_LIMIT,
            )
        except FileNotFoundError as exc:
            msg = f"{launch.program} not found on PATH -- install pi to place a call"
            raise CallAgentSpawnError(msg) from exc
        return cls(process)

    async def send(self, command: Mapping[str, object]) -> None:
        """Write one JSONL command."""
        stdin = self._process.stdin
        if stdin is None or stdin.is_closing():
            msg = "call agent stdin is closed"
            raise CallAgentEndedError(msg)
        try:
            stdin.write(json.dumps(command).encode() + b"\n")
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            msg = f"call agent stdin broke: {self.stderr_tail()}"
            raise CallAgentEndedError(msg) from exc

    async def receive(self, inactivity_s: float) -> PiRpcRecord:
        """Return the next record, or raise if none arrives within *inactivity_s*.

        The bound restarts with every record, of any kind -- a tool-heavy
        reply streams tool events the whole time it works, so only genuine
        silence trips it, never a long but busy turn.
        """
        stdout = self._process.stdout
        if stdout is None:
            msg = "call agent has no stdout pipe"
            raise CallAgentEndedError(msg)
        try:
            line = await asyncio.wait_for(stdout.readline(), timeout=inactivity_s)
        except TimeoutError:
            msg = f"no event from the call agent in {inactivity_s:.0f}s"
            raise CallAgentEndedError(msg) from None
        except (ValueError, asyncio.LimitOverrunError) as exc:
            msg = "call agent wrote an over-long record"
            raise CallAgentEndedError(msg) from exc
        if not line:
            await self._settle_stderr()
            msg = f"call agent exited: {self.stderr_tail()}"
            raise CallAgentEndedError(msg)
        try:
            return PiRpcRecord.parse(line)
        except MalformedRecordError as exc:
            raise CallAgentEndedError(str(exc)) from exc

    async def close(self) -> None:
        """Ask for an orderly exit (stdin closed), killing after a short grace."""
        stdin = self._process.stdin
        if stdin is not None and not stdin.is_closing():
            stdin.close()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=_SHUTDOWN_GRACE_S)
        except TimeoutError:
            await self.kill()
            return
        await self._stop_drain()

    async def kill(self) -> None:
        """Kill the process now and reap it."""
        if self._process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._process.kill()
        await self._process.wait()
        await self._stop_drain()

    def stderr_tail(self) -> str:
        """Return the last few lines pi wrote to stderr, for diagnostics only."""
        return " | ".join(self._stderr_tail) or "(no stderr)"

    async def _drain_stderr(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return
        async for raw in stderr:
            self._stderr_tail.append(raw.decode(errors="replace").rstrip())

    async def _settle_stderr(self) -> None:
        """Let the drain catch up after stdout closed, so the error names why.

        stdout reaching EOF does not mean stderr's last lines -- usually the
        reason pi exited -- have been read yet. Bounded: a process that
        closed stdout but keeps stderr open must not hang the call.
        """
        await asyncio.wait({self._drain}, timeout=_STDERR_SETTLE_S)

    async def _stop_drain(self) -> None:
        if not self._drain.done():
            self._drain.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._drain
