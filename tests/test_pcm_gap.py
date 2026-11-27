"""Dry spells in streamed audio: no click at either edge, and only real ones counted.

Named after the properties of ``docs/pcm-stream.tex``: P6 (a dry spell opens
only on a streaming sink that already played audio), P7 (shaping never moves
a byte count) and P8 (a dry spell is starvation when the same utterance resumes,
a pause when a new utterance begins, and neither when the stream ends in it).
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
from contextlib import asynccontextmanager
from itertools import pairwise
from typing import TYPE_CHECKING, cast

import pytest
from test_pcm_stream import BLOCK, DeviceThread, FakeDevice, open_sink

from punt_vox.voxd import pcm_stream
from punt_vox.voxd.pcm_audio import PCM_SAMPLE_RATE_HZ, PcmBuffer
from punt_vox.voxd.pcm_gap import FADE_FRAMES
from punt_vox.voxd.pcm_output import OutputDeviceLostError
from punt_vox.voxd.pcm_stream import PcmStreamSink
from punt_vox.voxd.voice_agent.events import Audio
from punt_vox.voxd.voice_call.speaker import AgentSpeaker

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from punt_vox.voxd.playback import PlaybackQueue

AMPLITUDE = 12_000
PERIOD = 40  # frames per cycle: a 400 Hz tone, whose peak is easy to cut at
NATURAL_STEP = 2 * math.pi / PERIOD * AMPLITUDE
STEP_LIMIT = 1.25 * NATURAL_STEP
"""The loudest tone's own sample-to-sample step, plus room for the blend.

A hard cut or restart at a peak jumps by the whole amplitude (6x this)."""


def tone(frames: int, *, ends_at_peak: bool = False) -> bytes:
    """Return *frames* of a 400 Hz tone starting (or ending) at a positive peak."""
    first = (10 - (frames - 1)) if ends_at_peak else 10
    return struct.pack(
        f"<{frames}h",
        *(
            round(AMPLITUDE * math.sin(2 * math.pi * (first + n) / PERIOD))
            for n in range(frames)
        ),
    )


def samples(pcm: bytes | bytearray) -> tuple[int, ...]:
    return struct.unpack(f"<{len(pcm) // 2}h", pcm)


def steps(pcm: bytes | bytearray) -> list[int]:
    heard = samples(pcm)
    return [abs(b - a) for a, b in pairwise(heard)]


def steps_before_the_end(heard: bytes | bytearray, audio_end: int) -> int:
    """Return the biggest step up to *audio_end* bytes, where the last audio stops.

    The utterance's own end is left out: this tone stops at full volume, which
    real speech, ending in silence, does not.
    """
    return max(steps(heard[:audio_end]))


class TestNoClickAtADrySpell:
    @pytest.mark.parametrize(
        ("cut_frames", "empty_pulls"),
        [
            pytest.param(650, 2, id="cut-mid-block-long-gap"),
            pytest.param(640, 1, id="cut-on-block-edge"),
            pytest.param(639, 0, id="one-frame-hole"),
            pytest.param(655, 40, id="cut-mid-block-0.8s-gap"),
        ],
    )
    async def test_no_step_above_the_limit_at_either_edge(
        self, cut_frames: int, empty_pulls: int
    ) -> None:
        sink, device = await open_sink()
        sink.feed(tone(cut_frames, ends_at_peak=True))
        device.opened.pull(-(-cut_frames // BLOCK) + empty_pulls)
        pulled = -(-cut_frames // BLOCK) + empty_pulls
        resumed = tone(BLOCK * 3)  # restarts at a peak: the worst restart
        sink.feed(resumed)
        sink.finish()
        device.opened.pull(4)
        audio_end = pulled * BLOCK * 2 + len(resumed)
        assert steps_before_the_end(device.opened.heard, audio_end) <= STEP_LIMIT

    async def test_audio_outside_the_fades_is_untouched(self) -> None:
        first, second = tone(640, ends_at_peak=True), tone(BLOCK * 3)
        sink, device = await open_sink()
        sink.feed(first)
        device.opened.pull(3)
        sink.feed(second)
        sink.finish()
        device.opened.pull(4)
        heard = bytes(device.opened.heard)
        resume = 3 * BLOCK * 2
        assert heard[: len(first)] == first
        assert (
            heard[resume + FADE_FRAMES * 2 : resume + len(second)]
            == (second[FADE_FRAMES * 2 :])
        )

    async def test_a_dry_spell_after_a_fade_in_still_fades_out(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK, ends_at_peak=True))
        device.opened.pull(2)
        sink.feed(tone(FADE_FRAMES // 2, ends_at_peak=True))  # dries up mid-fade-in
        device.opened.pull(3)
        sink.feed(tone(BLOCK * 2))
        sink.finish()
        device.opened.pull(3)
        audio_end = 5 * BLOCK * 2 + BLOCK * 2 * 2
        assert steps_before_the_end(device.opened.heard, audio_end) <= STEP_LIMIT


class TestUninterruptedAudioIsBitIdentical:
    async def test_full_utterance_plays_exactly_as_fed(self) -> None:
        audio = tone(BLOCK * 5)
        sink, device = await open_sink()
        sink.feed(audio)
        sink.finish()
        device.opened.pull(6)
        assert bytes(device.opened.heard[: len(audio)]) == audio

    async def test_chunked_arrival_ahead_of_the_device_plays_exactly_as_fed(
        self,
    ) -> None:
        audio = tone(BLOCK * 5 + 7)
        sink, device = await open_sink()
        for start in range(0, len(audio), 1_000):
            sink.feed(audio[start : start + 1_000])
        sink.finish()
        device.opened.pull(6)
        assert bytes(device.opened.heard[: len(audio)]) == audio

    async def test_nothing_follows_the_audio_but_its_fade_and_silence(self) -> None:
        audio = tone(BLOCK * 2)
        sink, device = await open_sink()
        sink.feed(audio)
        sink.finish()
        device.opened.pull(4)
        assert not any(device.opened.heard[len(audio) + FADE_FRAMES * 2 :])


class TestOnlyRealStarvationIsCounted:
    async def test_a_dry_spell_mid_utterance_is_counted_with_its_length(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK))
        device.opened.pull(1 + 2)  # two starved blocks: 40 ms
        sink.feed(tone(BLOCK))
        device.opened.pull(1)
        report = await sink.close()
        assert report.underflows == 1
        assert report.gap_total.ms == pytest.approx(40.0)
        assert report.gap_longest.ms == pytest.approx(40.0)
        assert report.degraded

    async def test_total_and_longest_cover_every_dry_spell(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK))
        device.opened.pull(2)  # 20 ms dry
        sink.feed(tone(BLOCK))
        device.opened.pull(4)  # 60 ms dry
        sink.feed(tone(BLOCK))
        device.opened.pull(1)
        report = await sink.close()
        assert report.underflows == 2
        assert report.gap_total.ms == pytest.approx(80.0)
        assert report.gap_longest.ms == pytest.approx(60.0)

    async def test_a_stall_inside_one_reply_is_starvation_at_any_length(self) -> None:
        """P8: the same utterance resuming is late audio, however late."""
        sink, device = await open_sink()
        sink.feed(tone(BLOCK), starts_utterance=True)
        device.opened.pull(1 + 75)  # 1.5 s dry
        sink.feed(tone(BLOCK))
        device.opened.pull(1)
        report = await sink.close()
        assert report.underflows == 1
        assert report.gap_longest.ms == pytest.approx(1500.0)
        assert report.gap_total.ms == pytest.approx(1500.0)
        assert report.idle_gaps == 0
        assert report.degraded

    async def test_a_pause_between_two_utterances_is_idle_not_starvation(self) -> None:
        """P8: a spell that resumes where a new utterance begins is a pause."""
        sink, device = await open_sink()
        sink.feed(tone(BLOCK), starts_utterance=True)
        device.opened.pull(1 + 40)  # 0.8 s dry
        sink.feed(tone(BLOCK), starts_utterance=True)
        device.opened.pull(1)
        report = await sink.close()
        assert report.underflows == 0
        assert report.idle_gaps == 1
        assert report.gap_total.nbytes == 0
        assert not report.degraded

    async def test_a_boundary_played_through_does_not_hide_a_later_stall(
        self,
    ) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK * 2), starts_utterance=True)
        sink.feed(tone(BLOCK * 2), starts_utterance=True)  # no gap at the join
        device.opened.pull(4 + 3)
        sink.feed(tone(BLOCK))  # the same utterance resumes late
        device.opened.pull(1)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (1, 0)

    async def test_chunks_that_do_not_start_an_utterance_never_end_a_stall_idle(
        self,
    ) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK))
        device.opened.pull(1 + 40)
        sink.feed(tone(BLOCK))
        device.opened.pull(1)
        assert (await sink.close()).underflows == 1

    async def test_silence_before_the_first_audio_is_not_a_dry_spell(self) -> None:
        """P6: a spell opens only on a sink that already played audio."""
        sink, device = await open_sink()
        device.opened.pull(10)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (0, 0)

    async def test_the_silence_after_the_response_finished_is_not_a_dry_spell(
        self,
    ) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK + 5))
        sink.finish()
        device.opened.pull(10)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (0, 0)
        assert not report.degraded

    async def test_a_dry_spell_open_at_close_is_trailing_not_starvation(self) -> None:
        """Nothing says whether the audio was late, so it is neither: but kept."""
        sink, device = await open_sink()
        sink.feed(tone(BLOCK))
        device.opened.pull(3)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (0, 0)
        assert report.trailing_gap.ms == pytest.approx(40.0)
        assert not report.degraded


class TestShapingMovesNoBytes:
    async def test_conservation_holds_across_a_dry_spell(self) -> None:
        """P7: fed = played + dropped + pending, whatever the fades did."""
        sink, device = await open_sink()
        sink.feed(tone(BLOCK + 11))
        device.opened.pull(4)
        sink.feed(tone(BLOCK))
        device.opened.pull(1)
        report = await sink.close()
        assert report.fed.nbytes == report.played.nbytes + report.dropped.nbytes
        assert report.played.nbytes == (BLOCK + 11 + BLOCK) * 2

    def test_the_fade_is_a_few_milliseconds(self) -> None:
        assert 2 <= FADE_FRAMES / PCM_SAMPLE_RATE_HZ * 1000 <= 5


class TestEveryCutFades:
    """A flush or a finish after audio fades its tail too, and counts no spell."""

    async def test_a_flush_at_a_peak_does_not_click(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK * 3))
        device.opened.pull(1)  # the block ends near a peak
        sink.flush()
        device.opened.pull(1)
        assert max(steps(device.opened.heard)) <= STEP_LIMIT

    @pytest.mark.parametrize("ending", ["flush", "finish"])
    async def test_an_ending_twenty_frames_into_a_fade_still_completes_it(
        self, ending: str
    ) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK - 20, ends_at_peak=True))
        device.opened.pull(1)  # 20 of the 48 fade frames fit in this block
        sink.flush() if ending == "flush" else sink.finish()
        device.opened.pull(1)
        heard = device.opened.heard
        assert max(steps(heard)) <= STEP_LIMIT
        assert not any(heard[(BLOCK + FADE_FRAMES) * 2 :])

    async def test_a_flush_still_discards_every_pending_byte(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK * 3))
        device.opened.pull(1)
        report = sink.flush()
        device.opened.pull(2)
        assert report.dropped.nbytes == BLOCK * 2 * 2
        assert not any(device.opened.heard[(BLOCK + FADE_FRAMES) * 2 :])

    async def test_a_flush_counts_no_dry_spell(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK * 3))
        device.opened.pull(1)
        sink.flush()
        device.opened.pull(2)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (0, 0)

    async def test_finish_ends_an_open_spell_as_trailing(self) -> None:
        """No end-of-utterance marker: the silence is kept, and called neither."""
        sink, device = await open_sink()
        sink.feed(tone(BLOCK, ends_at_peak=True))
        device.opened.pull(2)
        sink.finish()
        device.opened.pull(1)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (0, 0)
        assert report.trailing_gap.ms == pytest.approx(20.0)
        assert not report.degraded

    async def test_a_stall_that_ends_in_a_flush_keeps_its_length(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK, ends_at_peak=True), starts_utterance=True)
        device.opened.pull(1 + 50)  # one second dry
        sink.flush()
        device.opened.pull(1)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (0, 0)
        assert report.trailing_gap.ms == pytest.approx(1000.0)
        assert "trailing_gap=1000 ms" in report.describe(0.0)

    async def test_a_flush_ends_an_open_spell_as_trailing(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK, ends_at_peak=True))
        device.opened.pull(2)
        sink.flush()
        device.opened.pull(1)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (0, 0)
        assert report.trailing_gap.ms == pytest.approx(20.0)


class TestMarksAreFrameAccurate:
    async def test_a_stall_after_a_mark_inside_the_last_block_is_starvation(
        self,
    ) -> None:
        """The second utterance's first frames play in the block that runs dry."""
        sink, device = await open_sink()
        sink.feed(tone(BLOCK + 100), starts_utterance=True)
        sink.feed(tone(10), starts_utterance=True)
        device.opened.pull(2 + 20)
        sink.feed(tone(BLOCK))
        device.opened.pull(1)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (1, 0)

    async def test_an_odd_length_chunk_before_a_new_utterance_is_still_a_pause(
        self,
    ) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK) + b"\x01", starts_utterance=True)
        device.opened.pull(1 + 20)
        sink.feed(b"\x00" + tone(BLOCK), starts_utterance=True)
        device.opened.pull(2)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (0, 1)

    async def test_a_mark_after_the_resumed_block_leaves_the_stall_starvation(
        self,
    ) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK), starts_utterance=True)
        device.opened.pull(1 + 10)
        sink.feed(tone(BLOCK))  # the same utterance resumes
        sink.feed(tone(BLOCK), starts_utterance=True)  # a later one follows it
        device.opened.pull(2)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (1, 0)

    async def test_a_tool_pause_of_three_seconds_is_a_pause(self) -> None:
        """Marked by the tool call, though the turn's event id never changed."""
        sink, device = await open_sink()
        sink.feed(tone(BLOCK), starts_utterance=True)
        device.opened.pull(1 + 150)
        sink.feed(tone(BLOCK), starts_utterance=True)
        device.opened.pull(1)
        report = await sink.close()
        assert (report.underflows, report.idle_gaps) == (0, 1)
        assert not report.degraded

    async def test_the_mark_is_queued_before_the_audio_it_describes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The device thread must never pull audio whose mark is not there yet."""
        sink, _ = await open_sink()
        seen: list[int] = []
        append = PcmBuffer.append

        def spy(buffer: PcmBuffer, pcm: bytes) -> None:
            seen.append(len(sink._gap._marks))  # pyright: ignore[reportPrivateUsage]
            append(buffer, pcm)

        monkeypatch.setattr(PcmBuffer, "append", spy)
        sink.feed(tone(BLOCK), starts_utterance=True)
        assert seen == [1]


class TestTheFlushTailIsPlayedBeforeTheDeviceIsReleased:
    async def test_close_after_a_flush_waits_for_the_tail_then_stops(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK * 3))
        device.opened.pull(1)
        sink.flush()
        closing = asyncio.create_task(sink.close())
        await asyncio.sleep(0.005)
        assert not closing.done()  # waiting for the callback that plays the tail
        device.opened.pull(1)
        await closing
        stream = device.opened
        assert stream.stopped
        assert not stream.aborted
        assert stream.heard_at_release == BLOCK * 2 * 2
        assert max(steps(stream.heard)) <= STEP_LIMIT

    async def test_a_device_that_never_calls_back_is_aborted_after_the_wait(
        self,
    ) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK * 3))
        device.opened.pull(1)
        sink.flush()
        await sink.close()
        assert device.opened.aborted

    async def test_a_cancelled_close_still_releases_the_device(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK * 3))
        device.opened.pull(1)
        sink.flush()
        closing = asyncio.create_task(sink.close())
        await asyncio.sleep(0.005)  # inside the wait for the tail
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert device.opened.closed
        assert device.opened.aborted or device.opened.stopped

    async def test_a_barge_in_through_the_speaker_plays_the_tail(self) -> None:
        device = FakeDevice()

        class Playback:
            mutex = asyncio.Lock()

            @asynccontextmanager
            async def stream_pcm(
                self, *, locked: bool = False
            ) -> AsyncGenerator[PcmStreamSink]:
                assert locked
                sink = PcmStreamSink(asyncio.get_running_loop())
                await sink.open(device)
                try:
                    yield sink
                finally:
                    await sink.close()

        speaker = AgentSpeaker(cast("PlaybackQueue", Playback()))
        await speaker.play(Audio(tone(BLOCK * 3), 1))
        device.opened.pull(1)
        interrupting = asyncio.create_task(speaker.interrupt(1))
        await asyncio.sleep(0.005)
        device.opened.pull(1)
        await interrupting
        await speaker.close()
        assert device.opened.stopped
        assert not device.opened.aborted
        assert max(steps(device.opened.heard)) <= STEP_LIMIT


class TestFinishWaitsForTheTail:
    async def test_the_stream_is_not_drained_until_the_fade_is_done(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK - 20, ends_at_peak=True))
        sink.finish()
        device.opened.pull(1)  # 20 of the 48 fade frames fit in this block
        await asyncio.sleep(0)
        waiter = asyncio.create_task(sink.wait())
        await asyncio.sleep(0.005)
        assert not waiter.done()
        device.opened.pull(1)
        await asyncio.wait_for(waiter, timeout=1)
        await sink.close()
        stream = device.opened
        assert stream.stopped
        assert max(steps(stream.heard)) <= STEP_LIMIT
        assert not any(stream.heard[(BLOCK + FADE_FRAMES) * 2 :])

    async def test_finish_after_the_last_full_block_still_plays_the_tail(self) -> None:
        sink, device = await open_sink()
        sink.feed(tone(BLOCK, ends_at_peak=True))
        device.opened.pull(1)
        sink.finish()
        device.opened.pull(1)
        await asyncio.wait_for(sink.wait(), timeout=1)
        await sink.close()
        assert max(steps(device.opened.heard)) <= STEP_LIMIT


class TestFinishNeverWaitsOnADeadDevice:
    async def test_a_hang_up_after_a_long_silence_needs_no_more_callbacks(
        self,
    ) -> None:
        """The fade finished in the first silent block; nothing is left to play."""
        sink, device = await open_sink()
        sink.feed(tone(BLOCK * 5), starts_utterance=True)
        device.opened.pull(5 + 50)  # 100 ms of audio, then a second of silence
        sink.finish()
        await asyncio.wait_for(sink.wait(), timeout=1)
        report = await sink.close()
        assert device.opened.stopped
        assert report.trailing_gap.ms == pytest.approx(1000.0)

    async def test_a_device_that_stops_calling_back_ends_the_wait(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(pcm_stream, "_STALL_WAIT_S", 0.01)
        sink, device = await open_sink()
        sink.feed(tone(BLOCK * 5), starts_utterance=True)
        device.opened.pull(1)
        sink.finish()  # the other four blocks are never pulled: a stalled device
        with (
            caplog.at_level(logging.WARNING, logger="punt_vox.voxd.pcm_stream"),
            pytest.raises(OutputDeviceLostError),
        ):
            await asyncio.wait_for(sink.wait(), timeout=2)
        assert "made no progress" in caplog.text

    async def test_a_slow_but_live_device_is_waited_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pcm_stream, "_STALL_WAIT_S", 0.05)
        sink, device = await open_sink()
        sink.feed(tone(BLOCK * 6), starts_utterance=True)
        sink.finish()
        with DeviceThread(device.opened):
            await asyncio.wait_for(sink.wait(), timeout=2)
        await sink.close()
