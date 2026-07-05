"""Tests for the PlaybackPolicy result types and a conforming fake."""

from __future__ import annotations

from punt_vox.voxd.programs import COMPLETE, Advance, Complete, Part, PlaybackPolicy


def test_advance_carries_the_part() -> None:
    part = Part("a", 1)
    assert Advance(part).part is part


def test_advance_value_equality() -> None:
    assert Advance(Part("a", 1)) == Advance(Part("a", 1))


def test_complete_repr_and_type() -> None:
    assert isinstance(COMPLETE, Complete)
    assert repr(COMPLETE) == "COMPLETE"


def test_fake_policy_satisfies_the_protocol(policy: PlaybackPolicy) -> None:
    pool = (Part("a", 1), Part("b", 2))
    result = policy.next_part(pool, Part("a", 1))
    assert isinstance(result, Advance)
    assert result.part == Part("b", 2)


def test_avoid_repeat_replays_sole_part(policy: PlaybackPolicy) -> None:
    only = Part("a", 1)
    result = policy.next_part((only,), only)
    assert isinstance(result, Advance)
    assert result.part is only
