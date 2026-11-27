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


async def test_finish_plays_out_the_last_reply_before_release() -> None:
    playback = FakePlayback()
    speaker = AgentSpeaker(cast("PlaybackQueue", playback))
    await speaker.play(Audio(b"bye", 3))
    await speaker.finish()
    (sink,) = playback.sinks
    assert (sink.fed, sink.finished, sink.flushed) == ([b"bye"], True, 0)
    assert playback.released == 1


async def test_the_call_keeps_the_output_across_a_cut() -> None:
    playback = FakePlayback()
    speaker = AgentSpeaker(cast("PlaybackQueue", playback))
    await speaker.play(Audio(b"a", 1))
    await speaker.interrupt(1)
    assert playback.mutex.locked()  # queued hook speech cannot slip in here
    await speaker.play(Audio(b"b", 2))
    await speaker.close()
    assert not playback.mutex.locked()
    assert [sink.fed for sink in playback.sinks] == [[b"a"], [b"b"]]


async def test_an_interruption_before_any_audio_only_records_the_cut() -> None:
    playback = FakePlayback()
    speaker = AgentSpeaker(cast("PlaybackQueue", playback))
    await speaker.interrupt(4)
    await speaker.play(Audio(b"late", 4))
    assert playback.sinks == []


async def test_a_chunk_of_a_new_turn_begins_an_utterance() -> None:
    playback = FakePlayback()
    speaker = AgentSpeaker(cast("PlaybackQueue", playback))
    for pcm, event_id in ((b"a", 1), (b"b", 1), (b"c", 2)):
        await speaker.play(Audio(pcm, event_id))
    await speaker.close()
    (sink,) = playback.sinks
    assert sink.starts == [True, False, True]


async def test_a_tool_call_inside_one_turn_begins_an_utterance() -> None:
    """The pause shares the turn's event id; only the tool call marks it."""
    playback = FakePlayback()
    speaker = AgentSpeaker(cast("PlaybackQueue", playback))
    await speaker.play(Audio(b"a", 79))
    speaker.note_tool_call()
    await speaker.play(Audio(b"b", 79))
    await speaker.play(Audio(b"c", 79))
    await speaker.close()
    (sink,) = playback.sinks
    assert sink.starts == [True, True, False]


async def test_a_new_sink_after_a_cut_starts_fresh() -> None:
    playback = FakePlayback()
    speaker = AgentSpeaker(cast("PlaybackQueue", playback))
    await speaker.play(Audio(b"a", 1))
    await speaker.interrupt(1)
    await speaker.play(Audio(b"b", 2))
    await speaker.close()
    assert [sink.starts for sink in playback.sinks] == [[True], [True]]
