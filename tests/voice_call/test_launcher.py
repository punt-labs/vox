"""Tests for :mod:`punt_vox.voxd.voice_call.launcher`.

The launcher assembles one call from a ``call_start`` request. These tests
drive :meth:`CallLauncher.launch` with a peer that hangs up at once, a fake
``sounddevice`` for the microphone path, and a connector double in place of
the ElevenLabs account, and read what the launcher built: the input it chose,
the trace it reserved, the log lines it emitted, and the call it returned.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
from conversation_mode._mic_fakes import (
    FakeBackend,
    FakeInputStream,
    FakePortAudioError,
    fake_sounddevice,
)

from punt_vox.voxd.voice_agent.events import Metadata
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_agent.session import Conversation
from punt_vox.voxd.voice_call.agent_operations import AgentOperations
from punt_vox.voxd.voice_call.launcher import CallLauncher
from punt_vox.voxd.voice_call.progress import ProgressEvents
from punt_vox.voxd.voice_call.return_brief import ReturnBrief

from .fakes import FakePlayback, FakeWorkers

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from contextlib import AbstractContextManager

    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.voice_agent.contracts import SessionOptions
    from punt_vox.voxd.voice_agent.trace import EventTrace
    from punt_vox.voxd.voice_call.call import Opener
    from punt_vox.voxd.voice_call.contracts import ProjectAllowlist

_LOG = "punt_vox.voxd.voice_call.call_log"


class _Connector:
    """An account that signs one URL and records every teardown."""

    def __init__(self) -> None:
        self.connections: list[tuple[str, ...]] = []
        self.teardowns = 0

    def connection(self, tools: object) -> tuple[str, tuple[str, ...]]:
        self.connections.append(
            tuple(str(tool) for tool in cast("tuple[object, ...]", tools))
        )
        return "ws://peer", ("tool_a",)

    def teardown(self) -> None:
        self.teardowns += 1


class _HangUp:
    """A conversation whose peer hangs up as soon as it is answered."""

    def __init__(self) -> None:
        self.metadata = Metadata("conv_launch", "pcm_16000", "pcm_16000")
        self.sent: list[bytes] = []

    async def receive(self) -> object:
        await asyncio.sleep(0.01)
        raise EOFError

    async def send_audio(self, pcm: bytes) -> None:
        self.sent.append(pcm)

    async def send_tool_result(
        self, tool_call_id: str, result: str, *, is_error: bool
    ) -> None:
        del tool_call_id, result, is_error


class _Opener:
    """Record each dial and hand the call a :class:`_HangUp` peer."""

    def __init__(self) -> None:
        self.dials: list[tuple[str, SessionOptions]] = []
        self.peers: list[_HangUp] = []
        self.traces: list[EventTrace] = []

    @asynccontextmanager
    async def __call__(
        self, url: str, options: SessionOptions, *, trace: EventTrace
    ) -> AsyncGenerator[_HangUp]:
        self.traces.append(trace)
        self.dials.append((url, options))
        peer = _HangUp()
        self.peers.append(peer)
        yield peer


class _HoldMusic:
    """Count how many times the hold-music context was entered and left."""

    def __init__(self) -> None:
        self.entered = 0
        self.left = 0

    def __call__(self) -> AbstractContextManager[None]:
        return self

    def __enter__(self) -> None:
        self.entered += 1

    def __exit__(self, *exc_info: object) -> None:
        self.left += 1


def _launcher(
    trace_dir: Path,
    *,
    connector: _Connector | None = None,  # None: an account with no key.
    opener: _Opener | None = None,  # None: the real conversation opener.
    hold_music: Callable[[], AbstractContextManager[None]] = nullcontext,
) -> CallLauncher:
    workers = FakeWorkers()
    return CallLauncher(
        workers,
        cast("ProjectAllowlist", workers),
        cast("PlaybackQueue", FakePlayback()),
        AgentOperations(connector),
        progress=ProgressEvents(),
        hold_music=hold_music,
        trace_dir=trace_dir,
        opener=cast("Opener", opener) if opener is not None else Conversation.open,
    )


def _script(tmp_path: Path) -> list[dict[str, object]]:
    pcm = tmp_path / "turn.pcm"
    pcm.write_bytes(b"\x05\x00" * 320)
    return [{"pcm_path": str(pcm), "pause_s": 0.0}]


class TestEchoGuard:
    @pytest.mark.parametrize(
        "declaration", [{}, {"echo_control": True}, {"echo_control": "speakers"}]
    )
    async def test_a_mic_call_without_echo_protection_is_refused_before_any_work(
        self, tmp_path: Path, declaration: dict[str, object]
    ) -> None:
        connector = _Connector()
        launcher = _launcher(tmp_path, connector=connector)
        with pytest.raises(ValueError, match="Live calls require echo protection"):
            await launcher.launch(uuid4(), {"type": "call_start", **declaration})
        assert connector.connections == []
        assert list(tmp_path.iterdir()) == []

    async def test_the_refusal_names_both_remedies(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError) as failure:
            await _launcher(tmp_path).launch(uuid4(), {"type": "call_start"})
        assert "vox call --headset" in str(failure.value)
        assert "vox call --aec" in str(failure.value)

    async def test_a_script_call_needs_no_echo_declaration(
        self, tmp_path: Path
    ) -> None:
        opener = _Opener()
        launcher = _launcher(tmp_path, connector=_Connector(), opener=opener)
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        await active.wait_connected(5)
        await active.cancel()
        assert len(opener.dials) == 1


class TestInputSelection:
    async def test_a_script_request_feeds_the_script_to_the_peer(
        self, tmp_path: Path
    ) -> None:
        opener = _Opener()
        launcher = _launcher(tmp_path, connector=_Connector(), opener=opener)
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        await active.wait_connected(5)
        await asyncio.sleep(0.05)
        await active.cancel()
        (peer,) = opener.peers
        assert peer.sent
        assert peer.sent[0].startswith(b"\x05\x00" * 320)

    async def test_a_malformed_script_is_refused_before_any_work(
        self, tmp_path: Path
    ) -> None:
        connector = _Connector()
        launcher = _launcher(tmp_path, connector=connector)
        with pytest.raises(ValueError, match="script must be a list"):
            await launcher.launch(uuid4(), {"type": "call_start", "script": "x"})
        assert connector.connections == []
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize("echo_control", ["headset", "aec"])
    async def test_a_mic_request_captures_through_portaudio(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, echo_control: str
    ) -> None:
        backend = fake_sounddevice(monkeypatch)
        opener = _Opener()
        launcher = _launcher(tmp_path, connector=_Connector(), opener=opener)
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "echo_control": echo_control}
        )
        await active.wait_connected(5)
        await asyncio.sleep(0.05)
        await active.cancel()
        (stream,) = backend.created
        assert stream.kwargs["samplerate"] == 16_000
        assert stream.closed


class TestTraceAndLog:
    async def test_the_trace_is_reserved_under_the_call_id(
        self, tmp_path: Path
    ) -> None:
        call_id = uuid4()
        launcher = _launcher(tmp_path, connector=_Connector(), opener=_Opener())
        active = await launcher.launch(
            call_id, {"type": "call_start", "script": _script(tmp_path)}
        )
        await active.cancel()
        trace = tmp_path / f"call-{call_id.hex}.jsonl"
        assert active.status()["trace"] == str(trace)
        assert trace.exists()
        assert (trace.stat().st_mode & 0o777) == 0o600

    async def test_a_reused_call_id_is_refused(self, tmp_path: Path) -> None:
        call_id = uuid4()
        launcher = _launcher(tmp_path, connector=_Connector(), opener=_Opener())
        request: dict[str, object] = {
            "type": "call_start",
            "script": _script(tmp_path),
        }
        active = await launcher.launch(call_id, request)
        await active.cancel()
        with pytest.raises(ValueError, match="call_id has already been used"):
            await launcher.launch(call_id, request)

    async def test_a_missing_trace_dir_is_created_privately(
        self, tmp_path: Path
    ) -> None:
        trace_dir = tmp_path / "a" / "b"
        launcher = _launcher(trace_dir, connector=_Connector(), opener=_Opener())
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        await active.cancel()
        assert (trace_dir.stat().st_mode & 0o777) == 0o700

    async def test_the_lifecycle_is_logged_start_to_end(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=_LOG)
        launcher = _launcher(tmp_path, connector=_Connector(), opener=_Opener())
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        await active.wait_connected(5)
        while active.running:
            await asyncio.sleep(0.01)
        messages = [r.message for r in caplog.records if r.name == _LOG]
        assert messages[0] == "Call starting: input script, echo control none"
        assert messages[1] == "Call live: conversation conv_launch"
        assert messages[2].startswith("Call ended: ended, 0 tool calls, duration ")
        assert messages[2].endswith(f", trace {active.status()['trace']}")

    async def test_a_cancelled_call_still_logs_its_end(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=_LOG)
        connector = _Connector()
        launcher = _launcher(tmp_path, connector=connector, opener=_Opener())
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        await asyncio.sleep(0)  # the task enters the log context
        await active.cancel()
        (ended,) = [
            r.message for r in caplog.records if r.message.startswith("Call ended:")
        ]
        assert ended.startswith("Call ended: ended, 0 tool calls")

    async def test_the_departure_brief_is_saved_beside_the_trace(
        self, tmp_path: Path
    ) -> None:
        launcher = _launcher(tmp_path, connector=_Connector(), opener=_Opener())
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        await active.wait_connected(5)
        while active.running:
            await asyncio.sleep(0.01)
        assert json.loads((tmp_path / "departure.json").read_text()) == {}


class TestTaskLifecycle:
    async def test_hold_music_wraps_the_whole_call(self, tmp_path: Path) -> None:
        music = _HoldMusic()
        launcher = _launcher(
            tmp_path, connector=_Connector(), opener=_Opener(), hold_music=music
        )
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        await asyncio.sleep(0)  # the task enters the hold-music context
        assert music.entered == 1
        assert music.left == 0
        await active.cancel()
        assert music.left == 1

    async def test_the_dial_registers_the_boss_tools(self, tmp_path: Path) -> None:
        connector = _Connector()
        opener = _Opener()
        launcher = _launcher(tmp_path, connector=connector, opener=opener)
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        await active.wait_connected(5)
        await active.cancel()
        (tools,) = connector.connections
        assert any("start_worker" in tool for tool in tools)
        ((url, options),) = opener.dials
        assert url == "ws://peer"
        assert options.tool_ids == ("tool_a",)

    async def test_the_trace_stream_is_closed_when_the_task_ends(
        self, tmp_path: Path
    ) -> None:
        opener = _Opener()
        launcher = _launcher(tmp_path, connector=_Connector(), opener=opener)
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        await active.wait_connected(5)
        (trace,) = opener.traces
        trace.record("in", "ping", {"event_id": 1})
        while active.running:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0)  # the done callback closes the stream
        path = Path(str(active.status()["trace"]))
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row["type"] for row in rows] == ["ping"]
        with pytest.raises(ValueError, match="closed file"):
            trace.record("in", "ping", {"event_id": 2})

    async def test_an_unprovisioned_account_fails_the_launch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        launcher = _launcher(tmp_path, opener=_Opener())
        with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
            await launcher.launch(
                uuid4(), {"type": "call_start", "script": _script(tmp_path)}
            )
        assert list(tmp_path.iterdir()) == [tmp_path / "turn.pcm"]

    async def test_teardown_deletes_the_agent_registration(
        self, tmp_path: Path
    ) -> None:
        connector = _Connector()
        launcher = _launcher(tmp_path, connector=connector)
        await launcher.teardown()
        assert connector.teardowns == 1


class TestSetupFailure:
    """A failure between the starting line and the task leaves one end line."""

    @staticmethod
    def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
        return [r.message for r in caplog.records if r.name == _LOG]

    async def test_a_provisioning_failure_logs_not_started_once(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        caplog.set_level(logging.INFO, logger=_LOG)

        async def fail(_operations: AgentOperations) -> None:
            raise ConvAIError("agent provisioning failed")

        monkeypatch.setattr(AgentOperations, "prepare", fail)
        launcher = _launcher(tmp_path, connector=_Connector(), opener=_Opener())
        with pytest.raises(ConvAIError, match="agent provisioning failed"):
            await launcher.launch(
                uuid4(), {"type": "call_start", "script": _script(tmp_path)}
            )
        assert self._lines(caplog) == [
            "Call starting: input script, echo control none",
            "Call not started: ConvAIError",
        ]

    async def test_a_reused_call_id_logs_not_started_without_a_second_end(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=_LOG)
        call_id = uuid4()
        launcher = _launcher(tmp_path, connector=_Connector(), opener=_Opener())
        request: dict[str, object] = {
            "type": "call_start",
            "script": _script(tmp_path),
        }
        active = await launcher.launch(call_id, request)
        await asyncio.sleep(0)  # the task enters the log context
        await active.cancel()
        with pytest.raises(ValueError, match="call_id has already been used"):
            await launcher.launch(call_id, request)
        lines = self._lines(caplog)
        assert sum(line.startswith("Call ended:") for line in lines) == 1
        assert lines[-2:] == [
            "Call starting: input script, echo control none",
            "Call not started: ValueError",
        ]

    async def test_an_unusable_trace_dir_logs_the_error_type_without_its_path(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=_LOG)
        trace_dir = tmp_path / "traces"
        trace_dir.write_text("not a directory")
        launcher = _launcher(trace_dir, connector=_Connector(), opener=_Opener())
        with pytest.raises(OSError, match="traces"):
            await launcher.launch(
                uuid4(), {"type": "call_start", "script": _script(tmp_path)}
            )
        assert self._lines(caplog)[-1] == "Call not started: FileExistsError"
        assert not any(str(tmp_path) in line for line in self._lines(caplog))


class TestTaskFailure:
    """An exception in the task outside call.run still reaches the caller."""

    class _BrokenMusic:
        def __call__(self) -> AbstractContextManager[None]:
            return self

        def __enter__(self) -> None:
            raise RuntimeError("hold music player is not installed at /opt/x")

        def __exit__(self, *exc_info: object) -> None:
            pass

    async def test_hold_music_failing_on_entry_fails_the_call_promptly(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=_LOG)
        launcher = _launcher(
            tmp_path,
            connector=_Connector(),
            opener=_Opener(),
            hold_music=self._BrokenMusic(),
        )
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        async with asyncio.timeout(2):
            await active.wait_connected(30)
        status = active.status()
        assert status["state"] == "failed"
        assert status["error"] == (
            "RuntimeError: hold music player is not installed at <path>"
        )
        (ended,) = [
            r.message for r in caplog.records if r.message.startswith("Call ended:")
        ]
        assert "error RuntimeError: hold music player is not installed at <path>" in (
            ended
        )

    async def test_the_exception_the_task_ended_with_is_logged_once(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The done callback logs it; a later cancel does not log it again."""
        caplog.set_level(logging.WARNING, logger="punt_vox.voxd.voice_call")
        launcher = _launcher(
            tmp_path,
            connector=_Connector(),
            opener=_Opener(),
            hold_music=self._BrokenMusic(),
        )
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        await asyncio.sleep(0.05)
        await active.cancel()
        lines = [
            (r.name, r.message)
            for r in caplog.records
            if r.message.startswith("Call task ended with")
        ]
        ended = (
            "Call task ended with RuntimeError: hold music player is not installed "
            "at <path>"
        )
        assert lines == [("punt_vox.voxd.voice_call.launcher", ended)]

    async def test_a_failed_task_nobody_awaits_never_reports_an_unretrieved_exception(
        self, tmp_path: Path
    ) -> None:
        """wait_connected returns the failed status; the task is then dropped.

        asyncio reports "Task exception was never retrieved" through the
        loop's exception handler when such a task is collected, so the done
        callback must have read the exception already.
        """
        reported: list[dict[str, object]] = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: reported.append(context))
        launcher = _launcher(
            tmp_path,
            connector=_Connector(),
            opener=_Opener(),
            hold_music=self._BrokenMusic(),
        )
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        async with asyncio.timeout(2):
            await active.wait_connected(30)
        assert active.status()["state"] == "failed"
        assert not active.running
        del active
        gc.collect()
        await asyncio.sleep(0)
        assert reported == []


class TestMicrophoneBeforeDial:
    """The microphone is opened before the agent is dialled, never after."""

    async def test_a_microphone_that_cannot_open_refuses_the_call_without_a_dial(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger=_LOG)

        def refuse(**_kwargs: object) -> FakeInputStream:
            raise FakePortAudioError("Error opening RawInputStream: Device unavailable")

        fake_sounddevice(monkeypatch, FakeBackend(open_stream=refuse))
        connector = _Connector()
        launcher = _launcher(tmp_path, connector=connector, opener=_Opener())
        with pytest.raises(OSError, match="cannot open input device Fake Microphone"):
            await launcher.launch(
                uuid4(), {"type": "call_start", "echo_control": "headset"}
            )
        assert connector.connections == []
        assert not (tmp_path / "departure.json").exists()
        lines = [r.message for r in caplog.records if r.name == _LOG]
        assert lines[-1] == "Call not started: OSError"

    async def test_a_refused_microphone_gives_the_call_id_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The trace reserved for a call that never dialled is released.

        A retry with the same explicit call ID, once the microphone is free,
        must reach the connector instead of being refused as already used.
        """
        attempts: list[int] = []

        def refuse_once(**kwargs: object) -> FakeInputStream:
            attempts.append(1)
            if len(attempts) == 1:
                raise FakePortAudioError("Error opening RawInputStream: Device busy")
            return FakeInputStream(**kwargs)

        fake_sounddevice(monkeypatch, FakeBackend(open_stream=refuse_once))
        connector = _Connector()
        launcher = _launcher(tmp_path, connector=connector, opener=_Opener())
        call_id = uuid4()
        request: dict[str, object] = {"type": "call_start", "echo_control": "headset"}
        with pytest.raises(OSError, match="cannot open input device"):
            await launcher.launch(call_id, request)
        assert list(tmp_path.iterdir()) == []  # the reservation was undone
        active = await launcher.launch(call_id, request)
        await active.wait_connected(5)
        await active.cancel()
        assert len(connector.connections) == 1
        assert active.status()["trace"] == str(tmp_path / f"call-{call_id.hex}.jsonl")

    async def test_the_microphone_is_open_before_the_agent_answers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = fake_sounddevice(monkeypatch)
        opener = _Opener()
        launcher = _launcher(tmp_path, connector=_Connector(), opener=opener)
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "echo_control": "aec"}
        )
        (stream,) = backend.created  # opened by launch itself, before any dial
        assert not stream.closed
        await active.wait_connected(5)
        await active.cancel()
        assert stream.closed


class TestDepartureNotSaved:
    """A departure save failure is logged with its cause, never its path."""

    async def test_a_permission_error_logs_the_errno_and_no_path(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        caplog.set_level(logging.WARNING, logger="punt_vox.voxd.voice_call.launcher")

        def refuse(_brief: ReturnBrief, _reported: dict[str, int]) -> None:
            raise PermissionError(
                13, "Permission denied", "/Users/x/calls/departure.json"
            )

        monkeypatch.setattr(ReturnBrief, "depart", refuse)
        launcher = _launcher(tmp_path, connector=_Connector(), opener=_Opener())
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "script": _script(tmp_path)}
        )
        await active.wait_connected(5)
        while active.running:
            await asyncio.sleep(0.01)
        (line,) = [
            r.message
            for r in caplog.records
            if r.name == "punt_vox.voxd.voice_call.launcher"
        ]
        assert "/Users" not in line
        assert line == (
            "Call departure not saved (PermissionError: [Errno 13] Permission "
            "denied: '<path>', errno 13)"
        )


class TestCancelBeforeFirstStep:
    """A stop that lands before the task's first step still ends the call."""

    async def test_the_microphone_is_released_and_the_status_is_terminal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No await between launch and cancel: the task body never runs.

        run()'s cleanup never executes, so the launcher's completion path
        must close the input and end the call itself.
        """
        backend = fake_sounddevice(monkeypatch)
        launcher = _launcher(tmp_path, connector=_Connector(), opener=_Opener())
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "echo_control": "headset"}
        )
        await active.cancel()
        (stream,) = backend.created
        assert stream.closed
        assert active.status()["state"] == "ended"

    async def test_the_unstarted_call_still_logs_its_end(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger=_LOG)
        fake_sounddevice(monkeypatch)
        launcher = _launcher(tmp_path, connector=_Connector(), opener=_Opener())
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "echo_control": "headset"}
        )
        await active.cancel()
        (ended,) = [
            r.message for r in caplog.records if r.message.startswith("Call ended:")
        ]
        assert ended.startswith("Call ended: ended, 0 tool calls")

    async def test_a_cancel_after_the_body_ran_closes_the_input_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When run() ran, its own cleanup owns the close; the callback adds none."""
        exits: list[int] = []

        def count_exit(_stream: FakeInputStream, *_exc_info: object) -> None:
            exits.append(1)

        monkeypatch.setattr(FakeInputStream, "__exit__", count_exit)
        fake_sounddevice(monkeypatch)
        launcher = _launcher(tmp_path, connector=_Connector(), opener=_Opener())
        active = await launcher.launch(
            uuid4(), {"type": "call_start", "echo_control": "headset"}
        )
        await asyncio.sleep(0)  # the task takes its first step
        await active.cancel()
        assert exits == [1]
