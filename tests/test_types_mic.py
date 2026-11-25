"""Tests for punt_vox.types_mic -- the microphone probe report wire value."""

from __future__ import annotations

import pytest

from punt_vox.types_mic import MicProbeReport


def test_report_round_trips_through_the_wire() -> None:
    report = MicProbeReport(
        device="MacBook Pro Microphone",
        sample_rate_hz=16_000,
        peak=812,
        samples=16_000,
        overflows=2,
    )
    assert MicProbeReport.from_wire(report.to_wire()) == report
    assert report.signal
    assert report.to_wire()["overflows"] == 2


def test_from_wire_rejects_a_reply_without_the_overflow_count() -> None:
    wire = MicProbeReport(
        device="mic", sample_rate_hz=16_000, peak=0, samples=16_000, overflows=0
    ).to_wire()
    del wire["overflows"]
    with pytest.raises(ValueError, match="missing a report field"):
        MicProbeReport.from_wire(wire)


def test_silent_report_has_no_signal() -> None:
    report = MicProbeReport(
        device="mic", sample_rate_hz=16_000, peak=0, samples=16_000, overflows=0
    )
    assert not report.signal
    assert report.delivered


def test_empty_report_delivered_nothing_and_has_no_signal() -> None:
    """Zero samples is the device delivering nothing, not the device hearing silence."""
    report = MicProbeReport(
        device="mic", sample_rate_hz=16_000, peak=0, samples=0, overflows=0
    )
    assert not report.delivered
    assert not report.signal


def test_from_wire_rejects_a_non_mapping_reply() -> None:
    with pytest.raises(ValueError, match="mic probe reply"):
        MicProbeReport.from_wire(["not", "a", "mapping"])
