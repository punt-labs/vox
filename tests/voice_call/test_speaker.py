from __future__ import annotations

from typing import TYPE_CHECKING, cast

from punt_vox.voxd.voice_agent.events import Audio
from punt_vox.voxd.voice_call.speaker import AgentSpeaker

from .fakes import FakePlayback

if TYPE_CHECKING:
    from punt_vox.voxd.playback import PlaybackQueue


async def test_interruption_flushes_and_drops_the_cut_response() -> None:
    playback = FakePlayback()
    speaker = AgentSpeaker(cast("PlaybackQueue", playback))
    await speaker.play(Audio(b"a", 1))
    await speaker.play(Audio(b"b", 1))
    await speaker.interrupt(1)
    await speaker.play(Audio(b"late", 1))
    await speaker.play(Audio(b"next", 2))
    await speaker.close()
    first, second = playback.sinks
    assert (first.fed, first.flushed) == ([b"a", b"b"], 1)
    assert second.fed == [b"next"]
    assert playback.released == 2


async def test_close_without_audio_is_a_no_op() -> None:
    playback = FakePlayback()
    await AgentSpeaker(cast("PlaybackQueue", playback)).close()
    assert playback.sinks == []
