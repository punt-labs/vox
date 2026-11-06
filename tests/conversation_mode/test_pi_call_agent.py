"""Tests for :class:`PiCallAgent`, named for the call-agent Z model's operations.

Each class names the Z operation (``Spawn``, ``SendTurn``, ``ReplyComplete``,
``Teardown``, ``AbortTurn``) or invariant (``readOnly`` over the whole live
extent) it checks. The process is a :class:`FakePiProcess` at the subprocess
boundary; the channel, record parsing, and state machine run for real.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from conversation_mode._pi_rpc_fakes import (
    FakePiProcess,
    PiReply,
    SpawnRecorder,
    ToolUse,
)
from punt_vox.voxd.conversation_mode import pi_call_agent as agent_module
from punt_vox.voxd.conversation_mode.call_agent_errors import (
    CallAgentEndedError,
    CallAgentSpawnError,
    SecretPathDeniedError,
)
from punt_vox.voxd.conversation_mode.call_agent_launch import CallAgentLaunch
from punt_vox.voxd.conversation_mode.call_agent_state import (
    IllegalAgentTransitionError,
)
from punt_vox.voxd.conversation_mode.pi_call_agent import PiCallAgent
from punt_vox.voxd.conversation_mode.pi_turn_reply import GUARD_REFUSAL
from punt_vox.voxd.conversation_mode.pi_turn_verdict import GuardRefusalError
from punt_vox.voxd.conversation_mode.reply import ReplyChunk
from punt_vox.voxd.conversation_mode.reply_assembly import ReplyAssembly
from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy
from punt_vox.voxd.conversation_mode.session_attach import (
    SessionAttach,
    SessionAttachError,
)
from punt_vox.voxd.conversation_mode.turn import TranscribedTurn
from punt_vox.voxd.conversation_mode.turn_timer import TurnStage

_EXEC = "punt_vox.voxd.conversation_mode.pi_rpc_channel.asyncio.create_subprocess_exec"


class _SpyTimer:
    def __init__(self) -> None:
        self.stages: list[TurnStage] = []

    def mark(self, stage: TurnStage, *, detail: str | None = None) -> None:
        del detail
        self.stages.append(stage)


@contextmanager
def _inactivity(seconds: float) -> Generator[None]:
    with patch.object(agent_module, "REPLY_INACTIVITY_S", seconds):
        yield


async def _spawn(
    tmp_path: Path, process: FakePiProcess, timer: _SpyTimer | None = None
) -> tuple[PiCallAgent, SpawnRecorder]:
    recorder = SpawnRecorder(process)
    with patch(_EXEC, recorder):
        agent = await PiCallAgent.spawn(
            CallAgentLaunch(SecretPathPolicy(tmp_path)),
            cwd=tmp_path,
            opening_message="SNAPSHOT: we are fixing the music loop",
            turn_timer=timer or _SpyTimer(),
        )
    return agent, recorder


async def _reply(agent: PiCallAgent, text: str = "what does this do") -> str:
    """Return the reply as a consumer assembles it, retractions applied."""
    chunks: AsyncIterator[ReplyChunk] = agent.send_turn(TranscribedTurn(text=text))
    reply = ReplyAssembly()
    async for chunk in chunks:
        reply.add(chunk)
    return reply.text


async def _chunks(agent: PiCallAgent) -> list[ReplyChunk]:
    return [c async for c in agent.send_turn(TranscribedTurn(text="q"))]


def _retried_after_partial(text: str) -> PiReply:
    """A reply whose first attempt wrote some text, then the provider failed it."""
    return PiReply(text=text, retried=("529 overloaded",), partial="I think the ")


def _retried_after_lead(text: str) -> PiReply:
    """Message 1 spoke and read a file; message 2 wrote some text, then failed."""
    return PiReply(
        text=text,
        lead="Let me look. ",
        retried=("529 overloaded",),
        partial="I think the ",
    )


def test_pi_call_agent_satisfies_the_session_attach_protocol(tmp_path: Path) -> None:
    del tmp_path
    assert issubclass(PiCallAgent, SessionAttach)


class TestSpawn:
    async def test_spawn_delivers_the_snapshot_as_the_opening_message(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess.answering("Ready.")
        await _spawn(tmp_path, process)
        assert "SNAPSHOT: we are fixing the music loop" in process.prompts[0]

    async def test_spawn_returns_an_agent_ready_with_no_turns(
        self, tmp_path: Path
    ) -> None:
        agent, _ = await _spawn(tmp_path, FakePiProcess.answering("Ready."))
        assert (agent.can_accept_turn, agent.turns_handled) == (True, 0)

    async def test_spawn_failure_kills_the_process_and_raises(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess([PiReply(error="401 invalid api key")])
        with pytest.raises(CallAgentSpawnError, match="invalid api key"):
            await _spawn(tmp_path, process)
        assert process.killed

    async def test_a_missing_pi_binary_is_a_spawn_error(self, tmp_path: Path) -> None:
        with (
            patch(_EXEC, side_effect=FileNotFoundError("pi")),
            pytest.raises(CallAgentSpawnError, match="not found on PATH"),
        ):
            await PiCallAgent.spawn(
                CallAgentLaunch(SecretPathPolicy(tmp_path)),
                cwd=tmp_path,
                opening_message="x",
                turn_timer=_SpyTimer(),
            )

    async def test_a_pi_that_exits_at_startup_is_a_spawn_error(
        self, tmp_path: Path
    ) -> None:
        """A guard that cannot load makes pi exit before it reads a prompt."""
        process = FakePiProcess([])
        process.stderr.feed_data(b"Error: Failed to load extension secret_guard.js\n")
        process.exit(1)
        with pytest.raises(CallAgentSpawnError, match="Failed to load extension"):
            await _spawn(tmp_path, process)


class TestSendTurnAndReplyComplete:
    async def test_a_turn_streams_the_reply_and_returns_to_ready(
        self, tmp_path: Path
    ) -> None:
        agent, _ = await _spawn(
            tmp_path, FakePiProcess.answering("Ready.", "It returns the sum.")
        )
        assert await _reply(agent) == "It returns the sum."
        assert (agent.can_accept_turn, agent.turns_handled) == (True, 1)

    async def test_every_turn_goes_to_the_same_live_process(
        self, tmp_path: Path
    ) -> None:
        """The persistence the design exists for: one process, many turns."""
        process = FakePiProcess.answering("Ready.", "one", "two")
        agent, _ = await _spawn(tmp_path, process)
        await _reply(agent, "first")
        await _reply(agent, "second")
        assert process.prompts[1:] == ["first", "second"]

    async def test_the_last_chunk_is_marked_final(self, tmp_path: Path) -> None:
        agent, _ = await _spawn(tmp_path, FakePiProcess.answering("Ready.", "hi"))
        chunks = [c async for c in agent.send_turn(TranscribedTurn(text="q"))]
        assert [c.is_final for c in chunks] == [False, True]

    async def test_send_turn_while_busy_is_refused(self, tmp_path: Path) -> None:
        """SendTurn's precondition: never a second prompt while one is in flight."""
        agent, _ = await _spawn(
            tmp_path, FakePiProcess.answering("Ready.", "a long reply here")
        )
        first = agent.send_turn(TranscribedTurn(text="one"))
        await anext(first)
        second = agent.send_turn(TranscribedTurn(text="two"))
        with pytest.raises(IllegalAgentTransitionError, match="SendTurn"):
            await anext(second)
        await first.aclose()

    async def test_a_provider_error_is_transient_and_leaves_the_agent_ready(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess(
            [PiReply(text="Ready."), PiReply(text="", error="529 overloaded")]
        )
        agent, _ = await _spawn(tmp_path, process)
        with pytest.raises(SessionAttachError, match="529") as caught:
            await _reply(agent)
        assert not isinstance(caught.value, CallAgentEndedError)
        assert agent.can_accept_turn

    async def test_a_provider_error_pi_retried_successfully_yields_the_reply(
        self, tmp_path: Path
    ) -> None:
        """Only the final attempt decides: a retried 529 is not a failed turn."""
        retried = PiReply(text="It returns the sum.", retried=("529 overloaded",))
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), retried]))
        assert await _reply(agent) == "It returns the sum."

    async def test_a_failed_attempt_s_text_is_retracted_before_the_retry_s(
        self, tmp_path: Path
    ) -> None:
        replies = [PiReply("Ready."), _retried_after_partial("It returns the sum.")]
        agent, _ = await _spawn(tmp_path, FakePiProcess(replies))
        chunks = await _chunks(agent)
        restart = next(i for i, c in enumerate(chunks) if c.retracts)
        after = "".join(c.text for c in chunks[restart:])
        assert (chunks[0].text, after) == ("I ", "It returns the sum.")

    async def test_a_retry_after_text_was_written_yields_only_the_retry(
        self, tmp_path: Path
    ) -> None:
        replies = [PiReply("Ready."), _retried_after_partial("It returns the sum.")]
        agent, _ = await _spawn(tmp_path, FakePiProcess(replies))
        assert await _reply(agent) == "It returns the sum."

    async def test_a_retry_before_any_text_retracts_nothing(
        self, tmp_path: Path
    ) -> None:
        retried = PiReply(text="ok", retried=("529 overloaded",))
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), retried]))
        assert not any(c.retracts for c in await _chunks(agent))

    async def test_the_summary_keeps_only_the_successful_attempt_s_text(
        self, tmp_path: Path
    ) -> None:
        replies = [PiReply("Ready."), _retried_after_partial("## Decisions Made")]
        agent, _ = await _spawn(tmp_path, FakePiProcess(replies))
        assert await agent.final_turn("summarize") == "## Decisions Made"

    async def test_a_retry_keeps_the_text_of_the_messages_before_it(
        self, tmp_path: Path
    ) -> None:
        """pi drops only the failed message; the turn's earlier messages stand."""
        replies = [PiReply("Ready."), _retried_after_lead("It returns the sum.")]
        agent, _ = await _spawn(tmp_path, FakePiProcess(replies))
        assert await _reply(agent) == "Let me look. It returns the sum."

    async def test_the_summary_keeps_earlier_messages_and_the_retry(
        self, tmp_path: Path
    ) -> None:
        replies = [PiReply("Ready."), _retried_after_lead("## Decisions Made")]
        agent, _ = await _spawn(tmp_path, FakePiProcess(replies))
        assert await agent.final_turn("summarize") == "Let me look. ## Decisions Made"

    async def test_an_aborted_silent_retry_after_a_spoken_message_completes(
        self, tmp_path: Path
    ) -> None:
        aborted = PiReply(
            lead="Let me look. ", retried=("529",), partial="I think ", aborted=True
        )
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), aborted]))
        assert (await _reply(agent), agent.turns_handled) == ("Let me look. ", 1)

    async def test_retries_that_all_fail_fail_the_turn(self, tmp_path: Path) -> None:
        exhausted = PiReply(text="", retried=("529",), error="529 still overloaded")
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), exhausted]))
        with pytest.raises(SessionAttachError, match="529 still overloaded"):
            await _reply(agent)
        assert agent.can_accept_turn

    async def test_a_retried_opening_message_still_spawns(self, tmp_path: Path) -> None:
        opening = PiReply(text="Ready.", retried=("529 overloaded",))
        agent, _ = await _spawn(tmp_path, FakePiProcess([opening]))
        assert agent.can_accept_turn

    async def test_an_aborted_run_with_no_reply_is_a_failed_turn(
        self, tmp_path: Path
    ) -> None:
        agent, _ = await _spawn(
            tmp_path, FakePiProcess([PiReply("Ready."), PiReply(aborted=True)])
        )
        with pytest.raises(SessionAttachError, match="aborted"):
            await _reply(agent)
        assert agent.can_accept_turn

    async def test_a_retry_that_ends_aborted_with_nothing_new_is_a_failed_turn(
        self, tmp_path: Path
    ) -> None:
        """The failed attempt's retracted text does not make the retry a reply."""
        aborted = PiReply(retried=("529",), partial="I think the ", aborted=True)
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), aborted]))
        with pytest.raises(SessionAttachError, match="aborted"):
            await _reply(agent)
        assert (agent.can_accept_turn, agent.turns_handled) == (True, 0)

    async def test_a_failed_turn_is_not_counted_as_handled(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess([PiReply("Ready."), PiReply(text="", error="5xx")])
        agent, _ = await _spawn(tmp_path, process)
        with pytest.raises(SessionAttachError):
            await _reply(agent)
        assert agent.turns_handled == 0


class TestAbortTurn:
    async def test_silence_past_the_inactivity_bound_aborts_the_turn(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess([PiReply(text="Ready."), PiReply(hang=True)])
        agent, _ = await _spawn(tmp_path, process)
        with _inactivity(0.05), pytest.raises(CallAgentEndedError, match="no event"):
            await _reply(agent)
        assert (process.killed, agent.can_accept_turn) == (True, False)

    async def test_a_long_but_busy_reply_is_never_mistaken_for_a_hang(
        self, tmp_path: Path
    ) -> None:
        """The bound is inactivity, not total time: each record restarts it."""
        tools = tuple(ToolUse("read", {"path": f"src/m{i}.py"}) for i in range(4))
        busy = PiReply(text="Found it in m3.", tools=tools, pace_s=0.03)
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), busy]))
        with _inactivity(0.15):
            assert await _reply(agent) == "Found it in m3."

    async def test_a_process_that_dies_mid_turn_ends_the_agent(
        self, tmp_path: Path
    ) -> None:
        agent, _ = await _spawn(
            tmp_path, FakePiProcess([PiReply("Ready."), PiReply(die=True)])
        )
        with pytest.raises(CallAgentEndedError, match="fatal error"):
            await _reply(agent)
        assert not agent.can_accept_turn

    async def test_a_refused_prompt_ends_the_agent(self, tmp_path: Path) -> None:
        agent, _ = await _spawn(
            tmp_path, FakePiProcess([PiReply("Ready."), PiReply(refuse="nope")])
        )
        with pytest.raises(CallAgentEndedError, match="nope"):
            await _reply(agent)

    async def test_abandoning_the_reply_stream_aborts_the_turn(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess.answering("Ready.", "a long reply here")
        agent, _ = await _spawn(tmp_path, process)
        stream = agent.send_turn(TranscribedTurn(text="q"))
        await anext(stream)
        await stream.aclose()
        assert (process.killed, agent.can_accept_turn) == (True, False)


class TestSecretPathDenyList:
    async def test_a_read_the_guard_blocked_leaves_the_call_running(
        self, tmp_path: Path
    ) -> None:
        """The pi guard refused the read, so nothing was disclosed: carry on."""
        refused = PiReply(
            text="I can't open that file.",
            tools=(ToolUse("read", {"path": ".env"}, blocked=True),),
        )
        process = FakePiProcess([PiReply("Ready."), refused])
        agent, _ = await _spawn(tmp_path, process)
        await _reply(agent, "read me the env file")
        assert (process.killed, agent.can_accept_turn) == (False, True)

    async def test_text_spoken_before_a_refusal_is_followed_by_the_refusal(
        self, tmp_path: Path
    ) -> None:
        """The guard ends the turn, so the model never explains: vox does."""
        refused = PiReply(
            text="Let me look.",
            tools=(ToolUse("read", {"path": ".env"}, blocked=True),),
        )
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), refused]))
        assert await _reply(agent) == f"Let me look. {GUARD_REFUSAL}"

    async def test_a_refused_final_turn_yields_no_summary(self, tmp_path: Path) -> None:
        refused = PiReply(
            text="## Decisions Made",
            tools=(ToolUse("read", {"path": ".env"}, blocked=True),),
        )
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), refused]))
        with pytest.raises(GuardRefusalError):
            await agent.final_turn("summarize")
        assert agent.can_accept_turn

    async def test_every_guard_refusal_is_logged_with_its_reason(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        refused = PiReply(
            text="", tools=(ToolUse("read", {"path": ".env"}, blocked=True),)
        )
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), refused]))
        with caplog.at_level(logging.INFO, logger=agent_module.__name__):
            await _reply(agent)
        assert "vox secret guard: {'path': '.env'}" in caplog.text

    @pytest.mark.parametrize(
        "reason",
        [
            "vox secret guard: cannot list the files grep would read (no rg)",
            "vox secret guard: cannot check what git ignores (spawnSync git ENOENT)",
        ],
    )
    async def test_a_degraded_guard_refusal_is_logged_as_a_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, reason: str
    ) -> None:
        tool = ToolUse("grep", {"pattern": "x"}, blocked=True, reason=reason)
        refused = PiReply(text="", tools=(tool,))
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), refused]))
        with caplog.at_level(logging.INFO, logger=agent_module.__name__):
            await _reply(agent)
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(reason in message for message in warnings)

    async def test_an_extension_error_is_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        broken = PiReply(text="hi", extension_error="guard threw")
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), broken]))
        with caplog.at_level(logging.ERROR, logger=agent_module.__name__):
            await _reply(agent)
        assert "secret_guard.js (tool_call): guard threw" in caplog.text

    async def test_a_turn_ended_by_the_guard_explains_itself(
        self, tmp_path: Path
    ) -> None:
        """The guard terminates the turn on a block, so the model says nothing;
        the caller hears why instead of silence."""
        refused = PiReply(
            text="", tools=(ToolUse("read", {"path": "notes.local.md"}, blocked=True),)
        )
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), refused]))
        assert await _reply(agent) == GUARD_REFUSAL
        assert agent.can_accept_turn

    async def test_a_denied_read_that_executed_ends_the_agent(
        self, tmp_path: Path
    ) -> None:
        """Defence in depth: if a denied read ever ran, the call ends."""
        leak = PiReply(
            text="The key is sk-spike-not-a-real-secret",
            tools=(ToolUse("read", {"path": ".env"}, "FAKE_API_KEY=sk-spike"),),
        )
        process = FakePiProcess([PiReply("Ready."), leak])
        agent, _ = await _spawn(tmp_path, process)
        with pytest.raises(SecretPathDeniedError):
            await _reply(agent, "read me the env file")
        assert (process.killed, agent.can_accept_turn) == (True, False)

    async def test_no_reply_text_from_a_breached_turn_is_ever_yielded(
        self, tmp_path: Path
    ) -> None:
        leak = PiReply(
            text="The key is sk-spike",
            tools=(ToolUse("grep", {"pattern": "KEY", "glob": "**/.env*"}),),
        )
        agent, _ = await _spawn(tmp_path, FakePiProcess([PiReply("Ready."), leak]))
        received: list[str] = []
        stream = agent.send_turn(TranscribedTurn(text="q"))
        with pytest.raises(SecretPathDeniedError):
            received.append((await anext(stream)).text)
        assert received == []


class TestTurnTimer:
    async def test_the_first_tool_event_is_marked_once(self, tmp_path: Path) -> None:
        timer = _SpyTimer()
        tools = (ToolUse("read", {"path": "a.py"}), ToolUse("ls", {"path": "src"}))
        process = FakePiProcess([PiReply("Ready."), PiReply("done", tools=tools)])
        agent, _ = await _spawn(tmp_path, process, timer)
        await _reply(agent)
        assert timer.stages == ["first_tool_event"]

    async def test_a_turn_without_tools_marks_nothing(self, tmp_path: Path) -> None:
        timer = _SpyTimer()
        agent, _ = await _spawn(
            tmp_path, FakePiProcess.answering("Ready.", "hi"), timer
        )
        await _reply(agent)
        assert timer.stages == []


class TestTeardown:
    async def test_close_when_ready_ends_the_process_without_killing_it(
        self, tmp_path: Path
    ) -> None:
        process = FakePiProcess.answering("Ready.")
        agent, _ = await _spawn(tmp_path, process)
        await agent.close()
        assert (process.stdin.is_closing(), process.killed) == (True, False)

    async def test_a_turn_after_teardown_is_refused(self, tmp_path: Path) -> None:
        agent, _ = await _spawn(tmp_path, FakePiProcess.answering("Ready."))
        await agent.close()
        with pytest.raises(IllegalAgentTransitionError):
            await _reply(agent)

    async def test_final_turn_returns_its_text_unspoken(self, tmp_path: Path) -> None:
        process = FakePiProcess.answering("Ready.", "## Decisions Made\n- None.")
        agent, _ = await _spawn(tmp_path, process)
        assert await agent.final_turn("summarize") == "## Decisions Made\n- None."


class TestReadOnlyOverTheWholeLiveExtent:
    async def test_the_process_is_launched_read_only(self, tmp_path: Path) -> None:
        _, recorder = await _spawn(tmp_path, FakePiProcess.answering("Ready."))
        argv = recorder.argv
        assert argv[argv.index("--tools") + 1] == "read,grep,find,ls"

    async def test_nothing_but_prompts_is_ever_sent(self, tmp_path: Path) -> None:
        """No command could widen the tool surface after launch: only prompts."""
        process = FakePiProcess.answering("Ready.", "a", "b", "summary")
        agent, _ = await _spawn(tmp_path, process)
        await _reply(agent)
        await _reply(agent)
        await agent.final_turn("summarize")
        await agent.close()
        assert {command["type"] for command in process.commands} == {"prompt"}

    async def test_provider_keys_never_reach_the_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", "el-secret")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
        _, recorder = await _spawn(tmp_path, FakePiProcess.answering("Ready."))
        assert (
            not {
                "ELEVENLABS_API_KEY",
                "OPENAI_API_KEY",
                "AWS_SECRET_ACCESS_KEY",
            }
            & recorder.env.keys()
        )
