"""Tests for punt_vox.service.query_answer -- a bounded query's answer."""

from __future__ import annotations

from punt_vox.service.query_answer import QueryAnswer


def test_an_answered_query_is_not_timed_out() -> None:
    assert QueryAnswer.of(holds=True).holds is True
    assert QueryAnswer.of(holds=False).timed_out is False


def test_a_query_with_no_answer_confirms_nothing_and_names_itself() -> None:
    answer = QueryAnswer.no_answer("systemctl is-active voxd")
    assert answer.holds is False
    assert answer.timed_out is True
    assert answer.unanswered == "systemctl is-active voxd"
