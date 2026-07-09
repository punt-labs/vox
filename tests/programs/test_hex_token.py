"""Tests for the shared :class:`HexToken` base of AlbumId and PromptFingerprint."""

from __future__ import annotations

import pytest

from punt_vox.voxd.programs.album_id import AlbumId
from punt_vox.voxd.programs.album_tags import PromptFingerprint


class TestValidation:
    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="album id must be non-empty"):
            AlbumId("   ")

    def test_rejects_non_hex(self) -> None:
        with pytest.raises(ValueError, match="must be lowercase hex"):
            PromptFingerprint("XYZ")

    def test_strips_surrounding_whitespace(self) -> None:
        assert AlbumId("  a3f1c9  ").value == "a3f1c9"


class TestValueSemantics:
    def test_equal_values_are_equal_within_a_type(self) -> None:
        assert AlbumId("a3f1c9") == AlbumId("a3f1c9")
        assert hash(AlbumId("a3f1c9")) == hash(AlbumId("a3f1c9"))

    def test_the_subclass_is_part_of_identity(self) -> None:
        # Same hex value, different token types -- never equal, and the class is
        # folded into the hash so they do not collide in a shared dict.
        assert AlbumId("abcdef") != PromptFingerprint("abcdef")
        assert hash(AlbumId("abcdef")) != hash(PromptFingerprint("abcdef"))

    def test_repr_names_the_concrete_subclass(self) -> None:
        assert repr(AlbumId("a3f1c9")) == "AlbumId('a3f1c9')"
        assert repr(PromptFingerprint("deadbeef")) == "PromptFingerprint('deadbeef')"

    def test_str_is_the_bare_hex(self) -> None:
        assert str(AlbumId("a3f1c9")) == "a3f1c9"

    def test_unrelated_type_is_not_equal(self) -> None:
        assert AlbumId("a3f1c9") != "a3f1c9"
