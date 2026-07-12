"""Tests for ProgramName, Reason, and PartRef value objects."""

from __future__ import annotations

import pytest

from punt_vox.types_programs import Format, PartRef, ProgramName, Reason


class TestReason:
    def test_valid(self) -> None:
        assert Reason("bad_prompt").text == "bad_prompt"

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
    def test_empty_rejected(self, blank: str) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            Reason(blank)

    def test_preserves_verbatim_text(self) -> None:
        assert Reason("  padded  ").text == "  padded  "

    def test_value_equality_and_hash(self) -> None:
        assert Reason("x") == Reason("x")
        assert hash(Reason("x")) == hash(Reason("x"))
        assert Reason("x") != Reason("y")

    def test_not_equal_to_foreign_type(self) -> None:
        assert Reason("x") != "x"

    def test_repr_and_str(self) -> None:
        assert repr(Reason("x")) == "Reason('x')"
        assert str(Reason("x")) == "x"


class TestProgramName:
    def test_valid(self) -> None:
        assert ProgramName("ambient_techno").value == "ambient_techno"

    @pytest.mark.parametrize("blank", ["", "  "])
    def test_empty_rejected(self, blank: str) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ProgramName(blank)

    @pytest.mark.parametrize("bad", ["a/b", "a\\b", "../evil", "sub/../escape"])
    def test_path_separator_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="path separators"):
            ProgramName(bad)

    @pytest.mark.parametrize("dotted", [".", ".."])
    def test_dot_component_rejected(self, dotted: str) -> None:
        with pytest.raises(ValueError, match="dot path component"):
            ProgramName(dotted)

    def test_value_equality_and_hash(self) -> None:
        assert ProgramName("a") == ProgramName("a")
        assert hash(ProgramName("a")) == hash(ProgramName("a"))
        assert ProgramName("a") != ProgramName("b")

    def test_not_equal_to_foreign_type(self) -> None:
        assert ProgramName("a") != "a"

    def test_repr_and_str(self) -> None:
        assert repr(ProgramName("a")) == "ProgramName('a')"
        assert str(ProgramName("a")) == "a"


class TestPartRef:
    def test_parse(self) -> None:
        ref = PartRef.parse("playlist:2")
        assert ref.format is Format.PLAYLIST
        assert ref.index == 2

    def test_direct_construction(self) -> None:
        ref = PartRef(Format.PODCAST, 3)
        assert ref.format is Format.PODCAST
        assert ref.index == 3

    def test_index_below_one_rejected(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            PartRef(Format.PLAYLIST, 0)

    def test_missing_colon_rejected(self) -> None:
        with pytest.raises(ValueError, match="malformed"):
            PartRef.parse("playlist2")

    def test_unknown_format_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown format"):
            PartRef.parse("mixtape:2")

    def test_non_integer_index_rejected(self) -> None:
        with pytest.raises(ValueError, match="not an integer"):
            PartRef.parse("playlist:two")

    def test_value_equality_and_hash(self) -> None:
        assert PartRef(Format.PLAYLIST, 2) == PartRef(Format.PLAYLIST, 2)
        assert hash(PartRef(Format.PLAYLIST, 2)) == hash(PartRef(Format.PLAYLIST, 2))
        assert PartRef(Format.PLAYLIST, 2) != PartRef(Format.PLAYLIST, 3)
        assert PartRef(Format.PLAYLIST, 2) != PartRef(Format.PODCAST, 2)

    def test_not_equal_to_foreign_type(self) -> None:
        assert PartRef(Format.PLAYLIST, 2) != "playlist:2"

    def test_repr(self) -> None:
        assert repr(PartRef(Format.PLAYLIST, 2)) == "PartRef(playlist:2)"
