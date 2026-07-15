"""Behavioral tests for the hooks/suppress-output.sh PostToolUse hook.

The hook drives the two-channel display: ``updatedMCPToolOutput`` (the panel
line the user sees) and ``additionalContext`` (text injected back into the
agent). The invariant under test is which tools stay silent.

The same mic tool backs multiple slash commands, so silence is decided per
tool by whether EVERY flow that drives it wants the panel as the whole
response. Only the pure music-control and vibe tools qualify: on success
``music``/``music_play``/``music_next``/``vibe`` put a terminal stop-narration
directive in ``additionalContext`` instead of the result JSON. Every other
tool keeps its RESULT so the flow that needs it can reply — ``record`` returns
saved file paths, ``unmute`` drives ``/vox model|provider``, ``speak`` drives
``/mute``, ``notify`` drives ``/vox c``, and the query tools
(``status``/``who``/``music_list``) report data. On any tool error the failure
must still reach ``additionalContext``.

Driven as a subprocess against the real script — the interface is the
contract, so we exercise the shell, not a reimplementation of it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parent.parent / "hooks" / "suppress-output.sh"

# A distinctive slice of STOP_NARRATION — stable enough to assert on without
# pinning the whole sentence.
_STOP_MARK = "reply with no text, no summary, no narration. Stop."

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None, reason="suppress-output.sh requires jq"
)


def _run_hook(tool: str, result: object) -> dict[str, str]:
    """Run the hook for ``tool`` with ``result`` as the tool response.

    ``result`` is wrapped the way FastMCP delivers a string return: a
    single-element content array whose ``text`` is the JSON payload. Returns
    the parsed ``hookSpecificOutput`` mapping. Raises if the hook exits
    non-zero or emits no output.
    """
    return _invoke(tool, json.dumps(result))


def _run_hook_raw(tool: str, text: str) -> dict[str, str]:
    """Run the hook with a raw, non-JSON ``text`` as the tool response.

    FastMCP surfaces an uncaught tool exception as a bare content string
    (e.g. "Error executing tool music: KeyError: 'style'"), not our
    ``{"error": ...}`` contract. This drives that path.
    """
    return _invoke(tool, text)


def _invoke(tool: str, text: str) -> dict[str, str]:
    """Run the hook for ``tool`` with ``text`` as the response content.

    Returns the parsed ``hookSpecificOutput`` mapping. Raises if the hook
    exits non-zero or emits no output.
    """
    payload = {
        "tool_name": f"mcp__plugin_vox_mic__{tool}",
        "tool_response": [{"type": "text", "text": text}],
    }
    proc = subprocess.run(
        ["bash", str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    parsed = json.loads(proc.stdout)
    output = parsed["hookSpecificOutput"]
    assert isinstance(output, dict)
    return {str(k): str(v) for k, v in output.items()}


class TestSilentToolsStopNarration:
    """Music-control and vibe success replace the result JSON with the directive.

    These are the only tools every slash-command flow drives silently, so the
    panel line is the whole response and the payload must not leak.
    """

    def test_vibe_context_is_the_directive_not_json(self) -> None:
        out = _run_hook("vibe", {"vibe": {"vibe": "focused", "vibe_tags": "[calm]"}})
        assert _STOP_MARK in out["additionalContext"]
        # The raw result must NOT leak — no data for the agent to narrate.
        assert "vibe_tags" not in out["additionalContext"]
        assert "focused" not in out["additionalContext"]
        # The panel line is untouched.
        assert out["updatedMCPToolOutput"] == "♪ vibe shifted to [calm]"

    def test_music_context_is_the_directive(self) -> None:
        out = _run_hook("music", {"status": "generating", "style": "trance"})
        assert _STOP_MARK in out["additionalContext"]
        assert "trance" not in out["additionalContext"]
        assert out["updatedMCPToolOutput"].startswith("♪")

    def test_music_playing_status_is_the_directive(self) -> None:
        out = _run_hook("music", {"status": "playing", "name": "focus-beats"})
        assert _STOP_MARK in out["additionalContext"]
        assert "focus-beats" not in out["additionalContext"]
        assert out["updatedMCPToolOutput"].startswith("♪")

    def test_music_stopped_status_is_the_directive(self) -> None:
        out = _run_hook("music", {"status": "stopped"})
        assert _STOP_MARK in out["additionalContext"]
        assert out["updatedMCPToolOutput"].startswith("♪")

    def test_music_play_context_is_the_directive(self) -> None:
        out = _run_hook("music_play", {"name": "focus-beats"})
        assert _STOP_MARK in out["additionalContext"]
        assert "focus-beats" not in out["additionalContext"]
        assert out["updatedMCPToolOutput"].startswith("♪")

    def test_music_next_context_is_the_directive(self) -> None:
        out = _run_hook(
            "music_next",
            {"message": "♪ Skipping — generating next track...", "applied": True},
        )
        assert _STOP_MARK in out["additionalContext"]
        assert "applied" not in out["additionalContext"]
        # The panel keeps the tool's own message line.
        assert out["updatedMCPToolOutput"] == "♪ Skipping — generating next track..."


class TestReplyToolsKeepData:
    """Tools whose slash-command flows need an agent reply keep the JSON.

    record returns saved file paths (the agent must report them), unmute drives
    ``/vox model|provider`` ("Switched … to X"), speak drives ``/mute`` (a
    phrase reply), and notify drives ``/vox c`` (lists featured voices).
    """

    def test_record_paths_reach_context(self) -> None:
        # HIGH regression: the agent needs the saved paths to report/reuse.
        out = _run_hook(
            "record",
            [{"voice": "Matilda", "path": "/tmp/vox/take-1.mp3"}],
        )
        assert "/tmp/vox/take-1.mp3" in out["additionalContext"]
        assert _STOP_MARK not in out["additionalContext"]
        assert out["updatedMCPToolOutput"].startswith("♪")

    def test_unmute_payload_reaches_context(self) -> None:
        # MED regression: /vox model|provider derive their confirmation text
        # from the payload.
        out = _run_hook("unmute", [{"voice": "Matilda", "model": "eleven_v3"}])
        assert "eleven_v3" in out["additionalContext"]
        assert _STOP_MARK not in out["additionalContext"]
        assert out["updatedMCPToolOutput"].startswith("♪")

    def test_speak_payload_reaches_context(self) -> None:
        out = _run_hook("speak", {"speak": "n"})
        assert '"speak"' in out["additionalContext"]
        assert _STOP_MARK not in out["additionalContext"]
        assert out["updatedMCPToolOutput"] == "♪ chimes only"

    def test_notify_payload_reaches_context(self) -> None:
        out = _run_hook("notify", {"notify": {"notify": "y"}})
        assert '"notify"' in out["additionalContext"]
        assert _STOP_MARK not in out["additionalContext"]
        assert out["updatedMCPToolOutput"] == "♪ vox enabled"


class TestQueryToolsKeepData:
    """A query-tool success keeps the JSON so the agent can report it."""

    def test_status_context_carries_the_data(self) -> None:
        out = _run_hook("status", {"voice": "Matilda", "notify": "y"})
        assert "Matilda" in out["additionalContext"]
        assert _STOP_MARK not in out["additionalContext"]
        assert out["updatedMCPToolOutput"] == "♪ Matilda · notify=y"

    def test_music_list_context_carries_the_data(self) -> None:
        out = _run_hook("music_list", {"tracks": [{"name": "focus-beats"}]})
        assert "focus-beats" in out["additionalContext"]
        assert _STOP_MARK not in out["additionalContext"]


class TestErrorGuardPreserved:
    """On any tool error the failure still reaches additionalContext."""

    def test_control_error_reaches_context(self) -> None:
        out = _run_hook("music", {"error": "voxd unreachable"})
        assert "voxd unreachable" in out["additionalContext"]
        assert _STOP_MARK not in out["additionalContext"]
        assert out["updatedMCPToolOutput"] == "♪ error: voxd unreachable"

    def test_uncaught_exception_string_reaches_context(self) -> None:
        # FastMCP surfaces an uncaught tool exception as a bare, non-JSON
        # string. It matches neither the {"error":...} contract nor a success
        # object/array, so without the bare-string guard it would fall through
        # to a success branch and be overwritten by the stop-directive.
        msg = "Error executing tool music: KeyError: 'style'"
        out = _run_hook_raw("music", msg)
        assert msg in out["additionalContext"]
        assert _STOP_MARK not in out["additionalContext"]
        assert out["updatedMCPToolOutput"] == "♪ error"
