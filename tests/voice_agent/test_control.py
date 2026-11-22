from __future__ import annotations

import pytest

from punt_vox.voxd.voice_agent import ConvAIControl, ConvAIError


@pytest.mark.parametrize("resource_id", ["", "tool/other", "tool?force=false"])
def test_control_rejects_invalid_resource_ids_before_request(resource_id: str) -> None:
    with (
        ConvAIControl("test-key", base_url="http://127.0.0.1:1") as control,
        pytest.raises(ConvAIError, match="Invalid ElevenLabs resource ID"),
    ):
        control.delete_tool(resource_id)
