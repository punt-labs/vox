"""Behavioral tests for the hooks/suppress-output.sh PostToolUse hook.

The hook drives the two-channel display: ``updatedMCPToolOutput`` (the panel
line the user sees) and ``additionalContext`` (text injected back into the
agent). The invariant under test is the control/query split — for a
fire-and-forget CONTROL tool the hook must, on success, put a terminal
stop-narration directive in ``additionalContext`` (not the result JSON) so the
agent stays silent; for a QUERY tool it must keep the JSON so the agent can
report the data; and on any tool error the failure must still reach
``additionalContext``.

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


class TestControlToolsStopNarration:
    """A control-tool success replaces the result JSON with the directive."""

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

    def test_music_next_context_is_the_directive(self) -> None:
        out = _run_hook(
            "music_next",
            {"message": "♪ Skipping — generating next track...", "applied": True},
        )
        assert _STOP_MARK in out["additionalContext"]
        assert "applied" not in out["additionalContext"]
        # The panel keeps the tool's own message line.
        assert out["updatedMCPToolOutput"] == "♪ Skipping — generating next track..."

    def test_notify_context_is_the_directive(self) -> None:
        out = _run_hook("notify", {"notify": {"notify": "y"}})
        assert _STOP_MARK in out["additionalContext"]
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
