"""Tests for :mod:`punt_vox.voxd.voice_call.call_outcome`."""

from __future__ import annotations

import pytest

from punt_vox.voxd.voice_call.call_outcome import CallEnd, CallOutcome


@pytest.mark.parametrize(
    ("outcome", "text"),
    [
        (CallOutcome("ended", None, 3), "ended, 3 tool calls"),
        (CallOutcome("ended", None, 1), "ended, 1 tool call"),
        (CallOutcome("ended", None, 0), "ended, 0 tool calls"),
        (CallOutcome("failed", "boom", 0), "failed, 0 tool calls"),
        (CallOutcome("ended", None, 2, CallEnd.AGENT), "ended by agent, 2 tool calls"),
        (CallOutcome("ended", None, 0, CallEnd.PEER), "ended by peer, 0 tool calls"),
        (CallOutcome("ended", None, 1, CallEnd.CALLER), "ended by caller, 1 tool call"),
    ],
)
def test_the_outcome_reads_as_its_log_summary(outcome: CallOutcome, text: str) -> None:
    assert str(outcome) == text


def test_a_failed_outcome_carries_an_error_note() -> None:
    assert CallOutcome("failed", "boom", 0).error_note == ", error boom"


def test_a_clean_outcome_has_no_error_note() -> None:
    assert CallOutcome("ended", None, 2).error_note == ""


def test_who_ended_the_call_reads_as_its_wire_value() -> None:
    assert [str(end) for end in CallEnd] == ["agent", "peer", "caller"]
