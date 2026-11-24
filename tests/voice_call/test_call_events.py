from __future__ import annotations

import asyncio
import io
import json
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
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
from punt_vox.voxd.voice_call.mic_input import MicInput
from punt_vox.voxd.voice_call.progress import ProgressEvents
from punt_vox.voxd.voice_call.speaker import AgentSpeaker

from .fakes import FakePlayback, FakeWorkers, payload

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from punt_vox.voxd.conversation_mode.mic_audio_source import MicAudioSource
    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.voice_call.call import Opener
    from punt_vox.voxd.voice_call.contracts import CallInput


class _Silent:
    def open(self) -> None:
        pass

    async def chunks(self) -> AsyncIterator[bytes]:
        silence: tuple[bytes, ...] = ()
        for pcm in silence:
            yield pcm

    def close(self) -> None:
        pass


class _DeadMicSource:
    """A MicAudioSource double: the macOS denied capture, zeros without end."""

    def open(self) -> _DeadMicSource:
        return self

    async def chunks(self) -> AsyncGenerator[AudioChunk]:
        while True:
            await asyncio.sleep(0)
            yield AudioChunk(pcm=bytes(640), duration_s=0.02)

    def close(self) -> None:
        pass


def _dead_mic() -> MicInput:
    mic = MicInput(cast("MicAudioSource", _DeadMicSource()))
    mic.open()
    return mic


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


def _call(
    peer: _Peer,
    audio: CallInput | None = None,
    speaker: AgentSpeaker | None = None,  # None: a speaker over a fake playback.
) -> VoiceAgentCall:
    """Build a call on ``peer``; ``audio`` None sends no caller audio at all."""

    @asynccontextmanager
    async def opener(
        url: str, options: SessionOptions, *, trace: EventTrace
    ) -> AsyncGenerator[_Peer]:
        del url, options, trace
        yield peer

    workers = FakeWorkers()
    return VoiceAgentCall(
        BossTools(workers, workers),
        audio if audio is not None else _Silent(),
        speaker or AgentSpeaker(cast("PlaybackQueue", FakePlayback())),
        trace=cast("EventTrace", None),
        opener=cast("Opener", opener),
        progress=ProgressEvents(),
    )


async def _dial() -> tuple[str, SessionOptions]:
    return "ws://peer", SessionOptions("prompt", ())


def _receipt_call() -> tuple[VoiceAgentCall, FakeWorkers, io.StringIO]:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234", prompt_epoch=4)]
    stream = io.StringIO()
    call = VoiceAgentCall(
        BossTools(workers, workers),
        _Silent(),
        AgentSpeaker(cast("PlaybackQueue", FakePlayback())),
        trace=EventTrace(stream),
        opener=Conversation.open,
        progress=ProgressEvents(),
    )
    return call, workers, stream


async def test_permission_receipt_trace_keeps_prompt_but_not_words() -> None:
    call, _, stream = _receipt_call()
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


async def test_ordinary_steer_rejects_a_prompt_sequence_without_a_receipt() -> None:
    call, workers, stream = _receipt_call()
    conv = AsyncMock(spec=Conversation)
    await call._answer(
        conv,
        ClientToolCall(
            "ordinary",
            "steer_worker",
            {
                "worker_id": "abcd1234",
                "words": "Continue.",
                "prompt_seq": "private spoken answer",
            },
            expects_response=True,
        ),
    )
    assert stream.getvalue() == ""
    assert workers.steered == []
    conv.send_tool_result.assert_awaited_once()
    assert conv.send_tool_result.call_args.kwargs["is_error"] is True
    assert "prompt_seq requires choice" in conv.send_tool_result.call_args.args[1]


async def test_ordinary_receipt_trace_has_no_prompt_sequence() -> None:
    call, _, stream = _receipt_call()
    await call._answer(
        AsyncMock(spec=Conversation),
        ClientToolCall(
            "ordinary",
            "steer_worker",
            {"worker_id": "abcd1234", "words": "Continue."},
            expects_response=True,
        ),
    )
    trace = json.loads(stream.getvalue())
    assert trace["detail"]["choice"] is None
    assert "prompt_seq" not in trace["detail"]
    assert trace["detail"]["heard"] is True


async def test_reissued_steer_ids_share_a_prompt_but_not_the_next_prompt() -> None:
    workers = FakeWorkers()
    workers.known = [payload("abcd1234", prompt_epoch=4)]
    call = VoiceAgentCall(
        BossTools(workers, workers),
        _Silent(),
        AgentSpeaker(cast("PlaybackQueue", FakePlayback())),
        trace=MagicMock(spec=EventTrace),
        opener=Conversation.open,
        progress=ProgressEvents(),
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
    assert "Voice call failed" in caplog.text


async def test_a_live_call_logs_its_conversation_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    call = _call(_Peer([]))
    with caplog.at_level(logging.INFO, logger="punt_vox.voxd.voice_call.call_log"):
        await call.run(_dial)
    assert "Call live: conversation conv_1" in [r.message for r in caplog.records]


async def test_each_tool_call_is_logged_with_its_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    peer = _Peer(
        [
            ClientToolCall("t1", "list_workers", {}, expects_response=True),
            ClientToolCall("t2", "start_worker", {"task": " "}, expects_response=True),
            ClientToolCall("t3", "no_such_tool", {}, expects_response=False),
        ]
    )
    call = _call(peer)
    with caplog.at_level(logging.INFO, logger="punt_vox.voxd.voice_call.call_log"):
        await call.run(_dial)
    lines = [r.message for r in caplog.records if r.message.startswith("Call tool")]
    assert lines == [
        "Call tool list_workers: ok",
        "Call tool start_worker: error task must be a non-empty string",
        "Call tool unknown: error unknown tool no_such_tool",
    ]
    assert call.outcome().tool_calls == 3


@pytest.mark.parametrize(
    ("wire", "failure"),
    [("not json", "JSONDecodeError"), ("{}", "ValueError"), ("[]", "ValueError")],
)
async def test_an_unreadable_tool_error_reply_does_not_end_the_call(
    caplog: pytest.LogCaptureFixture, wire: str, failure: str
) -> None:
    """A runner error the wire cannot parse is answered, not raised into the group.

    Valid JSON of the wrong shape counts: a missing key or a list must not
    raise a KeyError or TypeError past the one ValueError the call contains.
    """

    class _BrokenTools:
        replayed = 0

        async def answer(
            self, name: str, arguments: dict[str, object]
        ) -> tuple[str, bool]:
            del name, arguments
            return wire, True

    peer = _Peer([ClientToolCall("t1", "list_workers", {}, expects_response=True)])
    call = _call(peer)
    call._tools = cast("BossTools", _BrokenTools())
    with caplog.at_level(logging.INFO, logger="punt_vox.voxd.voice_call.call_log"):
        await call.run(_dial)
    assert call.status()["state"] == "ended"
    assert call.status()["error"] is None
    assert peer.results == ['t1{"error": "tool reply was unreadable"}']
    assert call.status()["tool_calls"] == [
        {"tool": "list_workers", "arguments": {}, "error": True}
    ]
    lines = [r.message for r in caplog.records if r.message.startswith("Call tool")]
    assert lines == [
        f"Call tool list_workers: unreadable error reply ({failure})",
        "Call tool list_workers: error tool reply was unreadable",
    ]


async def test_a_silent_microphone_fails_the_live_call_naming_the_remedies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _OpenLine(_Peer):
        """A peer that never hangs up: only the caller's side can end the call."""

        async def receive(self) -> object:
            await asyncio.sleep(3600)
            raise EOFError

    call = _call(_OpenLine([]), _dead_mic())
    with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.voice_call"):
        async with asyncio.timeout(10):
            await call.run(_dial)
    status = call.status()
    assert status["state"] == "failed"
    error = str(status["error"])
    assert error.startswith("no microphone signal")
    assert "System Settings > Privacy & Security > Microphone" in error
    assert "terminal" in error
    assert call.outcome().error == error
    assert "Voice call failed" not in caplog.text  # an expected failure, not a bug


async def test_a_silence_failure_still_closes_the_speaker_and_departs() -> None:
    class _OpenLine(_Peer):
        async def receive(self) -> object:
            await asyncio.sleep(3600)
            raise EOFError

    speaker = AsyncMock(spec=AgentSpeaker)
    departures: list[dict[str, int]] = []

    async def depart(reported: dict[str, int]) -> None:
        departures.append(reported)

    call = _call(
        _OpenLine([]),
        _dead_mic(),
        speaker=speaker,
    )
    async with asyncio.timeout(10):
        await call.run(_dial, depart=depart)
    assert call.status()["state"] == "failed"
    speaker.close.assert_awaited_once_with()
    speaker.finish.assert_not_awaited()
    assert departures == [{}]


async def test_the_outcome_counts_every_tool_call_past_the_status_history() -> None:
    """The status keeps the last 20 tool calls; the end line counts them all."""
    call = _call(_Peer([]))
    conv = AsyncMock(spec=Conversation)
    for n in range(25):
        await call._answer(
            conv, ClientToolCall(f"t{n}", "list_workers", {}, expects_response=False)
        )
    shown = cast("list[dict[str, object]]", call.status()["tool_calls"])
    assert len(shown) == 20
    assert str(call.outcome()) == "connecting, 25 tool calls"
