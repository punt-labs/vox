"""Tests for the ``vox call`` CLI: the start and stop verbs, per ADR naming.

``ScriptedTurn``/``ScriptedSTTProvider`` unit tests live in
``tests/test_call_scripted.py``, mirroring their module
(``punt_vox.commands.call_scripted``). This file covers the CLI verbs and
the two ``start`` paths: scripted (dev/test) and live (default), the latter
driven by mocked ``MicAudioSource``/``ElevenLabsSTTProvider`` so it never
touches a real microphone or the ElevenLabs API. The call agent is a
:class:`FakePiProcess` at the subprocess boundary -- everything above the
pipe runs for real.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Coroutine, Generator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
import typer
import typer.core
from conversation_mode._pi_rpc_fakes import FakePiProcess, PiReply, SpawnRecorder
from typer.testing import CliRunner

from punt_vox.commands.call import CallCli
from punt_vox.commands.call_scripted import ScriptedTurn
from punt_vox.session_spec import SessionSpec
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
from punt_vox.voxd.conversation_mode.call_control import CallControl
from punt_vox.voxd.conversation_mode.call_lock import CallLock
from punt_vox.voxd.conversation_mode.call_summary import CallSummaryStore

# Force a wide terminal so typer's Rich-based help renderer never wraps flag
# names across lines -- CI's headless runner reports a narrow or undefined
# width, and a plain substring assertion against wrapped/truncated output is
# not what these tests mean to check.
runner = CliRunner(env={"COLUMNS": "200"})

_EXEC = "punt_vox.voxd.conversation_mode.pi_rpc_channel.asyncio.create_subprocess_exec"
# A fault here strikes inside the scripted drive loop, after the agent spawned.
_PROCESS_CHUNK = "punt_vox.commands.call_scripted.CallSession.process_chunk"


def _resolved_session_spec(
    provider: str = "elevenlabs",
) -> AbstractContextManager[MagicMock]:
    """Patch ``SessionSpec.for_repo`` so tests never touch the real ``vox.md``.

    ``call.py``'s ``_run`` resolves a :class:`SynthesisSpec` through
    ``SessionSpec.for_repo().fill()`` before the call state machine starts
    (the same fill-from-``vox.md`` path ``vox say``/``vox rec new`` use).
    Faking it here keeps these tests hermetic -- they must pass on a CI
    machine with no configured provider, not only on a workstation that
    happens to have this repo's own vox enabled.
    """
    fake_spec = MagicMock()
    fake_spec.fill.return_value = SynthesisSpec(provider=provider)
    return patch.object(SessionSpec, "for_repo", return_value=fake_spec)


def _call_lock_at(tmp_path: Path) -> AbstractContextManager[object]:
    """Patch ``CallLock.for_repo`` to a lock rooted at *tmp_path*."""
    return patch.object(
        CallLock, "for_repo", return_value=CallLock(tmp_path / "call.lock")
    )


def _call_control_at(tmp_path: Path) -> AbstractContextManager[object]:
    """Patch ``CallControl.for_repo`` to a mailbox rooted at *tmp_path*."""
    return patch.object(
        CallControl, "for_repo", return_value=CallControl(tmp_path / "call.control")
    )


def _summaries_at(tmp_path: Path) -> AbstractContextManager[object]:
    """Patch the summary store to *tmp_path*/calls, never the real home dir."""
    return patch.object(
        CallSummaryStore, "for_user", return_value=CallSummaryStore(tmp_path / "calls")
    )


@contextmanager
def _call_env(tmp_path: Path, process: FakePiProcess) -> Generator[SpawnRecorder]:
    """Everything a ``_run`` needs to be hermetic, with *process* as the agent."""
    recorder = SpawnRecorder(process)
    with (
        _resolved_session_spec(),
        _call_lock_at(tmp_path),
        _call_control_at(tmp_path),
        _summaries_at(tmp_path),
        patch(_EXEC, recorder),
    ):
        yield recorder


def _script_file(tmp_path: Path, lines: list[dict[str, object]]) -> Path:
    path = tmp_path / "script.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines))
    return path


def _context_file(tmp_path: Path, text: str = "Topic: the music loop") -> Path:
    path = tmp_path / "context.md"
    path.write_text(text)
    return path


class _RecordingClient:
    """Stands in for ``VoxClientSync``: records every phrase and spec."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.specs: list[SynthesisSpec | None] = []

    def synthesize(
        self,
        text: str,
        spec: SynthesisSpec | None = None,
        *,
        timeout: float | None = None,
    ) -> None:
        del timeout
        self.spoken.append(text)
        self.specs.append(spec)


def _client(client: object) -> AbstractContextManager[object]:
    from punt_vox.commands import call as call_module

    return patch.object(call_module, "VoxClientSync", return_value=client)


def test_stop_writes_a_stop_request(tmp_path: Path) -> None:
    CallLock(tmp_path / "call.lock").acquire("conversation mode call active")
    with _call_lock_at(tmp_path), _call_control_at(tmp_path):
        result = runner.invoke(CallCli.build_app(), ["stop"])
    assert result.exit_code == 0
    control = CallControl(tmp_path / "call.control")
    request = control.consume()
    assert request is not None
    assert request.kind == "stop"


def test_stop_refuses_when_no_call_is_active(tmp_path: Path) -> None:
    with _call_lock_at(tmp_path), _call_control_at(tmp_path):
        result = runner.invoke(CallCli.build_app(), ["stop"])
    assert result.exit_code != 0
    assert CallControl(tmp_path / "call.control").consume() is None


def test_stop_refuses_against_a_stale_lock_with_a_dead_pid(tmp_path: Path) -> None:
    """Regression: a killed `vox call start` leaves a lock file behind with a
    now-dead pid. `vox call stop` against that stale lock must refuse the
    same as "no call is active" -- not succeed and write a stop request
    into a mailbox nobody will ever read, since the process it thinks it is
    stopping is long gone.
    """
    CallLock(tmp_path / "call.lock").acquire("stale call")
    with (
        patch("os.kill", side_effect=ProcessLookupError),
        _call_lock_at(tmp_path),
        _call_control_at(tmp_path),
    ):
        result = runner.invoke(CallCli.build_app(), ["stop"])
    assert result.exit_code != 0
    assert "no call is active" in result.output
    assert CallControl(tmp_path / "call.control").consume() is None


def test_the_verbs_are_start_and_stop() -> None:
    """A call agent is attached to no session, so there is nothing to transfer."""
    group = cast("typer.core.TyperGroup", typer.main.get_command(CallCli.build_app()))
    assert set(group.commands) == {"start", "stop"}


async def test_apply_control_stops_on_a_stop_request(tmp_path: Path) -> None:
    from punt_vox.commands import call as call_module

    control = CallControl(tmp_path / "call.control")
    control.request_stop()

    async def speak(text: str) -> None:
        del text

    assert await call_module.CallCli._apply_control(control, MagicMock(), speak)


async def test_apply_control_continues_with_no_request(tmp_path: Path) -> None:
    from punt_vox.commands import call as call_module

    async def speak(text: str) -> None:
        del text

    control = CallControl(tmp_path / "call.control")
    assert not await call_module.CallCli._apply_control(control, MagicMock(), speak)


def _start_command() -> typer.core.TyperCommand:
    """Return the ``start`` subcommand's underlying Typer/click command.

    Introspecting the registered parameters, rather than parsing rendered
    ``--help`` text, is immune to Typer's Rich-based help renderer wrapping
    or truncating output based on the runner's detected terminal width.
    """
    app_command = typer.main.get_command(CallCli.build_app())
    group = cast("typer.core.TyperGroup", app_command)
    return cast("typer.core.TyperCommand", group.commands["start"])


def test_start_no_longer_requires_the_script_option() -> None:
    """``--script`` is the dev/test opt-in now, not a required flag."""
    script_param = next(p for p in _start_command().params if p.name == "script")
    assert script_param.required is False


def test_start_requires_a_context_snapshot() -> None:
    """The call agent sees only the snapshot, so a call cannot start without one."""
    context_param = next(p for p in _start_command().params if p.name == "context")
    assert context_param.required is True


def test_start_without_context_is_a_usage_error() -> None:
    result = runner.invoke(CallCli.build_app(), ["start"])
    assert result.exit_code == 2


def _close_coro(coro: Coroutine[object, object, object]) -> None:
    """A no-op ``asyncio.run`` stand-in: closes *coro* without running it."""
    coro.close()


def test_start_help_shows_the_trace_turns_flag() -> None:
    param_names = {p.name for p in _start_command().params}
    assert "trace_turns" in param_names


def test_start_help_does_not_collide_with_the_global_verbose_flag() -> None:
    """Regression: a second, same-named --verbose scoped to `call start`
    silently did something completely different from the pre-existing
    global --verbose (raise the client log level) depending on flag
    position -- vox's own OutputFlags convention says position must never
    change meaning. `call start --help` must not offer a bare `-v`/
    `--verbose` at all; only the distinctly-named --trace-turns.
    """
    result = runner.invoke(CallCli.build_app(), ["start", "--help"])
    assert result.exit_code == 0
    assert "--verbose" not in result.stdout
    assert "-v," not in result.stdout


def test_start_with_trace_turns_echoes_the_turn_timer_to_console(
    tmp_path: Path,
) -> None:
    """Regression: --trace-turns must reach configure_turn_timer_logging as
    echo_to_console=True -- the flag that decides whether the turn-latency
    trace also prints live, never whether it reaches vox.log (that's
    unconditional).
    """
    from punt_vox.commands import call as call_module

    with (
        patch.object(call_module, "configure_turn_timer_logging") as mock_configure,
        patch("asyncio.run", side_effect=_close_coro),
    ):
        call_module.CallCli().start(_context_file(tmp_path), trace_turns=True)
    mock_configure.assert_called_once_with(echo_to_console=True)


def test_start_without_trace_turns_does_not_echo_the_turn_timer_to_console(
    tmp_path: Path,
) -> None:
    from punt_vox.commands import call as call_module

    with (
        patch.object(call_module, "configure_turn_timer_logging") as mock_configure,
        patch("asyncio.run", side_effect=_close_coro),
    ):
        call_module.CallCli().start(_context_file(tmp_path))
    mock_configure.assert_called_once_with(echo_to_console=False)


async def test_run_call_passes_a_resolved_spec_with_a_provider_to_synthesize(
    tmp_path: Path,
) -> None:
    """Regression: a bare ``client.synthesize(text)`` sends no provider on the
    wire, and voxd's ``speech_handlers.py`` rejects with ``Unknown provider
    ''`` -- ``parse_required_str`` requires one and does not guess. Every
    ``speak()`` call inside ``_run`` must pass a :class:`SynthesisSpec` whose
    ``provider`` survived ``SessionSpec.for_repo().fill()`` resolution, not
    just call ``speak()`` at all.
    """
    from punt_vox.commands import call as call_module

    client = _RecordingClient()
    with _client(client), _call_env(tmp_path, FakePiProcess.answering("Ready.")):
        await call_module.CallCli()._run(
            _context_file(tmp_path), _script_file(tmp_path, [])
        )

    assert client.specs  # at least the "Listening." cue was spoken
    assert all(
        spec is not None and spec.provider == "elevenlabs" for spec in client.specs
    )


async def test_run_call_speaks_the_reply_and_holds_the_lock_only_while_active(
    tmp_path: Path,
) -> None:
    """End-to-end through the CLI orchestration: real detector, fake speech I/O."""
    from punt_vox.commands import call as call_module

    script = _script_file(tmp_path, [{"text": "what does this do", "confidence": 0.95}])
    client = _RecordingClient()
    process = FakePiProcess.answering("Ready.", "It returns the sum.", "## Decisions")
    with _client(client), _call_env(tmp_path, process):
        await call_module.CallCli()._run(_context_file(tmp_path), script)

    assert "It returns the sum." in client.spoken
    assert CallLock(tmp_path / "call.lock").read() is None  # released after hangup


async def test_run_call_opens_the_agent_with_the_context_snapshot(
    tmp_path: Path,
) -> None:
    from punt_vox.commands import call as call_module

    process = FakePiProcess.answering("Ready.")
    with _client(_RecordingClient()), _call_env(tmp_path, process):
        await call_module.CallCli()._run(
            _context_file(tmp_path, "Topic: retention of call summaries"),
            _script_file(tmp_path, []),
        )

    assert "Topic: retention of call summaries" in process.prompts[0]


async def test_a_clean_call_ends_by_printing_where_its_summary_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from punt_vox.commands import call as call_module

    script = _script_file(tmp_path, [{"text": "what does this do", "confidence": 0.95}])
    process = FakePiProcess.answering("Ready.", "It sums.", "## Decisions Made\n- x")
    with _client(_RecordingClient()), _call_env(tmp_path, process):
        await call_module.CallCli()._run(_context_file(tmp_path), script)

    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert line.startswith("call summary: ")
    summary = Path(line.removeprefix("call summary: "))
    assert "## Decisions Made" in summary.read_text()
    assert process.returncode is not None  # the agent never outlives the call


class _FakeLiveMicSource:
    """Stands in for :class:`MicAudioSource`: no hardware, real turn shape."""

    def __init__(self, calibration: list[AudioChunk], live: list[AudioChunk]) -> None:
        self._calibration = calibration
        self._live = live
        self.drain_calls = 0
        self.listening_calls: list[bool] = []

    async def capture_seconds(self, _duration_s: float) -> list[AudioChunk]:
        return self._calibration

    async def chunks(self) -> AsyncIterator[AudioChunk]:
        for chunk in self._live:
            yield chunk

    def drain_pending(self) -> int:
        self.drain_calls += 1
        return 0

    def set_listening(self, *, listening: bool) -> None:
        self.listening_calls.append(listening)


class _FakeLiveSTT:
    """Stands in for :class:`ElevenLabsSTTProvider`: canned transcript, no SDK."""

    def __init__(self, text: str, confidence: float) -> None:
        self._text = text
        self._confidence = confidence

    @property
    def name(self) -> str:
        return "fake-elevenlabs"

    async def transcribe(
        self, chunks: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[object]:
        from punt_vox.voxd.conversation_mode.stt_provider import TranscriptEvent

        async for _ in chunks:
            pass
        yield TranscriptEvent(
            text=self._text, confidence=self._confidence, is_final=True
        )

    def check_health(self) -> list[object]:
        return []


@contextmanager
def _live_audio(mic_source: _FakeLiveMicSource, stt: _FakeLiveSTT) -> Generator[None]:
    with (
        patch(
            "punt_vox.commands.call_live_driver.MicAudioSource", return_value=mic_source
        ),
        patch(
            "punt_vox.commands.call_live_driver.ElevenLabsSTTProvider", return_value=stt
        ),
    ):
        yield


async def test_run_call_live_path_captures_from_mic_and_transcribes(
    tmp_path: Path,
) -> None:
    """Default (no ``--script``) drives mic capture + ElevenLabs STT, both faked."""
    from punt_vox.commands import call as call_module

    calibration = ScriptedTurn.silence_chunks(10)
    live_chunks = ScriptedTurn(text="ignored", confidence=1.0).synthetic_chunks()
    mic_source = _FakeLiveMicSource(calibration, live_chunks)
    stt = _FakeLiveSTT("what does this do", confidence=0.95)
    client = _RecordingClient()
    process = FakePiProcess.answering("Ready.", "It returns the sum.", "## Decisions")

    with _client(client), _call_env(tmp_path, process), _live_audio(mic_source, stt):
        await call_module.CallCli()._run(_context_file(tmp_path), None)

    assert "It returns the sum." in client.spoken
    assert CallLock(tmp_path / "call.lock").read() is None  # released after hangup

    # Regression: draining must happen after every utterance, not only on
    # the speaking -> listening mode transition -- "Listening.", the
    # instant ack quip, the reply itself, and "Ready." (spoken *after* that
    # transition already fired) are four separate speak() calls, and each
    # must drain the mic's self-captured backlog on its own.
    assert mic_source.drain_calls == len(client.spoken) == 4

    # The mic-echo mitigation (mic_audio_source.py's set_listening, gated at
    # the PortAudio callback itself, not just drained after the fact): the
    # gate closes before every speak() call and reopens after, one
    # False/True pair per utterance.
    assert mic_source.listening_calls == [False, True] * 4


async def test_run_call_live_path_times_out_after_inactivity(
    tmp_path: Path,
) -> None:
    """FR-2's bounded-inactivity end must actually be wired into the live loop."""
    from punt_vox.commands import call as call_module

    calibration = ScriptedTurn.silence_chunks(10)
    # Plain silence, never enough to close a turn -- nothing here should
    # ever speak a reply or reset the inactivity clock via a mode change
    # other than the call's own start.
    live_chunks = [AudioChunk(pcm=b"\x00\x00", duration_s=0.02) for _ in range(50)]
    mic_source = _FakeLiveMicSource(calibration, live_chunks)
    stt = _FakeLiveSTT("unused", confidence=0.95)
    client = _RecordingClient()

    with (
        _client(client),
        _call_env(tmp_path, FakePiProcess.answering("Ready.")),
        _live_audio(mic_source, stt),
        patch("punt_vox.commands.call_live_driver._INACTIVITY_TIMEOUT_S", 0.0),
    ):
        await call_module.CallCli()._run(_context_file(tmp_path), None)

    # The loop ended itself via the FR-2 timeout, not by exhausting the
    # chunk generator with a turn still pending -- "Listening." is the only
    # cue spoken; there is no reply and no "Ready.".
    assert client.spoken == ["Listening."]
    assert CallLock(tmp_path / "call.lock").read() is None  # released after timeout


class _ClientFailingOnErrorSummary(_RecordingClient):
    """Speaks normally except when asked to speak the outer boundary's
    "call ended unexpectedly" summary -- lets a test drive the outer
    ``except`` handler's own ``speak()`` call into failure without
    disturbing every earlier cue in the call.
    """

    def synthesize(
        self,
        text: str,
        spec: SynthesisSpec | None = None,
        *,
        timeout: float | None = None,
    ) -> None:
        super().synthesize(text, spec, timeout=timeout)
        if text.startswith("The call ended unexpectedly"):
            raise RuntimeError("daemon also unreachable")


async def test_outer_boundary_speaks_and_reraises_on_a_mid_call_crash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The one mechanism that turns a mid-call crash into audible feedback:
    ``commands/call.py``'s outer ``except Exception`` handler must speak a
    human-facing summary, log via ``logger.exception``, release the lock in
    ``finally``, and still re-raise so the process exits non-zero.
    """
    from punt_vox.commands import call as call_module

    script = _script_file(tmp_path, [{"text": "what does this do", "confidence": 0.95}])
    client = _ClientFailingOnErrorSummary()
    process = FakePiProcess.answering("Ready.")
    with (
        _client(client),
        _call_env(tmp_path, process),
        patch(_PROCESS_CHUNK, side_effect=OSError("mic device busy")),
        caplog.at_level("ERROR", logger="punt_vox.commands.call"),
        pytest.raises(OSError, match="mic device busy"),
    ):
        await call_module.CallCli()._run(_context_file(tmp_path), script)

    # The fault struck inside the drive loop, after the agent was spawned;
    # the agent was abandoned with the call, never left running.
    assert len(process.prompts) == 1
    assert process.returncode is not None

    assert any("call ended unexpectedly" in record.message for record in caplog.records)
    # The spoken summary is a fixed sentence -- never the raw exception text,
    # which for an agent failure can embed its stderr verbatim (a
    # voice-disclosure hazard). The detail lives only in the log line above.
    assert (
        "The call ended unexpectedly. Check the terminal for details." in client.spoken
    )
    assert not any("mic device busy" in phrase for phrase in client.spoken)
    assert CallLock(tmp_path / "call.lock").read() is None  # released in finally


async def test_outer_boundary_survives_a_failing_speak_and_reraises_the_original(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A ``speak()`` failure inside the outer boundary's own fallback call
    must not replace the original exception -- the root cause, not a
    secondary daemon-RPC failure, is what the human's terminal and the
    process's exit code must show.
    """
    from punt_vox.commands import call as call_module

    script = _script_file(tmp_path, [{"text": "what does this do", "confidence": 0.95}])
    process = FakePiProcess.answering("Ready.")
    with (
        _client(_ClientFailingOnErrorSummary()),
        _call_env(tmp_path, process),
        patch(_PROCESS_CHUNK, side_effect=OSError("mic device busy")),
        caplog.at_level("ERROR", logger="punt_vox.commands.call"),
        pytest.raises(OSError, match="mic device busy"),
    ):
        await call_module.CallCli()._run(_context_file(tmp_path), script)

    assert process.returncode is not None  # spawned, then abandoned by the crash

    assert any(
        "also failed to speak the call-ended summary" in record.message
        for record in caplog.records
    )
    assert CallLock(tmp_path / "call.lock").read() is None  # still released


async def test_a_crash_mid_call_kills_the_agent_and_writes_no_summary(
    tmp_path: Path,
) -> None:
    """An abnormal end abandons the agent: no summary turn, no orphan process."""
    from punt_vox.commands import call as call_module

    script = _script_file(tmp_path, [{"text": "hi", "confidence": 0.95}])
    process = FakePiProcess.answering("Ready.", "reply")
    with (
        _client(_ClientFailingOnErrorSummary()),
        _call_env(tmp_path, process),
        patch(
            "punt_vox.commands.call_scripted.ScriptedCallDriver.run",
            side_effect=RuntimeError("STT provider crashed"),
        ),
        pytest.raises(RuntimeError, match="STT provider crashed"),
    ):
        await call_module.CallCli()._run(_context_file(tmp_path), script)

    assert process.returncode is not None
    assert not (tmp_path / "calls").exists()


@pytest.mark.parametrize("context_text", ["", "   \n"])
async def test_an_empty_snapshot_is_a_clean_usage_error_not_a_crash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, context_text: str
) -> None:
    """A bad snapshot is an expected, actionable usage error -- it must reach
    typer's clean CLI handling, not be logged as "call ended unexpectedly"."""
    from punt_vox.commands import call as call_module

    client = _RecordingClient()
    recorder: SpawnRecorder
    with (
        _client(client),
        _call_env(tmp_path, FakePiProcess([])) as recorder,
        caplog.at_level("ERROR", logger="punt_vox.commands.call"),
        pytest.raises(typer.BadParameter, match="empty"),
    ):
        await call_module.CallCli()._run(
            _context_file(tmp_path, context_text), _script_file(tmp_path, [])
        )

    assert recorder.argv == ()  # no agent was ever started
    assert not any("unexpectedly" in record.message for record in caplog.records)
    assert client.spoken == []
    assert CallLock(tmp_path / "call.lock").read() is None


async def test_an_agent_that_will_not_start_is_a_clean_usage_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from punt_vox.commands import call as call_module

    client = _RecordingClient()
    with (
        _client(client),
        _call_env(tmp_path, FakePiProcess([PiReply(error="401 invalid api key")])),
        caplog.at_level("ERROR", logger="punt_vox.commands.call"),
        pytest.raises(typer.BadParameter, match="invalid api key"),
    ):
        await call_module.CallCli()._run(
            _context_file(tmp_path), _script_file(tmp_path, [])
        )

    assert not any("unexpectedly" in record.message for record in caplog.records)
    assert client.spoken == []


if __name__ == "__main__":
    pytest.main([__file__])
