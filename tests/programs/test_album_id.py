"""Tests for the ``AlbumId`` value object and its collision-avoiding mint."""

from __future__ import annotations

import pytest

from punt_vox.voxd.programs.album_id import AlbumId


class TestConstruction:
    def test_accepts_lowercase_hex(self) -> None:
        assert AlbumId("a3f1c9").value == "a3f1c9"

    def test_strips_surrounding_whitespace(self) -> None:
        assert AlbumId("  a3f1c9  ").value == "a3f1c9"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="must be non-empty"):
            AlbumId("   ")

    def test_rejects_non_hex(self) -> None:
        with pytest.raises(ValueError, match="lowercase hex"):
            AlbumId("nothex")

    def test_rejects_uppercase_hex(self) -> None:
        with pytest.raises(ValueError, match="lowercase hex"):
            AlbumId("A3F1C9")

    def test_rejects_path_separator(self) -> None:
        with pytest.raises(ValueError, match="lowercase hex"):
            AlbumId("a3/c9")


class TestMint:
    def test_mint_avoids_taken_set(self) -> None:
        first = AlbumId.mint(frozenset())
        second = AlbumId.mint(frozenset({first}))
        assert second != first

    def test_mint_into_empty_set_yields_valid_id(self) -> None:
        minted = AlbumId.mint(set())
        assert AlbumId(minted.value) == minted

    def test_mint_skips_a_saturated_prefix(self) -> None:
        # A taken set that would collide forces the loop to spin to a fresh id.
        taken = {AlbumId.mint(frozenset()) for _ in range(64)}
        minted = AlbumId.mint(taken)
        assert minted not in taken


class TestValueSemantics:
    def test_equality_and_hash(self) -> None:
        assert AlbumId("a3f1c9") == AlbumId("a3f1c9")
        assert hash(AlbumId("a3f1c9")) == hash(AlbumId("a3f1c9"))

    def test_distinct_ids_differ(self) -> None:
        assert AlbumId("a3f1c9") != AlbumId("7b2e04")

    def test_not_equal_to_foreign_type(self) -> None:
        assert AlbumId("a3f1c9") != "a3f1c9"

    def test_repr_and_str(self) -> None:
        album_id = AlbumId("a3f1c9")
        assert repr(album_id) == "AlbumId('a3f1c9')"
        assert str(album_id) == "a3f1c9"
