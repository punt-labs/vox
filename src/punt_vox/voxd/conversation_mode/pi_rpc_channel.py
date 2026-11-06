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
import logging
import os
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.call_agent_errors import (
    CallAgentEndedError,
    CallAgentSpawnError,
)
from punt_vox.voxd.conversation_mode.pi_rpc_record import (
    MalformedRecordError,
    PiRpcRecord,
)
from punt_vox.voxd.conversation_mode.pi_stderr import PiStderr

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from punt_vox.voxd.conversation_mode.call_agent_launch import CallAgentLaunch

__all__ = ["PiRpcChannel"]

logger = logging.getLogger(__name__)

# One RPC record per line, and some records are large: ``agent_end`` repeats
# every message of the run, tool results included. asyncio's default 64KB
# line limit would end a call over one long file read.
_LINE_LIMIT = 32 * 1024 * 1024

# How long an orderly shutdown (stdin closed) may take before the process is
# killed outright.
_SHUTDOWN_GRACE_S = 5.0

# How long to wait for pi to be reaped once its pipes have closed.
_REAP_S = 1.0


@final
class PiRpcChannel:
    """One live ``pi --mode rpc`` process, spoken to over its stdin and stdout."""

    __slots__ = ("_process", "_stderr")
    _process: asyncio.subprocess.Process
    _stderr: PiStderr

    def __new__(cls, process: asyncio.subprocess.Process) -> Self:
        self = super().__new__(cls)
        self._process = process
        self._stderr = PiStderr(process.stderr)
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
        except OSError as exc:
            msg = f"cannot start {launch.program}: {exc}"
            raise CallAgentSpawnError(msg) from exc
        return cls(process)

    async def send(self, command: Mapping[str, object]) -> None:
        """Write one JSONL command."""
        stdin = self._process.stdin
        if stdin is None or stdin.is_closing():
            raise await self._ended("stdin is closed")
        try:
            stdin.write(json.dumps(command).encode() + b"\n")
            await stdin.drain()
        except OSError as exc:
            raise await self._ended(f"stdin broke ({exc!r})") from exc

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
            raise await self._ended("exited")
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
        except BaseException:
            # Cancelled mid-grace: never leave pi running behind the call.
            await self.kill()
            raise
        await self._stderr.stop()
        if self._process.returncode:
            logger.warning(
                "call agent exited with %s: %s", self._status(), self._stderr.tail()
            )

    async def kill(self) -> None:
        """Kill the process now and reap it."""
        if self._process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._process.kill()
        await self._process.wait()
        await self._stderr.stop()

    async def _ended(self, what: str) -> CallAgentEndedError:
        """Return the error for a process that stopped being usable, with why.

        stdout reaching EOF or stdin breaking does not mean stderr's last
        lines -- usually the reason pi exited -- have been read yet, nor
        that pi has been reaped; both waits are bounded, so a process that
        closed its pipes but lingers cannot hang the call.
        """
        await self._stderr.settle()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._process.wait(), timeout=_REAP_S)
        msg = f"call agent {what} ({self._status()}): {self._stderr.tail()}"
        return CallAgentEndedError(msg)

    def _status(self) -> str:
        code = self._process.returncode
        return "still running" if code is None else f"exit {code}"
