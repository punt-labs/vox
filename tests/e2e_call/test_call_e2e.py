"""Drive one scripted voice call through a real voxd against the fake agent.

No microphone, no credentials and no ElevenLabs: the daemon's REST calls go
to :class:`FakeConvAIRest`, its conversation socket to
:class:`FakeConvAIAgent`, both on loopback, and the caller's audio is a
scripted PCM turn fed through the ``vox call --script`` CLI.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from punt_vox.config import ConfigStore

from .bare_path import BarePath
from .fake_agent import FakeConvAIAgent
from .fake_rest import FakeConvAIRest
from .steps import (
    ExpectAudioChunks,
    ExpectPong,
    HangUp,
    SendAgentResponse,
    SendAudio,
    SendInterruption,
    SendPing,
    SendToolCall,
    SendToolResponse,
    SendUserTranscript,
    WaitForLogLine,
    WaitForToolResult,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from .steps import Step

pytestmark = pytest.mark.call_e2e

_STARTUP_TIMEOUT_S = 30.0
# Every `vox call` the tier runs is bounded; a fake that never hangs up must
# fail the test here, not hold the suite.
_CALL_TIMEOUT_S = 45.0
_STOP_TIMEOUT_S = 10.0
_TASK = "add greeting.py"
# What tools/run_coverage.py sets for its subprocess startup hook; the daemon
# and the CLI the tier launches are measured only when these pass through.
_COVERAGE_VARS = ("COVERAGE_PROCESS_START", "COVERAGE_FILE")
_TOOL_CALL_STEPS: tuple[Step, ...] = (
    SendPing(1),
    ExpectPong(1),
    ExpectAudioChunks(1),
    SendUserTranscript("please add a greeting"),
    SendAgentResponse("Starting a worker on that now.", 1),
    SendToolCall("start_worker", "call_1", {"task": _TASK}),
    WaitForToolResult("call_1"),
    HangUp(),
)
# Two seconds of agent audio: far more than plays before the cut lands.
_AGENT_PCM = bytes(16_000 * 2 * 2)
_PLAYBACK_STARTED = "PCM playback started"
_GOODBYE = "Goodbye for now."
# The agent says one closing sentence, runs its built-in end_call system
# tool, and the server hangs up: the order the real server uses.
_AGENT_ENDS_STEPS: tuple[Step, ...] = (
    SendUserTranscript("goodbye"),
    SendAgentResponse(_GOODBYE, 1),
    SendAudio(bytes(16_000 * 2 // 10), 1),  # 100 ms of the closing sentence.
    SendToolResponse("end_call", "end_1", "system"),
    HangUp(),
)


def _interrupted_steps(log: Path) -> tuple[Step, ...]:
    """Reply, let the daemon play some of it, then cut it mid-reply and hang up.

    The wait is what makes the cut mid-reply: without it the interruption
    could land before the device pulled a block, and the flush would drop
    audio that never began to play.
    """
    return (
        SendUserTranscript("please add a greeting"),
        SendAgentResponse("Starting a worker on that now.", 1),
        SendAudio(_AGENT_PCM, 1),
        WaitForLogLine(log, _PLAYBACK_STARTED),
        SendInterruption(1),
        HangUp(),
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return cast("int", probe.getsockname()[1])


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _tail(path: Path, lines: int = 40) -> str:
    """Return the last *lines* of *path*, or a note when it does not exist yet.

    A daemon that crashed may have written bytes that are not UTF-8; they are
    replaced, so the failure message is never itself a decode error.
    """
    if not path.is_file():
        return f"({path.name} was not written)"
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


def _daemon_detail(home: Path, stderr: Path) -> str:
    log = home / ".punt-labs" / "vox" / "logs" / "vox.log"
    return f"--- stderr ---\n{_tail(stderr)}\n--- vox.log ---\n{_tail(log)}"


def _wait_for_port_file(
    run_dir: Path, daemon: subprocess.Popen[bytes], home: Path, stderr: Path
) -> int:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    port_file = run_dir / "serve.port"
    while time.monotonic() < deadline:
        if daemon.poll() is not None:
            msg = f"voxd exited early with {daemon.returncode}\n"
            raise RuntimeError(msg + _daemon_detail(home, stderr))
        if port_file.is_file() and (run_dir / "serve.token").is_file():
            return int(port_file.read_text().strip())
        time.sleep(0.1)
    raise TimeoutError("voxd did not write serve.port\n" + _daemon_detail(home, stderr))


@pytest.fixture
def voxd_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Return a daemon environment rooted at ``tmp_path`` with no mic needed."""
    return _daemon_env(tmp_path, monkeypatch)


def _daemon_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Build the fixture's environment; callable directly by the fixture's tests.

    The environment is bare: nothing of the test process's own leaks in but
    the coverage runner's variables, so a daemon under ``make coverage`` is
    measured and one under plain pytest is not.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo = _git_repo(tmp_path / "repo")
    ConfigStore(ConfigStore.global_dir()).write_field("worker_repos", repr([str(repo)]))
    coverage = {name: os.environ[name] for name in _COVERAGE_VARS if name in os.environ}
    return {
        "HOME": str(home),
        "PATH": BarePath(tmp_path).value,
        "PUNT_TELEMETRY": "off",
        "VOX_CALL_ALLOW_LOOPBACK": "1",
        "VOX_PCM_OUTPUT": "silent",
        **coverage,
    }


@dataclass(frozen=True, slots=True)
class Daemon:
    """A running voxd under test: the port it serves and its process."""

    port: int
    process: subprocess.Popen[bytes]

    def kill(self, timeout: float = 5.0) -> int:
        """SIGKILL the daemon and return its exit status once it has gone."""
        self.process.kill()
        return self.process.wait(timeout=timeout)


@contextmanager
def _daemon(env: dict[str, str]) -> Generator[Daemon]:
    """Run voxd on a free port under *env*; yield the running daemon.

    The daemon must still be running at teardown. One that died during the
    block fails the test with its exit code, its stderr and the tail of its
    log: as the error when the block ended normally, and as a note on the
    block's own exception otherwise, so a call timeout or a CLI failure
    caused by a daemon crash still shows the crash.
    """
    port = _free_port()
    home = Path(env["HOME"])
    stderr_path = home / "voxd.stderr"
    with stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", "punt_vox.voxd.daemon", "--port", str(port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )
    try:
        # A startup failure already carries the daemon detail in its own
        # error, so only an exit after startup is added as a note.
        run_dir = home / ".punt-labs" / "vox" / "run"
        port = _wait_for_port_file(run_dir, process, home, stderr_path)
        try:
            yield Daemon(port, process)
        except BaseException as exc:
            if (exited := process.poll()) is not None:
                exc.add_note(_exit_detail(exited, home, stderr_path))
            raise
    finally:
        exited = process.poll()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    assert exited is None, _exit_detail(exited, home, stderr_path)
    # A stop signal ends the daemon by a normal exit, not by the signal:
    # that is what lets Python's own shutdown (and the coverage save under
    # the coverage runner) run after uvicorn's.
    assert process.returncode == 0, _exit_detail(process.returncode, home, stderr_path)


def _exit_detail(exited: int, home: Path, stderr: Path) -> str:
    return f"voxd exited with {exited} during the test\n" + _daemon_detail(home, stderr)


def _script(tmp_path: Path) -> Path:
    pcm = tmp_path / "turn.pcm"
    pcm.write_bytes(bytes(640 * 10))  # 200 ms of silence at 16 kHz s16le
    script = tmp_path / "turns.jsonl"
    script.write_text(json.dumps({"pcm_path": "turn.pcm", "pause_s": 0.5}) + "\n")
    return script


def _vox(
    env: dict[str, str], *args: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "punt_vox", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _vox_call(env: dict[str, str], script: Path) -> subprocess.CompletedProcess[str]:
    """Place one scripted call; whatever happens, leave no call live in voxd.

    The call's own outcome is primary: when it raised or exited non-zero, a
    stop failure is attached to that exception as a note, never raised over
    it, and a non-zero exit skips the end-line wait so a refused start
    surfaces the CLI's own output immediately. When the call exited 0, the
    daemon is given time to finish the call before the cleanup stop, and a
    call it never finished, or a stop that failed, is the error.
    """
    try:
        result = _vox(env, "call", "--script", str(script), timeout=_CALL_TIMEOUT_S)
    except BaseException as exc:
        if (failure := _stop_failure(env)) is not None:
            exc.add_note(failure)
        raise
    if result.returncode != 0:
        err = AssertionError(
            f"vox call exited {result.returncode}: {result.stdout}{result.stderr}"
        )
        if (failure := _stop_failure(env)) is not None:
            err.add_note(failure)
        raise err
    unfinished = _unfinished_failure(env, _STOP_TIMEOUT_S)
    unstopped = _stop_failure(env)
    if (failure := unfinished or unstopped) is not None:
        raise AssertionError(failure)
    return result


def _unfinished_failure(
    env: dict[str, str], timeout_s: float
) -> str | None:  # None: the daemon logged the call's end.
    """Wait for the daemon's end line, written once the last reply has played.

    The CLI returns when the status reads ended, which the daemon publishes
    before the speaker drains the final reply; a stop sent then flushes the
    audio a test measures. The end line follows the drain, so the cleanup
    stop waits for it.
    """
    if _log_contains(_log_path(env), "Call ended: ", timeout_s=timeout_s):
        return None
    return f"voxd did not log the call's end within {timeout_s:.0f}s"


def _log_contains(path: Path, text: str, *, timeout_s: float) -> bool:
    """Poll a log file until it carries *text*; False once *timeout_s* passes."""
    deadline = time.monotonic() + timeout_s
    while True:
        if path.is_file() and text in path.read_text(errors="replace"):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _stop_failure(env: dict[str, str]) -> str | None:  # None: the stop was fine.
    """Stop a call the CLI may have left live; describe a stop that failed.

    A refusal because no call is active is the normal case after a call that
    ended on its own.
    """
    try:
        stop = _vox(env, "call", "stop", timeout=_STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return f"vox call stop hung for {_STOP_TIMEOUT_S:.0f}s after the call"
    refused = stop.returncode == 2 and "no call is active" in stop.stderr
    if stop.returncode == 0 or refused:
        return None
    return f"vox call stop exited {stop.returncode}: {stop.stdout}{stop.stderr}"


@dataclass(frozen=True, slots=True)
class CallRun:
    """What one scripted call left behind: the CLI's run and the daemon's status."""

    result: subprocess.CompletedProcess[str]
    status: dict[str, object]  # ``vox call status --json`` after the call ended.


def _call_then_teardown(env: dict[str, str], script: Path) -> CallRun:
    """Place the call, read its final status, then delete the agent and tools."""
    result = _vox_call(env, script)
    status = _vox(env, "call", "status", "--json", timeout=_STOP_TIMEOUT_S)
    assert status.returncode == 0, status.stdout + status.stderr
    teardown = _vox(env, "call", "teardown", timeout=_STOP_TIMEOUT_S)
    assert teardown.returncode == 0, teardown.stdout + teardown.stderr
    return CallRun(result, cast("dict[str, object]", json.loads(status.stdout)))


async def _run_call(
    env: dict[str, str], script: Path, steps: tuple[Step, ...]
) -> tuple[FakeConvAIAgent, FakeConvAIRest, CallRun]:
    """Start both fakes, then voxd with their addresses, then place the call.

    After the call the daemon tears its agent and tools down through the
    fake, so every DELETE the real client sends (with its ``?force=true``)
    is answered by an id the fake issued.
    """
    async with FakeConvAIAgent(steps) as agent:
        with FakeConvAIRest(agent.signed_url) as rest:
            env = {**env, "VOX_CONVAI_BASE_URL": rest.base_url}
            with _daemon(env):
                run = await asyncio.to_thread(_call_then_teardown, env, script)
            return agent, rest, run


def _log_path(env: dict[str, str]) -> Path:
    return Path(env["HOME"]) / ".punt-labs" / "vox" / "logs" / "vox.log"


def _daemon_log(env: dict[str, str]) -> str:
    return _log_path(env).read_text()


def _flush_dropped_ms(daemon_log: str) -> float:
    """Return how much queued audio the cut flushed, from the flush log line."""
    match = re.search(r"PCM flush at [\d.]+ ms: dropped ([\d.]+) ms", daemon_log)
    assert match is not None, "no flush line in the daemon log"
    return float(match.group(1))


@dataclass(frozen=True, slots=True)
class _StreamClose:
    """The sink's closing report for the last PCM stream the daemon played."""

    played_ms: int
    dropped_ms: int
    interrupted: bool

    _LINE = re.compile(
        r"PCM stream closed at \d+ ms: fed \d+ ms, played (\d+) ms, "
        r"dropped (\d+) ms, interrupted=(True|False)"
    )

    @classmethod
    def last(cls, daemon_log: str) -> _StreamClose:
        """Parse the newest closing report; the log must carry one."""
        reports = cls._LINE.findall(daemon_log)
        assert reports, daemon_log
        played, dropped, interrupted = reports[-1]
        return cls(int(played), int(dropped), interrupted == "True")


def _trace_events(report: str) -> list[str]:
    trace_line = next(
        (line for line in report.splitlines() if line.startswith("trace: ")), None
    )
    assert trace_line is not None, report
    trace = Path(trace_line.removeprefix("trace: "))
    assert trace.is_file(), report
    return [json.loads(line)["type"] for line in trace.read_text().splitlines()]


def test_the_daemon_env_carries_the_coverage_runner_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the coverage runner, the daemon and CLI it spawns are measured too.

    tools/run_coverage.py sets these two for the subprocess startup hook;
    everything else in the test process's environment stays out.
    """
    monkeypatch.setenv("COVERAGE_PROCESS_START", "/cfg/pyproject.toml")
    monkeypatch.setenv("COVERAGE_FILE", "/cfg/.coverage")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "must-not-leak")
    env = _daemon_env(tmp_path, monkeypatch)
    assert env["COVERAGE_PROCESS_START"] == "/cfg/pyproject.toml"
    assert env["COVERAGE_FILE"] == "/cfg/.coverage"
    assert "ELEVENLABS_API_KEY" not in env


def test_the_daemon_env_is_bare_without_the_coverage_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COVERAGE_PROCESS_START", raising=False)
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    env = _daemon_env(tmp_path, monkeypatch)
    assert "COVERAGE_PROCESS_START" not in env
    assert "COVERAGE_FILE" not in env


def test_tail_survives_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    """A daemon's stderr with an invalid byte still tails into the failure message."""
    stderr = tmp_path / "voxd.stderr"
    stderr.write_bytes(b"first line\nTraceback \xff here\n")
    assert _tail(stderr) == "first line\nTraceback \ufffd here"


def _hang(
    env: dict[str, str], *args: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    """Stand in for a vox CLI whose every invocation runs out its timeout."""
    del env
    raise subprocess.TimeoutExpired(["vox", *args], timeout)


def test_a_hung_stop_after_a_timed_out_call_is_a_note_on_the_call_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The call's timeout stays the error; the stop's hang rides along as a note."""
    monkeypatch.setattr(f"{__name__}._vox", _hang)
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        _vox_call({}, tmp_path / "turns.jsonl")
    assert failure.value.cmd[1:3] == ["call", "--script"]
    assert failure.value.timeout == _CALL_TIMEOUT_S
    assert failure.value.__notes__ == [
        f"vox call stop hung for {_STOP_TIMEOUT_S:.0f}s after the call"
    ]


def test_a_call_the_daemon_never_finished_is_the_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no end line in the log, the cleanup names the unfinished call."""
    stopped: list[str] = []

    def stop(env: dict[str, str], *args: str, timeout: float) -> object:
        del env, timeout
        stopped.append(" ".join(args))
        return subprocess.CompletedProcess(["vox", *args], 0, "", "")

    monkeypatch.setattr(f"{__name__}._vox", stop)
    monkeypatch.setattr(f"{__name__}._STOP_TIMEOUT_S", 0.1)
    env = {"HOME": str(tmp_path)}
    with pytest.raises(AssertionError, match="did not log the call's end within 0s"):
        _vox_call(env, tmp_path / "turns.jsonl")
    assert stopped == ["call --script " + str(tmp_path / "turns.jsonl"), "call stop"]


def test_a_nonzero_call_exit_surfaces_cli_output_without_waiting_for_end_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refused start fails at once with CLI output and skips the end-line wait."""
    calls: list[str] = []

    def stub_vox(
        env: dict[str, str], *args: str, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del env, timeout
        calls.append(" ".join(args))
        if args[:2] == ("call", "stop"):
            return subprocess.CompletedProcess(
                ["vox", *args], 2, "", "no call is active\n"
            )
        return subprocess.CompletedProcess(
            ["vox", *args],
            1,
            "starting call\n",
            "Call unavailable: start refused by probe\n",
        )

    def fail_if_waited(env: dict[str, str], timeout_s: float) -> str | None:
        del env, timeout_s
        pytest.fail("_unfinished_failure must not run when vox call exited non-zero")

    monkeypatch.setattr(f"{__name__}._vox", stub_vox)
    monkeypatch.setattr(f"{__name__}._unfinished_failure", fail_if_waited)
    script = tmp_path / "turns.jsonl"
    with pytest.raises(AssertionError) as failure:
        _vox_call({"HOME": str(tmp_path)}, script)
    message = str(failure.value)
    assert "vox call exited 1:" in message
    assert "starting call\n" in message
    assert "Call unavailable: start refused by probe\n" in message
    assert not hasattr(failure.value, "__notes__")
    assert calls == [f"call --script {script}", "call stop"]


def test_a_stop_failure_after_a_nonzero_call_exit_is_a_note_on_the_call_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stop failure after a non-zero call exit rides along as a note."""
    calls: list[str] = []

    def stub_vox(
        env: dict[str, str], *args: str, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del env
        calls.append(" ".join(args))
        if args[:2] == ("call", "stop"):
            raise subprocess.TimeoutExpired(["vox", *args], timeout)
        return subprocess.CompletedProcess(
            ["vox", *args], 2, "", "Error: start refused by probe\n"
        )

    def fail_if_waited(env: dict[str, str], timeout_s: float) -> str | None:
        del env, timeout_s
        pytest.fail("_unfinished_failure must not run when vox call exited non-zero")

    monkeypatch.setattr(f"{__name__}._vox", stub_vox)
    monkeypatch.setattr(f"{__name__}._unfinished_failure", fail_if_waited)
    script = tmp_path / "turns.jsonl"
    with pytest.raises(AssertionError) as failure:
        _vox_call({"HOME": str(tmp_path)}, script)
    message = str(failure.value)
    assert "vox call exited 2:" in message
    assert "Error: start refused by probe\n" in message
    assert failure.value.__notes__ == [
        f"vox call stop hung for {_STOP_TIMEOUT_S:.0f}s after the call"
    ]
    assert calls == [f"call --script {script}", "call stop"]


def test_the_cleanup_waits_for_the_end_line_before_stopping(tmp_path: Path) -> None:
    log = tmp_path / "vox.log"
    assert not _log_contains(log, "Call ended: ", timeout_s=0.1)
    log.write_text("Call live: conversation c\nCall ended: ended by agent\n")
    assert _log_contains(log, "Call ended: ", timeout_s=0.1)


def test_a_daemon_killed_mid_call_puts_its_detail_on_the_propagating_error(
    voxd_env: dict[str, str],
) -> None:
    """The block's own error, not the fixture's assert, carries the daemon detail."""
    with pytest.raises(subprocess.TimeoutExpired) as failure, _daemon(voxd_env) as voxd:
        assert voxd.kill() == -signal.SIGKILL
        raise subprocess.TimeoutExpired(["vox", "call"], _CALL_TIMEOUT_S)
    (note,) = failure.value.__notes__
    assert note.startswith(f"voxd exited with {-signal.SIGKILL} during the test\n")
    assert "--- vox.log ---" in note


def test_a_terminated_daemon_exits_normally(voxd_env: dict[str, str]) -> None:
    """SIGTERM runs the daemon's shutdown and then Python's; the exit status is 0."""
    with _daemon(voxd_env) as voxd:
        assert voxd.process.poll() is None


def test_scripted_call_reaches_the_tool_result(
    tmp_path: Path, voxd_env: dict[str, str]
) -> None:
    """One call provisions, dials, answers a tool call, and reports its end."""
    agent, rest, run = asyncio.run(
        _run_call(voxd_env, _script(tmp_path), _TOOL_CALL_STEPS)
    )
    result = run.result

    assert agent.fault is None
    assert agent.cut_short is None
    assert rest.errors == []
    assert agent.audio_chunks > 0
    assert ("POST", "/v1/convai/agents/create") in rest.requests
    # Teardown deleted the agent and every tool with the real client's query;
    # rest.errors == [] above proves each named an id the fake issued.
    deletes = [path for method, path in rest.requests if method == "DELETE"]
    assert deletes, rest.requests
    assert all(path.endswith("?force=true") for path in deletes), deletes
    assert any(path.startswith("/v1/convai/agents/") for path in deletes), deletes
    assert any(path.startswith("/v1/convai/tools/") for path in deletes), deletes
    override = cast(
        "dict[str, object]", agent.initiation["conversation_config_override"]
    )
    assert "agent" in override
    tool_result = agent.tool_results["call_1"]
    # No tmux on the daemon's PATH, so the worker launch fails and the daemon
    # reports it to the agent rather than starting a real coding session. The
    # missing binary is an OSError, so the agent hears the path-free fault and
    # the raw detail (the binary's name) goes to the daemon log alone. On a
    # Linux host running inside a systemd service (a CI runner), the launch
    # is wrapped in systemd-run, which the bare PATH lacks too, so that is
    # the name the log carries there.
    assert tool_result["is_error"] is True
    assert json.loads(str(tool_result["result"])) == {"error": "operation failed"}
    daemon_log = _daemon_log(voxd_env)
    assert "call tool start_worker failed: " in daemon_log
    assert "No such file or directory" in daemon_log, daemon_log
    assert "'tmux'" in daemon_log or "'systemd-run'" in daemon_log, daemon_log

    report = result.stdout + result.stderr
    assert result.returncode == 0, report
    assert "agent: Starting a worker on that now." in report
    assert "tool: start_worker(" in report
    assert ") -> error" in report
    events = _trace_events(report)
    assert "ws_open" in events
    assert "pong" in events
    assert "client_tool_result" in events


def test_interruption_then_hangup_ends_the_call_cleanly(
    tmp_path: Path, voxd_env: dict[str, str]
) -> None:
    """An interruption mid-reply, then a remote hangup, is a clean end, not a fault."""
    agent, rest, run = asyncio.run(
        _run_call(voxd_env, _script(tmp_path), _interrupted_steps(_log_path(voxd_env)))
    )
    result = run.result

    assert agent.fault is None
    assert agent.cut_short is None
    assert rest.errors == []
    report = result.stdout + result.stderr
    assert result.returncode == 0, report
    assert "call ended" in report
    assert "agent: Starting a worker on that now." in report
    assert not [line for line in report.splitlines() if line.startswith("error: ")]
    assert "interruption" in _trace_events(report)
    assert agent.tool_results == {}
    # The daemon played the start of the reply, then the cut flushed the
    # rest: the sink logged playback beginning before the flush, and its
    # closing report counts audio played and the remainder dropped.
    daemon_log = _daemon_log(voxd_env)
    assert "PCM flush at" in daemon_log
    assert daemon_log.index(_PLAYBACK_STARTED) < daemon_log.index("PCM flush at")
    close = _StreamClose.last(daemon_log)
    assert close.interrupted
    assert close.played_ms > 0
    assert _flush_dropped_ms(daemon_log) > 0


def test_the_agent_ending_the_call_is_a_clean_end_by_the_agent(
    tmp_path: Path, voxd_env: dict[str, str]
) -> None:
    """A goodbye the agent answers with end_call ends the call, credited to the agent.

    The daemon treats the agent's hang-up as a normal end: the CLI exits 0 and
    reports the call ended, the status names the agent as who ended it, the
    end line in the daemon log says so too, and the trace records the agent's
    end_call acknowledgement. The closing sentence plays to its end before
    the hang-up: the sink's closing report shows the stream drained, not cut.
    """
    agent, rest, run = asyncio.run(
        _run_call(voxd_env, _script(tmp_path), _AGENT_ENDS_STEPS)
    )

    assert agent.fault is None
    assert agent.cut_short is None
    assert rest.errors == []
    report = run.result.stdout + run.result.stderr
    assert run.result.returncode == 0, report
    assert "call ended" in report
    assert f"agent: {_GOODBYE}" in report
    assert not [line for line in report.splitlines() if line.startswith("error: ")]
    assert run.status["state"] == "ended", run.status
    assert run.status["ended_by"] == "agent", run.status
    assert run.status["error"] is None, run.status
    daemon_log = _daemon_log(voxd_env)
    ended = [line for line in daemon_log.splitlines() if "Call ended: " in line]
    assert len(ended) == 1, daemon_log
    assert "Call ended: ended by agent, 0 tool calls, duration " in ended[0], ended
    events = _trace_events(report)
    assert "agent_tool_response" in events, events
    assert "agent_end_call" in events, events
    assert "call ended by agent" in report
    close = _StreamClose.last(daemon_log)
    assert not close.interrupted, daemon_log
    assert close.dropped_ms == 0, daemon_log
    assert close.played_ms == 100, daemon_log
