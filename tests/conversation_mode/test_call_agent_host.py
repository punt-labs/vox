"""Tests for :class:`CallAgentHost`: one call's agent from spawn to summary."""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from conversation_mode._pi_rpc_fakes import (
    FakePiProcess,
    PiReply,
    SpawnRecorder,
    ToolUse,
)
from punt_vox.voxd.conversation_mode import call_agent_host as host_module
from punt_vox.voxd.conversation_mode.call_agent_errors import (
    CallAgentEndedError,
    CallAgentSpawnError,
)
from punt_vox.voxd.conversation_mode.call_agent_host import CallAgentHost, CallOutcome
from punt_vox.voxd.conversation_mode.call_agent_launch import CallAgentLaunch
from punt_vox.voxd.conversation_mode.call_summary import CallSummaryStore
from punt_vox.voxd.conversation_mode.pi_call_agent import PiCallAgent
from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy
from punt_vox.voxd.conversation_mode.turn import TranscribedTurn
from punt_vox.voxd.conversation_mode.turn_timer import LoggingTurnTimer

_EXEC = "punt_vox.voxd.conversation_mode.pi_rpc_channel.asyncio.create_subprocess_exec"

_SUMMARY = "## Decisions Made\n- Keep the pool at 12.\n## Action Items\n- None."


async def _host(
    tmp_path: Path, process: FakePiProcess, store_root: Path | None = None
) -> CallAgentHost:
    timer = LoggingTurnTimer()
    with patch(_EXEC, SpawnRecorder(process)):
        agent = await PiCallAgent.spawn(
            CallAgentLaunch(SecretPathPolicy(tmp_path)),
            cwd=tmp_path,
            opening_message="snapshot",
            turn_timer=timer,
        )
    store = CallSummaryStore(store_root or tmp_path / "calls")
    return CallAgentHost(
        agent, store, call_id="20261105T140302Z-abc123", turn_timer=timer
    )


async def _one_turn(host: CallAgentHost) -> None:
    async for _chunk in host.agent.send_turn(TranscribedTurn(text="q")):
        pass


class TestFinish:
    async def test_a_clean_call_writes_the_agent_s_summary(
        self, tmp_path: Path
    ) -> None:
        host = await _host(tmp_path, FakePiProcess.answering("Ready.", "a", _SUMMARY))
        await _one_turn(host)
        outcome = await host.finish()
        assert outcome.summary_path is not None
        assert _SUMMARY in outcome.summary_path.read_text()

    async def test_the_summary_is_the_agent_s_final_turn_then_teardown(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess.answering("Ready.", "a", _SUMMARY)
        host = await _host(tmp_path, process)
        await _one_turn(host)
        await host.finish()
        assert "## Action Items" in process.prompts[-1]
        assert (process.stdin.is_closing(), process.killed) == (True, False)

    async def test_a_call_with_no_turns_has_nothing_to_summarize(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess.answering("Ready.")
        outcome = await (await _host(tmp_path, process)).finish()
        assert (outcome.summary_path, process.stdin.is_closing()) == (None, True)

    async def test_a_failed_summary_turn_still_tears_the_agent_down(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess([PiReply("Ready."), PiReply("a"), PiReply(error="5xx")])
        host = await _host(tmp_path, process)
        await _one_turn(host)
        outcome = await host.finish()
        assert outcome.summary_path is None
        assert process.returncode is not None

    async def test_a_summary_turn_the_guard_refused_writes_no_summary(
        self, tmp_path: Path
    ) -> None:
        refused = PiReply(
            text="## Decisions Made",
            tools=(ToolUse("read", {"path": ".env"}, blocked=True),),
        )
        process = FakePiProcess([PiReply("Ready."), PiReply("a"), refused])
        host = await _host(tmp_path, process)
        await _one_turn(host)
        outcome = await host.finish()
        assert outcome.line() == (
            "no call summary: the secret guard blocked a read this turn needed"
        )
        assert not (tmp_path / "calls").exists()

    async def test_a_summary_that_cannot_be_saved_is_an_outcome_not_a_crash(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("")
        process = FakePiProcess.answering("Ready.", "a", _SUMMARY)
        host = await _host(tmp_path, process, store_root=blocker / "calls")
        await _one_turn(host)
        with caplog.at_level(logging.ERROR, logger=host_module.__name__):
            outcome = await host.finish()
        assert outcome.line().startswith("no call summary: it could not be saved")
        assert f"{len(_SUMMARY)} characters" in caplog.text
        assert process.returncode is not None

    async def test_an_empty_summary_is_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        process = FakePiProcess.answering("Ready.", "a", "   ")
        host = await _host(tmp_path, process)
        await _one_turn(host)
        with caplog.at_level(logging.WARNING, logger=host_module.__name__):
            outcome = await host.finish()
        assert outcome.summary_path is None
        assert "empty summary" in caplog.text

    async def test_an_ended_agent_reports_why_there_is_no_summary(
        self, tmp_path: Path
    ) -> None:
        host = await _host(
            tmp_path, FakePiProcess([PiReply("Ready."), PiReply(die=True)])
        )
        with pytest.raises(CallAgentEndedError, match="exited"):
            await _one_turn(host)
        outcome = await host.finish()
        assert outcome.line() == "no call summary: the call agent had already ended"


class TestAbandon:
    async def test_abandon_while_busy_kills_the_agent_via_abort_turn(
        self, tmp_path: Path
    ) -> None:
        """The reply stream is suspended mid-reply -- the agent is genuinely
        busy, nothing cancelled it -- so only close()'s busy branch
        (``AbortTurn``) can end the process."""
        process = FakePiProcess.answering("Ready.", "a long reply here")
        host = await _host(tmp_path, process)
        stream = host.agent.send_turn(TranscribedTurn(text="q"))
        await anext(stream)
        assert (host.agent.can_accept_turn, process.killed) == (False, False)
        await host.abandon()
        assert process.killed
        await stream.aclose()

    async def test_abandon_after_a_cancelled_read_kills_the_agent(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess([PiReply("Ready."), PiReply(hang=True)])
        host = await _host(tmp_path, process)
        stream = host.agent.send_turn(TranscribedTurn(text="q"))
        pending = stream.__anext__()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await pending
        await host.abandon()
        assert process.killed

    async def test_abandon_when_ready_writes_no_summary(self, tmp_path: Path) -> None:
        process = FakePiProcess.answering("Ready.", "a")
        host = await _host(tmp_path, process)
        await _one_turn(host)
        await host.abandon()
        assert not (tmp_path / "calls").exists()


class TestOpen:
    @pytest.fixture(autouse=True)
    def ripgrep(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """pi's own rg, as the guard will find it: open checks for it first."""
        rg = tmp_path / "agent" / "bin" / "rg"
        rg.parent.mkdir(parents=True)
        rg.write_text("#!/bin/sh\n")
        rg.chmod(0o755)
        monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agent"))
        return rg

    async def test_open_spawns_with_the_snapshot_from_stdin(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess.answering("Ready.")
        with patch(_EXEC, SpawnRecorder(process)):
            host = await CallAgentHost.open(
                Path("-"),
                cwd=tmp_path,
                repo_root=tmp_path,
                stdin=io.StringIO("Topic: the vibe nudge"),
            )
        assert "Topic: the vibe nudge" in process.prompts[0]
        await host.abandon()

    async def test_a_bad_snapshot_fails_before_any_process_starts(
        self, tmp_path: Path
    ) -> None:
        recorder = SpawnRecorder(FakePiProcess([]))
        with patch(_EXEC, recorder), pytest.raises(ValueError, match="empty"):
            await CallAgentHost.open(
                Path("-"), cwd=tmp_path, repo_root=tmp_path, stdin=io.StringIO("")
            )
        assert recorder.argv == ()

    async def test_no_ripgrep_fails_the_call_before_any_process_starts(
        self, tmp_path: Path, ripgrep: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard refuses every directory grep without rg; say so up front."""
        ripgrep.unlink()
        git_only = tmp_path / "git-only"
        git_only.mkdir()
        git = shutil.which("git")
        assert git is not None
        (git_only / "git").symlink_to(git)
        monkeypatch.setenv("PATH", str(git_only))
        recorder = SpawnRecorder(FakePiProcess([]))
        with (
            patch(_EXEC, recorder),
            pytest.raises(CallAgentSpawnError, match="ripgrep"),
        ):
            await CallAgentHost.open(
                Path("-"),
                cwd=tmp_path,
                repo_root=tmp_path,
                stdin=io.StringIO("Topic: x"),
            )
        assert recorder.argv == ()


def test_the_outcome_line_names_the_summary_file(tmp_path: Path) -> None:
    path = tmp_path / "x-summary.md"
    assert CallOutcome(path).line() == f"call summary: {path}"
