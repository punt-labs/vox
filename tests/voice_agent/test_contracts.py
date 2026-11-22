from __future__ import annotations

from punt_vox.voxd.voice_agent import ClientTool, SessionOptions


def test_optional_tool_parameters_and_session_text_only_default() -> None:
    assert ClientTool("clock", "Return UTC time").parameters is None
    assert SessionOptions("prompt", ("tool_clock",)).text_only is False
