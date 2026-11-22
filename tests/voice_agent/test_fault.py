from __future__ import annotations

import pytest

from punt_vox.voxd.voice_agent import ConvAIError
from punt_vox.voxd.voice_agent._decode import EventDecoder, Message


def test_client_error_does_not_expose_vendor_message() -> None:
    body = Message({"client_error_event": {"message": "private wire detail"}})

    with pytest.raises(ConvAIError) as failure:
        EventDecoder.decode("client_error", body)

    assert str(failure.value) == "ElevenLabs sent a client_error event"
    assert failure.value.__cause__ is None
