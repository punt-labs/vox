"""Real headless Claude Code drives vox's plugin and verifies its recorded speech."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from punt_vox.paths import recordings_dir, run_dir
from roundtrip.backends import load_whisper, whisper_transcript
from roundtrip.oracle import THRESHOLD, assert_transcript, decode

pytestmark = [pytest.mark.agent, pytest.mark.roundtrip]

_PLUGIN = Path(__file__).resolve().parents[2] / "plugin"
_RECORDING = "nightly-agent.mp3"
_TEXT = "The garden gate is open. Please bring the flowers inside."


@pytest.fixture
def claude() -> str:
    executable = shutil.which("claude")
    missing = [
        command
        for command in ("voxd", "vox", "espeak-ng", "ffmpeg")
        if shutil.which(command) is None
    ]
    if executable is None:
        missing.append("claude")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        reason = f"Agent tier requires: {', '.join(missing)}"
        if os.environ.get("VOX_AGENT_REQUIRE") == "1":
            pytest.fail(reason)
        pytest.skip(reason)
    assert executable is not None
    return executable


@pytest.fixture
def vox_daemon(tmp_path: Path) -> Iterator[dict[str, str]]:
    env = {
        "HOME": str(Path.home()),  # tests/conftest.py's per-test HOME
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.environ["PATH"])),
        "PUNT_TELEMETRY": "off",
    }
    project_config = tmp_path / ".punt-labs" / "vox"
    project_config.mkdir(parents=True)
    (project_config / "vox.md").write_text(
        "---\nprovider: espeak\n---\n", encoding="utf-8"
    )
    port_file = run_dir() / "serve.port"
    log = tmp_path / "voxd.log"
    with (
        log.open("w", encoding="utf-8") as output,
        subprocess.Popen(
            ["voxd", "--host", "127.0.0.1", "--port", "0"],
            cwd=tmp_path,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        ) as daemon,
    ):
        try:
            deadline = time.monotonic() + 15
            while not port_file.is_file():
                if daemon.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail(
                        f"voxd did not bind:\n{log.read_text(encoding='utf-8')}"
                    )
                time.sleep(0.05)
            yield env
        finally:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()


def verify_claude_session(
    result: subprocess.CompletedProcess[str], tool: str, api_key: str
) -> float:
    try:
        events = [json.loads(line) for line in result.stdout.splitlines()]
    except json.JSONDecodeError:
        detail = result.stdout
    else:
        session = next(
            (event for event in reversed(events) if event["type"] == "result"), None
        )
        if (
            result.returncode == 0
            and session is not None
            and session.get("subtype") == "success"
            and not session["is_error"]
        ):
            tool_calls = [
                block["name"]
                for event in events
                if event["type"] == "assistant"
                for block in event["message"]["content"]
                if block["type"] == "tool_use"
            ]
            assert tool_calls == [tool], "Claude must call the vox recording tool once"
            return float(session["total_cost_usd"])
        detail = (
            session.get("result") or session.get("subtype", "unknown error")
            if session is not None
            else "missing final result"
        )
    diagnostic = f"{detail}\n{result.stderr}".replace(api_key, "[redacted]").strip()
    pytest.fail(f"Claude Code failed (exit {result.returncode}): {diagnostic[-1500:]}")


async def test_agent_plugin_roundtrip(
    claude: str, vox_daemon: dict[str, str], tmp_path: Path
) -> None:
    try:
        model = load_whisper()
    except pytest.skip.Exception as exc:
        if os.environ.get("VOX_AGENT_REQUIRE") == "1":
            pytest.fail(f"Agent tier requires Whisper: {exc}")
        raise
    manifest = json.loads((_PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    tool = f"mcp__plugin_{manifest['name']}_mic__rec"
    api_key = os.environ["ANTHROPIC_API_KEY"]
    result = subprocess.run(
        [
            claude,
            "-p",
            "--setting-sources",
            "",
            "--settings",
            '{"disableAllHooks":true}',
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--model",
            "haiku",
            "--max-turns",
            "2",
            "--max-budget-usd",
            "0.05",
            "--no-session-persistence",
            "--output-format",
            "stream-json",
            "--verbose",
            "--tools",
            "",
            "--allowedTools",
            tool,
            "--plugin-dir",
            str(_PLUGIN),
        ],
        input=(
            f"Call {tool} exactly once with subcommand 'new', name '{_RECORDING}', "
            f"and text '{_TEXT}'. Make the tool call; do not just describe it."
        ),
        cwd=tmp_path,
        env=vox_daemon
        | {
            "ANTHROPIC_API_KEY": api_key,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "ENABLE_TOOL_SEARCH": "false",
        },
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    cost = verify_claude_session(result, tool, api_key)
    path = recordings_dir() / _RECORDING
    assert path.is_file(), "Claude did not create a recording via the vox plugin"
    decode(path)
    actual = await whisper_transcript(model, path)
    error = assert_transcript(_TEXT, actual, THRESHOLD)
    print(f"agent WER={error:.4f} limit={THRESHOLD}; Claude cost=${cost:.4f}")
    with pytest.raises(AssertionError, match="WER"):
        assert_transcript(_TEXT.replace("garden", "wooden"), actual, THRESHOLD)
