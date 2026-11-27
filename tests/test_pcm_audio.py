"""Tests for the ``pcm_16000`` format helpers and the shared PCM buffer."""

from __future__ import annotations

import pytest

from punt_vox.voxd.pcm_audio import PcmBuffer, PcmDuration


class TestPcmBuffer:
    def test_pull_into_moves_whole_frames_in_order_and_pads_with_silence(self) -> None:
        buffer = PcmBuffer()
        buffer.append(b"\x01\x02\x03")
        out = bytearray(b"\xff" * 4)
        assert buffer.pull_into(memoryview(out)) == 2
        assert bytes(out) == b"\x01\x02\x00\x00"
        assert len(buffer) == 1

    def test_pull_into_leaves_what_does_not_fit_pending(self) -> None:
        buffer = PcmBuffer()
        buffer.append(b"\x01\x02\x03\x04\x05\x06")
        out = bytearray(4)
        assert buffer.pull_into(memoryview(out)) == 4
        assert bytes(out) == b"\x01\x02\x03\x04"
        assert len(buffer) == 2

    def test_clear_reports_dropped_bytes(self) -> None:
        buffer = PcmBuffer()
        buffer.append(b"\x00" * 10)
        assert buffer.clear() == 10
        assert buffer.clear() == 0


class TestPcmDuration:
    def test_one_second_of_pcm_16000(self) -> None:
        assert PcmDuration(32_000).ms == pytest.approx(1000.0)
