"""Tests for punt_vox.providers.chunked."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from punt_vox.providers.chunked import chunked_synthesize


def _make_synthesize_chunk() -> MagicMock:
    """Create a mock that writes valid MP3 bytes to the given path."""
    import io

    from pydub import AudioSegment

    silence = AudioSegment.silent(duration=50)
    buf = io.BytesIO()
    silence.export(buf, format="mp3")  # pyright: ignore[reportUnknownMemberType]
    mp3_bytes = buf.getvalue()

    def _write_mp3(text: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(mp3_bytes)

    return MagicMock(side_effect=_write_mp3)


class TestChunkedSynthesize:
    def test_single_chunk_no_stitch(self, tmp_path: Path) -> None:
        """Text under the limit calls synthesize_chunk once."""
        mock_synth = _make_synthesize_chunk()
        output = tmp_path / "out.mp3"

        chunked_synthesize(
            text="Hello world.",
            char_limit=4096,
            synthesize_chunk=mock_synth,
            output_path=output,
        )

        assert mock_synth.call_count == 1
        assert output.exists()

    def test_multiple_chunks_stitched(self, tmp_path: Path) -> None:
        """Text over the limit produces multiple calls and output exists."""
        mock_synth = _make_synthesize_chunk()
        output = tmp_path / "out.mp3"

        # 14 chars per sentence, limit 20 -> at least 2 chunks
        text = "Hello world. Goodbye world."
        chunked_synthesize(
            text=text,
            char_limit=20,
            synthesize_chunk=mock_synth,
            output_path=output,
        )

        assert mock_synth.call_count >= 2
        assert output.exists()

    def test_synthesize_chunk_called_with_correct_args(self, tmp_path: Path) -> None:
        """Verify chunk text and path arguments."""
        mock_synth = _make_synthesize_chunk()
        output = tmp_path / "out.mp3"

        chunked_synthesize(
            text="Short.",
            char_limit=4096,
            synthesize_chunk=mock_synth,
            output_path=output,
        )

        args = mock_synth.call_args
        assert args is not None
        chunk_text, chunk_path = args[0]
        assert chunk_text == "Short."
        assert isinstance(chunk_path, Path)
        assert chunk_path.suffix == ".mp3"
