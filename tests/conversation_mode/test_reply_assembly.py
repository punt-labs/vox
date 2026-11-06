"""Tests for :class:`ReplyAssembly`: a streamed reply's text, retractions applied."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from punt_vox.voxd.conversation_mode.reply import ReplyChunk
from punt_vox.voxd.conversation_mode.reply_assembly import ReplyAssembly

if TYPE_CHECKING:
    import pytest

_RESTART = ReplyChunk(text="", is_final=False, retracts=len("I think the "))


def _assembled(*chunks: ReplyChunk) -> ReplyAssembly:
    reply = ReplyAssembly()
    for chunk in chunks:
        reply.add(chunk)
    return reply


def _text(text: str) -> ReplyChunk:
    return ReplyChunk(text=text, is_final=False)


def test_chunks_join_in_order() -> None:
    assert _assembled(_text("It "), _text("returns.")).text == "It returns."


def test_a_retraction_withdraws_only_the_characters_it_names() -> None:
    retract = ReplyChunk(text="", is_final=False, retracts=len("I think "))
    reply = _assembled(_text("Let me look. "), _text("I think "), retract)
    assert reply.text == "Let me look. "


def test_a_restart_retracts_everything_before_it() -> None:
    reply = _assembled(_text("I think the "), _RESTART, _text("It returns the sum."))
    assert reply.text == "It returns the sum."


def test_retracting_everything_leaves_no_text() -> None:
    assert _assembled(_text("I think the "), _RESTART).text == ""


def test_a_restart_is_logged_with_what_it_discarded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="punt_vox.voxd.conversation_mode")
    _assembled(_text("Let me look. "), _text("I think "), _text("the "), _RESTART)
    assert any("discarded 12" in record.message for record in caplog.records)
