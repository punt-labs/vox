"""Tests for :class:`CallRunner`: spawn the agent, drive the call, end the agent.

The call agent is a :class:`FakePiProcess` at the subprocess boundary and the
call is the scripted (``--script``) drive loop, so everything between them --
host, agent, session -- runs for real.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import typer
from conversation_mode._pi_rpc_fakes import FakePiProcess, PiReply, SpawnRecorder

from punt_vox.commands.call_runner import CallRunner
from punt_vox.voxd.conversation_mode.call_agent_host import CallAgentHost
from punt_vox.voxd.conversation_mode.call_control import CallControl
from punt_vox.voxd.conversation_mode.call_session import CallSession
from punt_vox.voxd.conversation_mode.call_summary import CallSummaryStore

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.speak_fn import SpeakFn

_EXEC = "punt_vox.voxd.conversation_mode.pi_rpc_channel.asyncio.create_subprocess_exec"
_DRIVE = "punt_vox.commands.call_scripted.ScriptedCallDriver.run"


class _Speaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def __call__(self, text: str) -> None:
        self.spoken.append(text)


async def _chime() -> None:
    return None


async def _never_stop(
    control: CallControl, session: CallSession, speak: SpeakFn
) -> bool:
    del control, session, speak
    return False


def _runner(tmp_path: Path) -> CallRunner:
    return CallRunner(
        speak=_Speaker(),
        chime=_chime,
        control=CallControl(tmp_path / "call.control"),
        apply_control=_never_stop,
    )


@contextmanager
def _agent(tmp_path: Path, process: FakePiProcess) -> Generator[None]:
    store = CallSummaryStore(tmp_path / "calls")
    with (
        patch(_EXEC, SpawnRecorder(process)),
        patch.object(CallSummaryStore, "for_user", return_value=store),
    ):
        yield


def _files(tmp_path: Path, *turns: str) -> tuple[Path, Path]:
    context = tmp_path / "context.md"
    context.write_text("Topic: the music loop")
    script = tmp_path / "script.jsonl"
    script.write_text(
        "\n".join(json.dumps({"text": t, "confidence": 0.95}) for t in turns)
    )
    return context, script


async def test_a_clean_call_returns_where_its_summary_landed(tmp_path: Path) -> None:
    context, script = _files(tmp_path, "what does this do")
    process = FakePiProcess.answering("Ready.", "It sums.", "## Decisions Made")
    with _agent(tmp_path, process):
        outcome = await _runner(tmp_path).run(context, script, repo_root=tmp_path)
    assert outcome.summary_path is not None
    assert "## Decisions Made" in outcome.summary_path.read_text()


async def test_an_agent_that_will_not_start_is_a_usage_error(tmp_path: Path) -> None:
    context, script = _files(tmp_path, "hi")
    process = FakePiProcess([PiReply(error="401 invalid api key")])
    with (
        _agent(tmp_path, process),
        pytest.raises(typer.BadParameter, match="invalid api key"),
    ):
        await _runner(tmp_path).run(context, script, repo_root=tmp_path)


async def test_a_crashed_drive_loop_ends_the_agent_and_reraises(
    tmp_path: Path,
) -> None:
    context, script = _files(tmp_path, "hi")
    process = FakePiProcess.answering("Ready.")
    with (
        _agent(tmp_path, process),
        patch(_DRIVE, side_effect=RuntimeError("STT provider crashed")),
        pytest.raises(RuntimeError, match="STT provider crashed"),
    ):
        await _runner(tmp_path).run(context, script, repo_root=tmp_path)
    assert process.returncode is not None


async def test_a_failing_abandon_is_logged_and_never_replaces_the_original(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    context, script = _files(tmp_path, "hi")
    with (
        _agent(tmp_path, FakePiProcess.answering("Ready.")),
        patch(_DRIVE, side_effect=RuntimeError("STT provider crashed")),
        patch.object(CallAgentHost, "abandon", side_effect=OSError("kill failed")),
        caplog.at_level(logging.ERROR, logger="punt_vox.commands.call_runner"),
        pytest.raises(RuntimeError, match="STT provider crashed"),
    ):
        await _runner(tmp_path).run(context, script, repo_root=tmp_path)
    assert "kill failed" in caplog.text
