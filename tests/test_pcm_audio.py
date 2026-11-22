"""Tests for the ``pcm_16000`` format helpers and the shared PCM buffer."""

from __future__ import annotations

import pytest

from punt_vox.voxd.pcm_audio import PcmBuffer, PcmDuration


class TestPcmBuffer:
    def test_pull_returns_whole_frames_in_order(self) -> None:
        buffer = PcmBuffer()
        buffer.append(b"\x01\x02\x03")
        assert buffer.pull(4) == b"\x01\x02"
        assert len(buffer) == 1

    def test_clear_reports_dropped_bytes(self) -> None:
        buffer = PcmBuffer()
        buffer.append(b"\x00" * 10)
        assert buffer.clear() == 10
        assert buffer.clear() == 0


class TestPcmDuration:
    def test_one_second_of_pcm_16000(self) -> None:
        assert PcmDuration(32_000).ms == pytest.approx(1000.0)
