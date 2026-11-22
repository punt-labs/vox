"""Tests for punt_vox.voxd.playback_result -- PlaybackResult and its helpers."""

from __future__ import annotations

from pathlib import Path

from punt_vox.voxd.playback_result import MAX_STDERR_LEN, PlaybackResult


class TestStderrTruncation:
    """ffplay stderr can be unbounded; the truncator caps it but keeps both ends."""

    def test_short_text_passes_through(self) -> None:
        assert PlaybackResult.truncate_stderr("hello") == "hello"

    def test_long_text_truncated_with_marker(self) -> None:
        text = "A" * 5000 + "B" * 5000
        out = PlaybackResult.truncate_stderr(text)

        assert len(out) < len(text)
        assert "truncated" in out
        assert out.startswith("A")
        assert out.endswith("B")
        # Marker reports the dropped byte count.
        assert str(len(text) - MAX_STDERR_LEN) in out


def _result(rc: int, elapsed_s: float, stderr: str = "") -> PlaybackResult:
    return PlaybackResult(Path("a.mp3"), rc, elapsed_s, stderr, 0.0)


class TestVerdict:
    def test_clean_exit_that_played_is_ok(self) -> None:
        assert _result(0, 1.0).ok

    def test_clean_exit_that_played_nothing_is_not_ok(self) -> None:
        result = _result(0, 0.01)
        assert not result.ok
        assert result.failure_detail() == "played nothing (elapsed 0.010s)"

    def test_nonzero_exit_carries_stderr(self) -> None:
        result = _result(1, 1.0, "no device")
        assert not result.ok
        assert result.failure_detail() == "player exited rc=1: no device"
