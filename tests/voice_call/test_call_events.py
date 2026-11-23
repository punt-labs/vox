from __future__ import annotations

import asyncio
import io
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

from punt_vox.voxd.voice_agent import Conversation
from punt_vox.voxd.voice_agent.contracts import SessionOptions
from punt_vox.voxd.voice_agent.events import (
    AgentResponse,
    AgentResponseCorrection,
    ClientToolCall,
    Metadata,
    Ping,
    UserTranscript,
)
from punt_vox.voxd.voice_agent.trace import EventTrace
from punt_vox.voxd.voice_call.boss_tools import BossTools
from punt_vox.voxd.voice_call.call import VoiceAgentCall
from punt_vox.voxd.voice_call.speaker import AgentSpeaker

from .fakes import FakePlayback, FakeWorkers, payload

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    import pytest

    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.voice_call.call import Opener


class _Silent:
    async def chunks(self) -> AsyncIterator[bytes]:
        silence: tuple[bytes, ...] = ()
        for pcm in silence:
            yield pcm


class _Peer:
    """Play back a fixed run of events, then hang up."""

    def __init__(self, events: list[object]) -> None:
        self.metadata = Metadata("conv_1", "pcm_16000", "pcm_16000")
        self._events = events
        self.results: list[str] = []

    async def receive(self) -> object:
        await asyncio.sleep(0.01)  # lets a dispatched tool call finish
        if not self._events:
            raise EOFError
        return self._events.pop(0)

    async def send_audio(self, pcm: bytes) -> None:
        del pcm

    async def send_tool_result(
        self, tool_call_id: str, result: str, *, is_error: bool
    ) -> None:
        del is_error
        self.results.append(tool_call_id + result)


def _call(peer: _Peer) -> VoiceAgentCall:
    @asynccontextmanager
    async def opener(
        url: str, options: SessionOptions, *, trace: EventTrace
    ) -> AsyncGenerator[_Peer]:
        del url, options, trace
        yield peer

    workers = FakeWorkers()
    return VoiceAgentCall(
        BossTools(workers, workers),
        _Silent(),
        AgentSpeaker(cast("PlaybackQueue", FakePlayback())),
        trace=cast("EventTrace", None),
        opener=cast("Opener", opener),
    )


async def _dial() -> tuple[str, SessionOptions]:
    return "ws://peer", SessionOptions("prompt", ())


async def test_permission_receipt_trace_keeps_prompt_but_not_words() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234", prompt_epoch=4)]
    stream = io.StringIO()
    call = VoiceAgentCall(
        BossTools(workers, workers),
        _Silent(),
        AgentSpeaker(cast("PlaybackQueue", FakePlayback())),
        trace=EventTrace(stream),
    )
    await call._answer(
        AsyncMock(spec=Conversation),
        ClientToolCall(
            "approval",
            "steer_worker",
            {
                "worker_id": "abcd1234",
                "words": "private spoken answer",
                "choice": "approve",
                "prompt_seq": 4,
            },
            expects_response=True,
        ),
    )
    trace = json.loads(stream.getvalue())
    assert trace["type"] == "worker_steer_receipt"
    assert trace["detail"]["choice"] == "approve"
    assert trace["detail"]["prompt_seq"] == 4
    assert trace["detail"]["heard"] is True
    assert "private spoken answer" not in stream.getvalue()


async def test_reissued_steer_ids_share_a_prompt_but_not_the_next_prompt() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234", prompt_epoch=4)]
    call = VoiceAgentCall(
        BossTools(workers, workers),
        _Silent(),
        AgentSpeaker(cast("PlaybackQueue", FakePlayback())),
        trace=MagicMock(spec=EventTrace),
    )
    conv = AsyncMock(spec=Conversation)
    words: dict[str, object] = {"worker_id": "abcd1234", "words": "Continue."}
    for identity in ("interrupted", "reissued"):
        await call._answer(
            conv, ClientToolCall(identity, "steer_worker", words, expects_response=True)
        )
    assert workers.steered == [("abcd1234", "Continue.", False)]
    workers.known = [payload("abcd1234", prompt_epoch=9)]
    await call._answer(
        conv,
        ClientToolCall("next-prompt", "steer_worker", words, expects_response=True),
    )
    assert workers.steered == [
        ("abcd1234", "Continue.", False),
        ("abcd1234", "Continue.", False),
    ]
    assert [entry.args[0] for entry in conv.send_tool_result.await_args_list] == [
        "interrupted",
        "reissued",
        "next-prompt",
    ]


async def test_the_transcript_keeps_both_sides_and_ignores_other_events() -> None:
    peer = _Peer(
        [
            UserTranscript("start a worker", None),
            AgentResponse("on it", None, "r1"),
            AgentResponseCorrection("on it", "on", 1, "r0"),  # not in the history
            ClientToolCall("t1", "list_workers", {}, expects_response=False),
            Ping(2),
        ]
    )
    call = _call(peer)
    await call.run(_dial)
    status = call.status()
    assert status["state"] == "ended"
    assert status["transcript"] == [
        {"role": "user", "text": "start a worker"},
        {"role": "agent", "text": "on it"},
    ]
    assert status["tool_calls"] == [
        {"tool": "list_workers", "arguments": {}, "error": False}
    ]
    assert peer.results == []  # the agent asked for no answer


async def test_an_unexpected_failure_is_logged_and_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken() -> tuple[str, SessionOptions]:
        raise KeyError("agent_id")

    call = _call(_Peer([]))
    await call.run(broken)
    assert call.status()["state"] == "failed"
    assert "agent_id" in str(call.status()["error"])
    assert "Mode B call failed" in caplog.text
