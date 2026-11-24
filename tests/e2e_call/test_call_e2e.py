"""Drive one scripted voice call through a real voxd against the fake agent.

No microphone, no credentials and no ElevenLabs: the daemon's REST calls go
to :class:`FakeConvAIRest`, its conversation socket to
:class:`FakeConvAIAgent`, both on loopback, and the caller's audio is a
scripted PCM turn fed through the ``vox call --script`` CLI.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
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
    SendUserTranscript,
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
_INTERRUPTED_STEPS: tuple[Step, ...] = (
    SendUserTranscript("please add a greeting"),
    SendAgentResponse("Starting a worker on that now.", 1),
    SendAudio(_AGENT_PCM, 1),
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


def _wait_for_port_file(run_dir: Path, daemon: subprocess.Popen[bytes]) -> int:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    port_file = run_dir / "serve.port"
    while time.monotonic() < deadline:
        if daemon.poll() is not None:
            raise RuntimeError(f"voxd exited early with {daemon.returncode}")
        if port_file.is_file() and (run_dir / "serve.token").is_file():
            return int(port_file.read_text().strip())
        time.sleep(0.1)
    raise TimeoutError("voxd did not write serve.port")


@pytest.fixture
def voxd_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Return a daemon environment rooted at ``tmp_path`` with no mic needed."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo = _git_repo(tmp_path / "repo")
    ConfigStore(ConfigStore.global_dir()).write_field("worker_repos", repr([str(repo)]))
    return {
        "HOME": str(home),
        "PATH": BarePath(tmp_path).value,
        "PUNT_TELEMETRY": "off",
        "ELEVENLABS_API_KEY": "test",
        "VOX_CALL_ALLOW_LOOPBACK": "1",
        "VOX_PCM_OUTPUT": "silent",
    }


@contextmanager
def _daemon(env: dict[str, str]) -> Generator[int]:
    """Run voxd on a free port under *env*; yield the port it serves."""
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "punt_vox.voxd.daemon", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        run_dir = Path(env["HOME"]) / ".punt-labs" / "vox" / "run"
        yield _wait_for_port_file(run_dir, process)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


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
    """Place one scripted call; whatever happens, leave no call live in voxd."""
    try:
        return _vox(env, "call", "--script", str(script), timeout=_CALL_TIMEOUT_S)
    finally:
        # A call the CLI gave up on is still live in the daemon until stopped;
        # with no call active the stop is refused, which is the normal case.
        _vox(env, "call", "stop", timeout=_STOP_TIMEOUT_S)


async def _run_call(
    env: dict[str, str], script: Path, steps: tuple[Step, ...]
) -> tuple[FakeConvAIAgent, FakeConvAIRest, subprocess.CompletedProcess[str]]:
    """Start both fakes, then voxd with their addresses, then place the call."""
    async with FakeConvAIAgent(steps) as agent:
        with FakeConvAIRest(agent.signed_url) as rest:
            env = {**env, "VOX_CONVAI_BASE_URL": rest.base_url}
            with _daemon(env):
                result = await asyncio.to_thread(_vox_call, env, script)
            return agent, rest, result


def _daemon_log(env: dict[str, str]) -> str:
    return (Path(env["HOME"]) / ".punt-labs" / "vox" / "logs" / "vox.log").read_text()


def _trace_events(report: str) -> list[str]:
    trace_line = next(
        line for line in report.splitlines() if line.startswith("trace: ")
    )
    trace = Path(trace_line.removeprefix("trace: "))
    assert trace.is_file()
    return [json.loads(line)["type"] for line in trace.read_text().splitlines()]


def test_scripted_call_reaches_the_tool_result(
    tmp_path: Path, voxd_env: dict[str, str]
) -> None:
    """One call provisions, dials, answers a tool call, and reports its end."""
    agent, rest, result = asyncio.run(
        _run_call(voxd_env, _script(tmp_path), _TOOL_CALL_STEPS)
    )

    assert agent.fault is None
    assert agent.cut_short is None
    assert rest.errors == []
    assert agent.audio_chunks > 0
    assert ("POST", "/v1/convai/agents/create") in rest.requests
    override = cast(
        "dict[str, object]", agent.initiation["conversation_config_override"]
    )
    assert "agent" in override
    tool_result = agent.tool_results["call_1"]
    # No tmux on the daemon's PATH, so the worker launch fails and the daemon
    # reports it to the agent rather than starting a real coding session. The
    # missing binary is an OSError, so the agent hears the path-free fault and
    # the raw detail (the tmux name) goes to the daemon log alone.
    assert tool_result["is_error"] is True
    assert json.loads(str(tool_result["result"])) == {"error": "operation failed"}
    daemon_log = _daemon_log(voxd_env)
    assert "call tool start_worker failed: " in daemon_log
    assert "tmux" in daemon_log

    report = result.stdout + result.stderr
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
    agent, rest, result = asyncio.run(
        _run_call(voxd_env, _script(tmp_path), _INTERRUPTED_STEPS)
    )

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
    # The daemon opened a stream for the agent's audio and the cut flushed it:
    # the sink's closing report says so, with the unplayed audio dropped.
    daemon_log = _daemon_log(voxd_env)
    assert "PCM flush at" in daemon_log
    assert "interrupted=True" in daemon_log
