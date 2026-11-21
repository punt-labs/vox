"""Tests for EdgeTrim and EdgeTrimmedMusic: dead air cut from generated tracks."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from mutagen.id3 import ID3
from pydub.generators import Sine
from pydub.silence import detect_leading_silence

from punt_vox._audio import AudioSegment
from punt_vox.voxd.programs.edge_trim import EdgeTrim, EdgeTrimmedMusic
from punt_vox.voxd.programs.music_producer import LengthPolicy, MusicProducer
from punt_vox.voxd.programs.part_tags import PartTags
from punt_vox.voxd.programs.producer import PartSpec

if TYPE_CHECKING:
    from pathlib import Path


def _track(path: Path, lead_ms: int, tone_ms: int, tail_ms: int) -> Path:
    """Write a tone padded with ``lead_ms`` and ``tail_ms`` of digital silence."""
    tone = Sine(440).to_audio_segment(duration=tone_ms, volume=-12.0)
    padded = (
        AudioSegment.silent(duration=lead_ms, frame_rate=44100)
        + tone
        + AudioSegment.silent(duration=tail_ms, frame_rate=44100)
    )
    padded.export(str(path), format="mp3")
    return path


def _edges(path: Path) -> tuple[int, int]:
    audio = AudioSegment.from_mp3(str(path))
    return detect_leading_silence(audio), detect_leading_silence(audio.reverse())


@final
class PaddedMusicProvider:
    """A MusicProvider double whose track opens on 2 s and ends on 3 s of silence."""

    __slots__ = ()

    async def generate_track(
        self, prompt: str, duration_ms: int, output_path: Path
    ) -> Path:
        return _track(output_path, lead_ms=2000, tone_ms=1000, tail_ms=3000)


class TestEdgeTrimmedMusic:
    async def test_produced_part_has_no_dead_air_and_keeps_its_tags(
        self, tmp_path: Path
    ) -> None:
        music = EdgeTrimmedMusic(PaddedMusicProvider(), EdgeTrim())
        tags = PartTags(title="calm", album="mix", genre="lofi", index=1, total=12)
        target = tmp_path / "001.mp3"

        await MusicProducer(music, LengthPolicy()).produce(
            PartSpec(prompt="calm", index=1, tags=tags), target
        )

        lead, tail = _edges(target)
        assert lead <= 60
        assert tail <= 60
        assert ID3(target)["TIT2"].text == ["calm"]


class TestEdgeTrim:
    def test_cuts_leading_and_trailing_silence(self, tmp_path: Path) -> None:
        track = _track(tmp_path / "t.mp3", lead_ms=2000, tone_ms=1500, tail_ms=4000)

        EdgeTrim().apply(track)

        lead, tail = _edges(track)
        assert lead <= 60
        assert tail <= 60
        assert 1.4 <= AudioSegment.from_mp3(str(track)).duration_seconds <= 1.6

    def test_leaves_a_track_without_dead_air_untouched(self, tmp_path: Path) -> None:
        track = _track(tmp_path / "t.mp3", lead_ms=0, tone_ms=1500, tail_ms=0)
        before = track.read_bytes()

        EdgeTrim().apply(track)

        assert track.read_bytes() == before

    def test_leaves_an_all_silent_track_untouched(self, tmp_path: Path) -> None:
        track = tmp_path / "t.mp3"
        AudioSegment.silent(duration=1000).export(str(track), format="mp3")
        before = track.read_bytes()

        EdgeTrim().apply(track)

        assert track.read_bytes() == before

    def test_keeps_sound_quieter_than_the_music_but_above_the_floor(
        self, tmp_path: Path
    ) -> None:
        """A soft intro at -40 dBFS is music, not dead air, and must survive."""
        intro = Sine(220).to_audio_segment(duration=1000, volume=-40.0)
        body = Sine(440).to_audio_segment(duration=1000, volume=-12.0)
        track = tmp_path / "t.mp3"
        (intro + body).export(str(track), format="mp3")

        EdgeTrim().apply(track)

        assert AudioSegment.from_mp3(str(track)).duration_seconds >= 1.9

    def test_leaves_an_undecodable_track_for_playback_to_skip(
        self, tmp_path: Path
    ) -> None:
        track = tmp_path / "t.mp3"
        track.write_bytes(b"not audio")

        EdgeTrim().apply(track)

        assert track.read_bytes() == b"not audio"
