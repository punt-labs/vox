"""Tests for Part, PartStatus, and the FrozenParts failed-Part map."""

from __future__ import annotations

import pytest

from punt_vox.types_programs import Reason
from punt_vox.voxd.programs import FrozenParts, Part, PartStatus


class TestPart:
    def test_valid(self) -> None:
        part = Part("abc", 3)
        assert part.identity == "abc"
        assert part.index == 3

    def test_empty_identity_rejected(self) -> None:
        with pytest.raises(ValueError, match="identity"):
            Part("", 1)

    def test_index_below_one_rejected(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            Part("abc", 0)

    def test_identity_is_equality(self) -> None:
        # Same audio identity, different index metadata -> same Part.
        assert Part("abc", 1) == Part("abc", 9)
        assert Part("abc", 1) != Part("xyz", 1)

    def test_hash_follows_identity(self) -> None:
        assert hash(Part("abc", 1)) == hash(Part("abc", 5))

    def test_dedups_in_a_set(self) -> None:
        assert len({Part("abc", 1), Part("abc", 2)}) == 1

    def test_not_equal_to_foreign_type(self) -> None:
        assert Part("abc", 1) != "abc"

    def test_repr(self) -> None:
        assert repr(Part("abc", 2)) == "Part(identity='abc', index=2)"


def test_part_status_values() -> None:
    assert PartStatus.READY.value == "ready"
    assert {s.value for s in PartStatus} == {
        "pending",
        "generating",
        "ready",
        "failed",
    }


class TestFrozenParts:
    def test_empty(self) -> None:
        empty = FrozenParts.empty()
        assert len(empty) == 0
        assert empty.parts == frozenset()
        assert list(empty) == []

    def test_with_failure_is_immutable(self) -> None:
        empty = FrozenParts.empty()
        grown = empty.with_failure(Part("a", 1), Reason("bad"))
        assert len(empty) == 0
        assert len(grown) == 1
        assert Part("a", 1) in grown

    def test_reason_for(self) -> None:
        fp = FrozenParts.empty().with_failure(Part("a", 1), Reason("bad"))
        assert fp.reason_for(Part("a", 1)) == Reason("bad")
        assert fp.reason_for(Part("z", 9)) is None

    def test_parts_returns_keys(self) -> None:
        fp = FrozenParts.empty().with_failure(Part("a", 1), Reason("bad"))
        assert fp.parts == {Part("a", 1)}

    def test_ordered_by_index(self) -> None:
        fp = (
            FrozenParts.empty()
            .with_failure(Part("c", 3), Reason("r3"))
            .with_failure(Part("a", 1), Reason("r1"))
        )
        assert [part.index for part, _ in fp.ordered()] == [1, 3]

    def test_value_equality_and_hash(self) -> None:
        a = FrozenParts.empty().with_failure(Part("a", 1), Reason("bad"))
        b = FrozenParts.empty().with_failure(Part("a", 1), Reason("bad"))
        assert a == b
        assert hash(a) == hash(b)

    def test_not_equal_to_foreign_type(self) -> None:
        assert FrozenParts.empty() != {}

    def test_repr_names_the_type(self) -> None:
        assert repr(FrozenParts.empty()).startswith("FrozenParts(")
