"""Tests for album tags, the tag query predicate, and the prompt fingerprint."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from punt_vox.voxd.programs.album_tags import AlbumTags, PromptFingerprint, TagQuery
from punt_vox.voxd.programs.wire import JsonObject


def _obj(data: Mapping[str, object]) -> JsonObject:
    return JsonObject.coerce(dict(data), "test")


class TestAlbumTagsSlug:
    def test_slug_joins_style_and_vibe(self) -> None:
        assert AlbumTags(style="trance", vibe="calm").slug() == "trance--calm"

    def test_slug_uses_curated_name_when_present(self) -> None:
        tags = AlbumTags(style="lofi", vibe="focused", name="focus-beats")
        assert tags.slug() == "focus-beats"

    def test_slug_of_name_with_spaces_is_filesystem_safe(self) -> None:
        tags = AlbumTags(style="lofi", vibe="calm", name="Focus Beats")
        assert tags.slug() == "focus-beats"

    def test_slug_of_name_with_slashes_is_filesystem_safe(self) -> None:
        tags = AlbumTags(style="lofi", vibe="calm", name="a/b\\c")
        assert "/" not in tags.slug()
        assert "\\" not in tags.slug()
        assert tags.slug() == "a-b-c"

    def test_slug_of_blank_component_falls_back(self) -> None:
        assert AlbumTags(style="!!!", vibe="???").slug() == "album--album"


class TestAlbumTagsWire:
    def test_round_trip_named(self) -> None:
        tags = AlbumTags(style="trance", vibe="calm", name="mix")
        assert AlbumTags.from_wire(_obj(tags.to_dict())) == tags

    def test_round_trip_unnamed_carries_null(self) -> None:
        tags = AlbumTags(style="trance", vibe="calm")
        record = tags.to_dict()
        assert record["name"] is None
        assert AlbumTags.from_wire(_obj(record)) == tags

    def test_from_wire_rejects_missing_style(self) -> None:
        with pytest.raises(ValueError, match="'style'"):
            AlbumTags.from_wire(_obj({"vibe": "calm", "name": None}))


class TestAlbumTagsMintUniqueName:
    def test_returns_desired_when_free(self) -> None:
        assert AlbumTags.mint_unique_name("focus", frozenset()) == "focus"

    def test_auto_suffixes_on_collision(self) -> None:
        assert AlbumTags.mint_unique_name("focus", {"focus"}) == "focus1"

    def test_auto_suffix_skips_taken_suffixes(self) -> None:
        taken = {"focus", "focus1", "focus2"}
        assert AlbumTags.mint_unique_name("focus", taken) == "focus3"


class TestTagQueryMatches:
    _TAGS = AlbumTags(style="trance", vibe="calm", name="mix")

    def test_empty_query_matches_everything(self) -> None:
        assert TagQuery().matches(self._TAGS) is True

    def test_style_only_matches(self) -> None:
        assert TagQuery(style="trance").matches(self._TAGS) is True
        assert TagQuery(style="lofi").matches(self._TAGS) is False

    def test_vibe_only_matches(self) -> None:
        assert TagQuery(vibe="calm").matches(self._TAGS) is True
        assert TagQuery(vibe="energetic").matches(self._TAGS) is False

    def test_both_axes_must_match(self) -> None:
        assert TagQuery(style="trance", vibe="calm").matches(self._TAGS) is True
        assert TagQuery(style="trance", vibe="energetic").matches(self._TAGS) is False

    def test_name_axis_matches(self) -> None:
        assert TagQuery(name="mix").matches(self._TAGS) is True
        assert TagQuery(name="other").matches(self._TAGS) is False

    def test_name_query_misses_unnamed_album(self) -> None:
        unnamed = AlbumTags(style="trance", vibe="calm")
        assert TagQuery(name="mix").matches(unnamed) is False


class TestPromptFingerprint:
    def test_same_prompt_set_is_stable(self) -> None:
        a = PromptFingerprint.from_prompts("base", ["one", "two"])
        b = PromptFingerprint.from_prompts("base", ["one", "two"])
        assert a == b
        assert hash(a) == hash(b)

    def test_changed_base_differs(self) -> None:
        a = PromptFingerprint.from_prompts("base", ["one"])
        b = PromptFingerprint.from_prompts("other", ["one"])
        assert a != b

    def test_changed_variation_differs(self) -> None:
        a = PromptFingerprint.from_prompts("base", ["one", "two"])
        b = PromptFingerprint.from_prompts("base", ["one", "three"])
        assert a != b

    def test_reordered_variations_differ(self) -> None:
        a = PromptFingerprint.from_prompts("base", ["one", "two"])
        b = PromptFingerprint.from_prompts("base", ["two", "one"])
        assert a != b

    def test_empty_variations_is_stable(self) -> None:
        a = PromptFingerprint.from_prompts("base", [])
        b = PromptFingerprint.from_prompts("base", [])
        assert a == b

    def test_parse_rejects_non_hex(self) -> None:
        with pytest.raises(ValueError, match="lowercase hex"):
            PromptFingerprint("nothex!")

    def test_parse_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            PromptFingerprint("  ")

    def test_round_trip_through_hex(self) -> None:
        fingerprint = PromptFingerprint.from_prompts("base", ["one"])
        assert PromptFingerprint(fingerprint.value) == fingerprint

    def test_not_equal_to_foreign_type(self) -> None:
        assert PromptFingerprint.from_prompts("base", []) != "deadbeef"

    def test_repr_and_str(self) -> None:
        fingerprint = PromptFingerprint("deadbeef")
        assert repr(fingerprint) == "PromptFingerprint('deadbeef')"
        assert str(fingerprint) == "deadbeef"
