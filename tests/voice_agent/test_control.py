from __future__ import annotations

import pytest

from punt_vox.voxd.voice_agent import ConvAIControl, ConvAIError


def test_credential_identity_uses_scrypt_scoped_to_the_endpoint() -> None:
    with (
        ConvAIControl("test-key") as control,
        ConvAIControl("test-key", base_url="http://127.0.0.1:1") as local,
    ):
        assert control.credential_id == (
            "243f5962451f4d59237848eaa523ceac270484dc29ec063b5151486d152c1b17954"
            "74874cc5e8b7c1266618f2787728b5693ec4272601aff3a2dc0cb5d8a30cf"
        )
        assert local.credential_id != control.credential_id


@pytest.mark.parametrize("resource_id", ["", "tool/other", "tool?force=false"])
def test_control_rejects_invalid_resource_ids_before_request(resource_id: str) -> None:
    with (
        ConvAIControl("test-key", base_url="http://127.0.0.1:1") as control,
        pytest.raises(ConvAIError, match="Invalid ElevenLabs resource ID"),
    ):
        control.delete_tool(resource_id)
