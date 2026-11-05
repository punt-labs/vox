"""Tests for punt_vox.service.termination -- what a terminate() came to."""

from __future__ import annotations

from punt_vox.service.termination import Termination


def test_termination_has_exactly_these_outcomes() -> None:
    assert {t.value for t in Termination} == {
        "exited",
        "survived",
        "term_only",
        "refused",
        "kill_refused",
    }
