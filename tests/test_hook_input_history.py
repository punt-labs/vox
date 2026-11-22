"""Input retention is bounded, sequence ordered, and independent of polling."""

from __future__ import annotations

from punt_vox.voxd.hook_input_history import HookInputHistory


def test_empty_history_has_no_receipts() -> None:
    history = HookInputHistory()
    assert history.last() == (0, "")
    assert history.since(0) == []


def test_history_keeps_newest_32_sequences_despite_reordered_arrival() -> None:
    history = HookInputHistory()
    for sequence in range(40, 0, -1):
        history.ingest(sequence, str(sequence))
    assert history.since(0) == [(seq, str(seq)) for seq in range(9, 41)]
    assert history.since(39) == [(40, "40")]
    assert history.since(40) == []
    assert history.last() == (40, "40")


def test_history_clips_prompt_and_preserves_first_observation() -> None:
    history = HookInputHistory()
    history.ingest(1, "x" * 1001)
    history.ingest(1, "duplicate")
    history.ingest(2, None)
    assert history.since(0) == [(1, "x" * 1000 + "..."), (2, "")]
