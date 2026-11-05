"""Tests for :class:`CallSnapshot`: the call agent's opening context."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from punt_vox.voxd.conversation_mode.call_snapshot import CallSnapshot


def test_a_snapshot_reads_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.md"
    path.write_text("Topic: the music loop\n")
    assert CallSnapshot.read(path, io.StringIO()).text == "Topic: the music loop"


def test_a_dash_reads_the_snapshot_from_stdin() -> None:
    snapshot = CallSnapshot.read(Path("-"), io.StringIO("Topic: from stdin\n"))
    assert snapshot.text == "Topic: from stdin"


@pytest.mark.parametrize("text", ["", "   \n\t"])
def test_an_empty_snapshot_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="empty"):
        CallSnapshot(text)


def test_an_oversized_snapshot_is_refused() -> None:
    with pytest.raises(ValueError, match="limit"):
        CallSnapshot("x" * (64 * 1024 + 1))


def test_an_unreadable_file_is_a_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read"):
        CallSnapshot.read(tmp_path / "missing.md", io.StringIO())


def test_the_opening_message_carries_the_snapshot_verbatim() -> None:
    message = CallSnapshot("Fix `vox call` latency; see DES-066.").opening_message()
    assert "Fix `vox call` latency; see DES-066." in message


def test_the_opening_message_asks_for_no_tool_use_yet() -> None:
    assert "Do not use any tools yet" in CallSnapshot("x").opening_message()
