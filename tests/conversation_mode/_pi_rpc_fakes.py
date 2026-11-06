"""A scripted stand-in for one ``pi --mode rpc`` process, at the subprocess boundary.

:class:`FakePiProcess` duck-types the slice of ``asyncio.subprocess.Process``
that :class:`~punt_vox.voxd.conversation_mode.pi_rpc_channel.PiRpcChannel`
uses -- stdin, stdout, stderr, ``kill``, ``wait``, ``returncode`` -- and
answers each ``prompt`` command written to its stdin with the next scripted
:class:`PiReply`, emitted as the same JSONL record shapes a live pi run
produces (``tests/conversation_mode/test_pi_rpc_record.py`` pins those).
Everything above the pipe -- the channel, the record parsing, the call agent
and its state machine -- runs for real.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Self, final

__all__ = ["FakePiProcess", "PiReply", "SpawnRecorder", "ToolUse"]


@final
@dataclass(frozen=True, slots=True)
class ToolUse:
    """One tool call the scripted reply makes before its text."""

    name: str
    args: dict[str, str]
    result: str = "ok"
    # The pi-side guard refused the call: it ends as an error, nothing read.
    blocked: bool = False
    # The guard's refusal text, when the test cares which refusal it was.
    reason: str = ""


@final
@dataclass(frozen=True, slots=True)
class PiReply:
    """How the fake answers one prompt."""

    text: str = "ok"
    tools: tuple[ToolUse, ...] = ()
    # The provider failed after the prompt was accepted: pi reports it in an
    # errored message_end and still settles.
    error: str = ""
    # pi refused the command outright (``success: false``); nothing follows.
    refuse: str = ""
    # Stop emitting after the response record, as a hung process would.
    hang: bool = False
    # Exit (stdout EOF) after the response record.
    die: bool = False
    # Seconds between consecutive records, to pace a long but busy reply.
    pace_s: float = 0.0
    # Attempts the provider failed (one error each) that pi auto-retried
    # before the attempt this reply describes; with ``error`` set too, the
    # retries were exhausted.
    retried: tuple[str, ...] = ()
    # Reply text each retried attempt wrote before the provider failed it.
    partial: str = ""
    # The run was aborted: it settles with ``aborted: true`` and no reply
    # (after any ``retried`` attempts).
    aborted: bool = False
    # An extension handler threw: pi reports it in an ``extension_error``.
    extension_error: str = ""


def _record(fields: dict[str, object]) -> bytes:
    return json.dumps(fields).encode() + b"\n"


def _update(event: dict[str, object]) -> bytes:
    return _record({"type": "message_update", "assistantMessageEvent": event})


@final
class _FakeStdin:
    __slots__ = ("_closed", "_process")

    def __init__(self, process: FakePiProcess) -> None:
        self._process = process
        self._closed = False

    def write(self, data: bytes) -> None:
        for line in data.splitlines():
            self._process.receive(json.loads(line))

    async def drain(self) -> None:
        if self._process.returncode is not None:
            raise BrokenPipeError

    def close(self) -> None:
        self._closed = True
        if self._process.exits_on_stdin_close:
            self._process.exit(0)

    def is_closing(self) -> bool:
        return self._closed


@final
class FakePiProcess:
    """A pi RPC process that answers each prompt with the next :class:`PiReply`."""

    def __init__(
        self, replies: Sequence[PiReply], *, exits_on_stdin_close: bool = True
    ) -> None:
        self._replies = list(replies)
        self.exits_on_stdin_close = exits_on_stdin_close
        self.commands: list[dict[str, object]] = []
        self.stdin = _FakeStdin(self)
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode: int | None = None
        self.killed = False
        self._exited = asyncio.Event()
        self._feeders: list[asyncio.Task[None]] = []

    @classmethod
    def answering(cls, *texts: str) -> Self:
        """Return a process that answers each prompt with one of *texts*, in order."""
        return cls([PiReply(text=text) for text in texts])

    @property
    def prompts(self) -> list[str]:
        """The ``message`` of every prompt command received, in order."""
        return [str(c["message"]) for c in self.commands if c.get("type") == "prompt"]

    def receive(self, command: dict[str, object]) -> None:
        self.commands.append(command)
        if command.get("type") != "prompt":
            return
        reply = self._replies.pop(0) if self._replies else PiReply()
        self._feeders.append(asyncio.ensure_future(self._feed(reply)))

    def kill(self) -> None:
        self.killed = True
        self.exit(-9)

    def exit(self, code: int) -> None:
        if self.returncode is not None:
            return
        self.returncode = code
        for feeder in self._feeders:
            feeder.cancel()
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    async def _feed(self, reply: PiReply) -> None:
        for line in self._records(reply):
            if reply.pace_s:
                await asyncio.sleep(reply.pace_s)
            if self.returncode is not None:
                return
            self.stdout.feed_data(line)
        if reply.die:
            self.stderr.feed_data(b"pi: fatal error\n")
            self.exit(1)

    @staticmethod
    def _records(reply: PiReply) -> list[bytes]:
        if reply.refuse:
            return [
                _record(
                    {
                        "type": "response",
                        "command": "prompt",
                        "success": False,
                        "error": reply.refuse,
                    }
                )
            ]
        records = [
            _record({"type": "response", "command": "prompt", "success": True}),
        ]
        if reply.hang or reply.die:
            return records
        records.append(_record({"type": "agent_start"}))
        if reply.extension_error:
            records.append(
                _record(
                    {
                        "type": "extension_error",
                        "extensionPath": "/x/secret_guard.js",
                        "event": "tool_call",
                        "error": reply.extension_error,
                    }
                )
            )
        for attempt, error in enumerate(reply.retried, start=1):
            records += [
                _update({"type": "text_delta", "delta": piece})
                for piece in re.findall(r"\S+\s*", reply.partial)
            ]
            records += [
                _record(
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "stopReason": "error",
                            "errorMessage": error,
                        },
                    }
                ),
                _record({"type": "agent_end", "messages": []}),
                _record(
                    {
                        "type": "auto_retry_start",
                        "attempt": attempt,
                        "errorMessage": error,
                    }
                ),
                _record({"type": "agent_start"}),
            ]
        if reply.aborted:
            return [
                *records,
                _record({"type": "agent_end", "messages": []}),
                _record({"type": "agent_settled", "aborted": True}),
            ]
        for index, tool in enumerate(reply.tools):
            call_id = f"call_{index}"
            refusal = tool.reason or f"vox secret guard: {tool.args}"
            result = refusal if tool.blocked else tool.result
            records += [
                _update(
                    {
                        "type": "toolcall_end",
                        "toolCall": {"name": tool.name, "arguments": tool.args},
                    }
                ),
                _record(
                    {
                        "type": "tool_execution_start",
                        "toolCallId": call_id,
                        "toolName": tool.name,
                        "args": tool.args,
                    }
                ),
                _record(
                    {
                        "type": "tool_execution_end",
                        "toolCallId": call_id,
                        "toolName": tool.name,
                        "isError": tool.blocked,
                        "result": {"content": [{"type": "text", "text": result}]},
                    }
                ),
            ]
        records += [
            _update({"type": "text_delta", "delta": piece})
            for piece in re.findall(r"\S+\s*", reply.text)
        ]
        stop = {"role": "assistant", "stopReason": "error" if reply.error else "stop"}
        if reply.error:
            stop["errorMessage"] = reply.error
        records += [
            _record({"type": "message_end", "message": stop}),
            _record({"type": "agent_end", "messages": []}),
        ]
        if reply.retried:
            ended: dict[str, object] = {"type": "auto_retry_end"}
            ended |= (
                {"success": False, "finalError": reply.error}
                if reply.error
                else {"success": True}
            )
            records.append(_record(ended))
        records.append(_record({"type": "agent_settled", "aborted": False}))
        return records


@dataclass
class SpawnRecorder:
    """Captures what ``asyncio.create_subprocess_exec`` was asked to run."""

    process: FakePiProcess
    argv: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)

    async def __call__(self, *argv: str, **kwargs: object) -> FakePiProcess:
        self.argv = argv
        env = kwargs.get("env")
        assert isinstance(env, dict)
        self.env = {str(k): str(v) for k, v in env.items()}
        return self.process
