"""Tests for :mod:`punt_vox.voxd.voice_call.launcher`.

The launcher assembles one call from a ``call_start`` request. These tests
drive :meth:`CallLauncher.launch` with a peer that hangs up at once, a fake
``sounddevice`` for the microphone path, and a connector double in place of
the ElevenLabs account, and read what the launcher built: the input it chose,
the trace it reserved, the log lines it emitted, and the call it returned.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
from conversation_mode._mic_fakes import FakeBackend

from punt_vox.voxd.voice_agent.events import Metadata
from punt_vox.voxd.voice_call.agent_operations import AgentOperations
from punt_vox.voxd.voice_call.launcher import CallLauncher
from punt_vox.voxd.voice_call.progress import ProgressEvents

from .fakes import FakePlayback, FakeWorkers

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from contextlib import AbstractContextManager

    from sounddevice import DeviceInfo

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
        opener=cast("Opener | None", opener),
    )


def _script(tmp_path: Path) -> list[dict[str, object]]:
    pcm = tmp_path / "turn.pcm"
    pcm.write_bytes(b"\x05\x00" * 320)
    return [{"pcm_path": str(pcm), "pause_s": 0.0}]


def _query_devices(kind: str) -> DeviceInfo:
    del kind
    return {"name": "Fake", "default_samplerate": 48000.0}


def _fake_microphone(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    backend = FakeBackend()
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(
            RawInputStream=backend.open,
            PortAudioError=OSError,
            query_devices=_query_devices,
        ),
    )
    return backend


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
        backend = _fake_microphone(monkeypatch)
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
