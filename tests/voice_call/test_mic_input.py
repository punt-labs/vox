"""Tests for :mod:`punt_vox.voxd.voice_call.mic_input`."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
from punt_vox.voxd.voice_call.mic_input import MicInput

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from punt_vox.voxd.conversation_mode.mic_audio_source import MicAudioSource


class _FakeMicSource:
    """A MicAudioSource double that yields a fixed run of 20 ms chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def chunks(self) -> AsyncIterator[AudioChunk]:
        for pcm in self._chunks:
            yield AudioChunk(pcm=pcm, duration_s=0.02)


def _source(chunks: list[bytes]) -> MicAudioSource:
    return cast("MicAudioSource", _FakeMicSource(chunks))


@pytest.mark.parametrize("echo_control", ["headset", "aec"])
async def test_from_wire_accepts_a_declared_echo_protection(echo_control: str) -> None:
    mic = MicInput.from_wire({"echo_control": echo_control}, _source([b"a\x00"]))
    assert [pcm async for pcm in mic.chunks()] == [b"a\x00"]


@pytest.mark.parametrize(
    "msg", [{}, {"echo_control": True}, {"echo_control": "speakers"}]
)
def test_from_wire_refuses_a_request_without_echo_protection(
    msg: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="Live calls require echo protection") as f:
        MicInput.from_wire(msg, _source([]))
    assert "vox call --headset" in str(f.value)
    assert "vox call --aec" in str(f.value)


@pytest.mark.parametrize("value", ["headset", "aec", "none"])
def test_echo_label_keeps_an_accepted_declaration(value: str) -> None:
    assert MicInput.echo_label(value) == value


@pytest.mark.parametrize("value", ["speakers", "head\nset", True, None, 3])
def test_echo_label_replaces_anything_else(value: object) -> None:
    assert MicInput.echo_label(value) == "other"
