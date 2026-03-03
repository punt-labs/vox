"""Tests for punt_vox.core."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydub import AudioSegment

from punt_vox.core import TRAILING_SILENCE_MS, TTSClient, stitch_audio
from punt_vox.types import (
    MergeStrategy,
    SynthesisRequest,
)


class TestTTSClientSynthesize:
    def test_synthesize_creates_file(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        request = SynthesisRequest(text="hello", voice="joanna", rate=75)
        out = tmp_output_dir / "test.mp3"

        result = tts_client.synthesize(request, out)

        assert result.path == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_synthesize_uses_ssml(
        self,
        mock_boto_client: MagicMock,
        tts_client: TTSClient,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="Hallo", voice="hans", rate=60)
        out = tmp_output_dir / "hallo.mp3"

        tts_client.synthesize(request, out)

        call_kwargs = mock_boto_client.synthesize_speech.call_args.kwargs
        assert call_kwargs["TextType"] == "ssml"
        assert '<prosody rate="60%">' in call_kwargs["Text"]
        assert "Hallo" in call_kwargs["Text"]

    def test_synthesize_passes_voice_params(
        self,
        mock_boto_client: MagicMock,
        tts_client: TTSClient,
        tmp_output_dir: Path,
    ) -> None:
        request = SynthesisRequest(text="Привет", voice="tatyana")
        out = tmp_output_dir / "privet.mp3"

        tts_client.synthesize(request, out)

        call_kwargs = mock_boto_client.synthesize_speech.call_args.kwargs
        assert call_kwargs["VoiceId"] == "Tatyana"
        assert call_kwargs["LanguageCode"] == "ru-RU"
        assert call_kwargs["Engine"] == "standard"

    def test_synthesize_creates_parent_dirs(
        self, tts_client: TTSClient, tmp_path: Path
    ) -> None:
        request = SynthesisRequest(text="hello", voice="joanna")
        out = tmp_path / "nested" / "dir" / "test.mp3"

        result = tts_client.synthesize(request, out)

        assert result.path.exists()

    def test_synthesize_result_metadata(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        request = SynthesisRequest(text="안녕하세요", voice="seoyeon")
        out = tmp_output_dir / "korean.mp3"

        result = tts_client.synthesize(request, out)

        assert result.text == "안녕하세요"
        assert result.voice == "Seoyeon"


class TestTTSClientSynthesizeBatch:
    def test_empty_batch_returns_empty(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        results = tts_client.synthesize_batch([], tmp_output_dir)
        assert results == []

    def test_batch_separate_creates_files(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        requests = [
            SynthesisRequest(text="hello", voice="joanna"),
            SynthesisRequest(text="world", voice="joanna"),
        ]

        results = tts_client.synthesize_batch(
            requests, tmp_output_dir, MergeStrategy.ONE_FILE_PER_INPUT
        )

        assert len(results) == 2
        for r in results:
            assert r.path.exists()

    def test_batch_separate_distinct_files(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        requests = [
            SynthesisRequest(text="hello", voice="joanna"),
            SynthesisRequest(text="world", voice="joanna"),
        ]

        results = tts_client.synthesize_batch(
            requests, tmp_output_dir, MergeStrategy.ONE_FILE_PER_INPUT
        )

        paths = {r.path for r in results}
        assert len(paths) == 2

    def test_batch_merged_creates_single_file(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        requests = [
            SynthesisRequest(text="hello", voice="joanna"),
            SynthesisRequest(text="world", voice="joanna"),
        ]

        results = tts_client.synthesize_batch(
            requests, tmp_output_dir, MergeStrategy.ONE_FILE_PER_BATCH, 300
        )

        assert len(results) == 1
        assert results[0].path.exists()

    def test_batch_merged_text_contains_all(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        requests = [
            SynthesisRequest(text="hello", voice="joanna"),
            SynthesisRequest(text="world", voice="joanna"),
        ]

        results = tts_client.synthesize_batch(
            requests, tmp_output_dir, MergeStrategy.ONE_FILE_PER_BATCH
        )

        assert "hello" in results[0].text
        assert "world" in results[0].text


class TestTTSClientSynthesizePair:
    def test_pair_creates_file(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        req1 = SynthesisRequest(text="strong", voice="joanna")
        req2 = SynthesisRequest(text="stark", voice="hans")
        out = tmp_output_dir / "pair.mp3"

        result = tts_client.synthesize_pair("strong", req1, "stark", req2, out, 500)

        assert result.path == out
        assert result.path.exists()

    def test_pair_result_contains_both_texts(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        req1 = SynthesisRequest(text="strong", voice="joanna")
        req2 = SynthesisRequest(text="stark", voice="hans")
        out = tmp_output_dir / "pair.mp3"

        result = tts_client.synthesize_pair("strong", req1, "stark", req2, out)

        assert "strong" in result.text
        assert "stark" in result.text

    def test_pair_calls_provider_twice(
        self,
        mock_boto_client: MagicMock,
        tts_client: TTSClient,
        tmp_output_dir: Path,
    ) -> None:
        req1 = SynthesisRequest(text="strong", voice="joanna")
        req2 = SynthesisRequest(text="stark", voice="hans")
        out = tmp_output_dir / "pair.mp3"

        tts_client.synthesize_pair("strong", req1, "stark", req2, out)

        assert mock_boto_client.synthesize_speech.call_count == 2


class TestTTSClientSynthesizePairBatch:
    def test_empty_batch_returns_empty(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        results = tts_client.synthesize_pair_batch([], tmp_output_dir)
        assert results == []

    def test_pair_batch_separate(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        pairs = [
            (
                SynthesisRequest(text="strong", voice="joanna"),
                SynthesisRequest(text="stark", voice="hans"),
            ),
            (
                SynthesisRequest(text="house", voice="joanna"),
                SynthesisRequest(text="Haus", voice="hans"),
            ),
        ]

        results = tts_client.synthesize_pair_batch(
            pairs, tmp_output_dir, MergeStrategy.ONE_FILE_PER_INPUT, 500
        )

        assert len(results) == 2
        for r in results:
            assert r.path.exists()

    def test_pair_batch_merged(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        pairs = [
            (
                SynthesisRequest(text="strong", voice="joanna"),
                SynthesisRequest(text="stark", voice="hans"),
            ),
            (
                SynthesisRequest(text="house", voice="joanna"),
                SynthesisRequest(text="Haus", voice="hans"),
            ),
        ]

        results = tts_client.synthesize_pair_batch(
            pairs, tmp_output_dir, MergeStrategy.ONE_FILE_PER_BATCH, 500
        )

        assert len(results) == 1
        assert results[0].path.exists()


class TestStitchAudio:
    def _write_fake_mp3(self, path: Path) -> None:
        """Write minimal valid MP3 bytes using ffmpeg."""
        import subprocess

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=22050:cl=mono",
                "-t",
                "0.1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "32k",
                str(path),
            ],
            capture_output=True,
            check=True,
        )

    def test_stitch_two_segments(self, tmp_path: Path) -> None:
        seg1 = tmp_path / "a.mp3"
        seg2 = tmp_path / "b.mp3"
        self._write_fake_mp3(seg1)
        self._write_fake_mp3(seg2)
        out = tmp_path / "stitched.mp3"

        stitch_audio([seg1, seg2], out, pause_ms=200)

        assert out.exists()
        assert out.stat().st_size > seg1.stat().st_size

    def test_stitch_single_segment(self, tmp_path: Path) -> None:
        seg = tmp_path / "a.mp3"
        self._write_fake_mp3(seg)
        out = tmp_path / "stitched.mp3"

        stitch_audio([seg], out, pause_ms=0)

        assert out.exists()

    def test_stitch_empty_raises(self, tmp_path: Path) -> None:
        out = tmp_path / "stitched.mp3"
        with pytest.raises(ValueError, match="must not be empty"):
            stitch_audio([], out)

    def test_stitch_missing_file_raises(self, tmp_path: Path) -> None:
        out = tmp_path / "stitched.mp3"
        missing = tmp_path / "nonexistent.mp3"
        with pytest.raises(FileNotFoundError, match=r"nonexistent\.mp3"):
            stitch_audio([missing], out)

    def test_stitch_creates_parent_dirs(self, tmp_path: Path) -> None:
        seg = tmp_path / "a.mp3"
        self._write_fake_mp3(seg)
        out = tmp_path / "nested" / "dir" / "stitched.mp3"

        stitch_audio([seg], out)

        assert out.exists()

    def test_stitch_appends_trailing_silence(self, tmp_path: Path) -> None:
        seg = tmp_path / "a.mp3"
        self._write_fake_mp3(seg)
        original_audio: Any = AudioSegment.from_mp3(str(seg))  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
        original_ms: int = len(original_audio)  # pyright: ignore[reportUnknownArgumentType]

        out = tmp_path / "stitched.mp3"
        stitch_audio([seg], out, pause_ms=0)

        stitched: Any = AudioSegment.from_mp3(str(out))  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
        stitched_ms: int = len(stitched)  # pyright: ignore[reportUnknownArgumentType]
        # Stitched file should be longer by ~TRAILING_SILENCE_MS.
        # Allow 50ms tolerance for MP3 frame alignment.
        assert stitched_ms >= original_ms + TRAILING_SILENCE_MS - 50


class TestTrailingSilence:
    """Verify that synthesize() pads the output file with trailing silence."""

    def test_synthesize_pads_output(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        request = SynthesisRequest(text="hello", voice="joanna", rate=75)
        out = tmp_output_dir / "test.mp3"

        tts_client.synthesize(request, out)

        # Read the file back — it should have trailing silence beyond
        # the raw 50ms silence the mock provider writes.
        audio: Any = AudioSegment.from_mp3(str(out))  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
        duration_ms: int = len(audio)  # pyright: ignore[reportUnknownArgumentType]
        assert duration_ms >= TRAILING_SILENCE_MS

    def test_batch_separate_pads_each_file(
        self, tts_client: TTSClient, tmp_output_dir: Path
    ) -> None:
        requests = [
            SynthesisRequest(text="hello", voice="joanna"),
            SynthesisRequest(text="world", voice="joanna"),
        ]

        results = tts_client.synthesize_batch(
            requests, tmp_output_dir, MergeStrategy.ONE_FILE_PER_INPUT
        )

        for r in results:
            audio: Any = AudioSegment.from_mp3(str(r.path))  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
            duration_ms: int = len(audio)  # pyright: ignore[reportUnknownArgumentType]
            assert duration_ms >= TRAILING_SILENCE_MS

    def _make_mp3_bytes(self, duration_ms: int = 50) -> bytes:
        silence = AudioSegment.silent(duration=duration_ms)
        buf = io.BytesIO()
        silence.export(buf, format="mp3")  # pyright: ignore[reportUnknownMemberType]
        return buf.getvalue()
