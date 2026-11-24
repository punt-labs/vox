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
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from punt_vox.config import ConfigStore

from .fake_agent import FakeConvAIAgent
from .fake_rest import FakeConvAIRest
from .steps import (
    HangUp,
    SendAgentResponse,
    SendToolCall,
    SendUserTranscript,
    WaitForToolResult,
)

if TYPE_CHECKING:
    from collections.abc import Generator

pytestmark = pytest.mark.call_e2e

_STARTUP_TIMEOUT_S = 30.0
_CALL_TIMEOUT_S = 60.0
_TASK = "add greeting.py"


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
    # A bare PATH without tmux: the worker launch must fail on every machine,
    # so the test never starts a real coding session. git stays for worktrees.
    git = Path(shutil.which("git") or "/usr/bin/git").resolve().parent
    return {
        "HOME": str(home),
        "PATH": os.pathsep.join(
            {str(Path(sys.executable).parent): None, str(git): None}
        ),
        "PUNT_TELEMETRY": "off",
        "ELEVENLABS_API_KEY": "test",
        "VOX_CALL_ALLOW_LOOPBACK": "1",
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


def _vox_call(env: dict[str, str], script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "punt_vox", "call", "--script", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=_CALL_TIMEOUT_S,
        check=False,
    )


async def _run_call(
    env: dict[str, str], script: Path
) -> tuple[FakeConvAIAgent, FakeConvAIRest, subprocess.CompletedProcess[str]]:
    """Start both fakes, then voxd with their addresses, then place the call."""
    steps = (
        SendUserTranscript("please add a greeting"),
        SendAgentResponse("Starting a worker on that now.", 1),
        SendToolCall("start_worker", "call_1", {"task": _TASK}),
        WaitForToolResult("call_1"),
        HangUp(),
    )
    async with FakeConvAIAgent(steps) as agent:
        with FakeConvAIRest(agent.signed_url) as rest:
            env = {**env, "VOX_CONVAI_BASE_URL": rest.base_url}
            with _daemon(env):
                result = await asyncio.to_thread(_vox_call, env, script)
            return agent, rest, result


def test_scripted_call_reaches_the_tool_result(
    tmp_path: Path, voxd_env: dict[str, str]
) -> None:
    """One call provisions, dials, answers a tool call, and reports its end."""
    agent, rest, result = asyncio.run(_run_call(voxd_env, _script(tmp_path)))

    assert ("POST", "/v1/convai/agents/create") in rest.requests
    override = cast(
        "dict[str, object]", agent.initiation["conversation_config_override"]
    )
    assert "agent" in override
    tool_result = agent.tool_results["call_1"]
    # No tmux on the daemon's PATH, so the worker launch fails and the daemon
    # reports it to the agent rather than starting a real coding session.
    assert tool_result["is_error"] is True
    assert "tmux" in str(tool_result["result"]).lower() or "No such file" in str(
        tool_result["result"]
    )

    report = result.stdout + result.stderr
    assert "agent: Starting a worker on that now." in report
    assert "tool: start_worker(" in report
    assert ") -> error" in report
    trace_line = next(
        line for line in report.splitlines() if line.startswith("trace: ")
    )
    trace = Path(trace_line.removeprefix("trace: "))
    assert trace.is_file()
    events = [json.loads(line)["type"] for line in trace.read_text().splitlines()]
    assert "ws_open" in events
    assert "client_tool_result" in events
