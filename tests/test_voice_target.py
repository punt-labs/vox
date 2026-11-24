"""Tests for the provider/model roster target and wire request."""

import pytest

from punt_vox.voice_target import VoiceTarget


@pytest.mark.parametrize(
    ("target", "fields"),
    [
        ("openai", {}),
        (VoiceTarget("openai", "  "), {}),
        (VoiceTarget("openai", " gpt-4o-mini-tts "), {"model": "gpt-4o-mini-tts"}),
    ],
)
def test_request_includes_only_a_nonblank_model(
    target: str | VoiceTarget, fields: dict[str, str]
) -> None:
    assert VoiceTarget.request(target) == {
        "type": "voices",
        "provider": "openai",
        **fields,
    }
