"""Tests for the PCM assembly used by MP3 stitching."""

from pathlib import Path

from pydub import AudioSegment

from punt_vox._audio import MP3Assembly


def test_format_upgrade_resamples_the_entire_prefix(tmp_path: Path) -> None:
    first = tmp_path / "first.mp3"
    AudioSegment.silent(duration=100).export(str(first), format="mp3")
    pause = AudioSegment.silent(duration=73)
    stereo = AudioSegment(
        b"\x00\x01\x00\xff" * 1024, sample_width=2, frame_rate=44100, channels=2
    )
    expected = AudioSegment.from_mp3(first) + pause + stereo + pause

    assembly = MP3Assembly(first)
    assembly.append(pause)
    assembly.append(stereo)
    assembly.append(pause)
    combined = assembly.build()

    assert combined.raw_data == expected.raw_data
    assert combined.frame_rate == expected.frame_rate == 44100
    assert combined.channels == expected.channels == 2
