"""Tests for :mod:`punt_vox.client_results` -- decoding voxd's terminal frames."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.client_results import RecordResult, SynthesizeResult


class TestRecordResult:
    def test_audio_frame_becomes_a_store_locator(self) -> None:
        result = RecordResult.from_audio(
            {
                "type": "audio",
                "name": "a.mp3",
                "path": "recordings/a.mp3",
                "bytes": "42",
            }
        )
        assert result == RecordResult(
            id="a.mp3",
            name="a.mp3",
            store_path=Path("recordings/a.mp3"),
            byte_count=42,
        )

    def test_other_frame_is_a_protocol_error(self) -> None:
        with pytest.raises(VoxdProtocolError, match="Expected 'audio'"):
            RecordResult.from_audio({"type": "done"})

    def test_missing_field_is_a_protocol_error(self) -> None:
        with pytest.raises(VoxdProtocolError, match="missing 'bytes'"):
            RecordResult.from_audio({"type": "audio", "name": "a", "path": "p"})

    def test_non_integer_bytes_is_a_protocol_error(self) -> None:
        with pytest.raises(VoxdProtocolError, match="non-integer 'bytes'"):
            RecordResult.from_audio(
                {"type": "audio", "name": "a", "path": "p", "bytes": "many"}
            )


class TestSynthesizeResult:
    def test_plain_frame_reports_cache_hit(self) -> None:
        assert SynthesizeResult.from_terminal("r1", {"cached": True}) == (
            SynthesizeResult("r1", cached=True)
        )

    def test_deduped_frame_carries_its_window(self) -> None:
        result = SynthesizeResult.from_terminal(
            "r2", {"deduped": True, "original_played_at": 1.5}
        )
        assert result == SynthesizeResult(
            "r2", deduped=True, original_played_at=1.5, ttl_seconds_remaining=None
        )
