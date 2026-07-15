"""Tests for album tags, the tag query predicate, and the prompt fingerprint."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from punt_vox.types_programs.wire import JsonObject
from punt_vox.voxd.programs.album_tags import AlbumTags, PromptFingerprint, TagQuery


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


_PATHOLOGICAL_MOOD = (
    "a long brutal grind that shipped: 6 review rounds — 4.12.0 out the "
    "door — and this very nudge just proved itself firing live"
)


class TestVibeNormalization:
    """The persisted vibe tag is a short, bounded, tag-safe label -- never prose."""

    def test_long_mood_is_bounded_and_sanitized(self) -> None:
        tags = AlbumTags(style="trance", vibe=_PATHOLOGICAL_MOOD)
        assert tags.vibe != _PATHOLOGICAL_MOOD
        assert len(tags.vibe) <= 48
        assert ":" not in tags.vibe
        assert "\n" not in tags.vibe

    def test_control_characters_are_stripped(self) -> None:
        tags = AlbumTags(style="trance", vibe="line one\nline two\ttabbed")
        assert "\n" not in tags.vibe
        assert "\t" not in tags.vibe

    def test_interior_whitespace_collapses(self) -> None:
        tags = AlbumTags(style="trance", vibe="late   night     flow")
        assert tags.vibe == "late night flow"

    def test_already_short_mood_is_preserved(self) -> None:
        assert AlbumTags(style="trance", vibe="calm").vibe == "calm"

    def test_unicode_mood_is_preserved(self) -> None:
        assert AlbumTags(style="lo-fi", vibe="néon 夜").vibe == "néon 夜"

    def test_punctuation_only_mood_normalizes_to_empty(self) -> None:
        # Empty is a valid vibe; junk punctuation is not (keep empty over prose).
        assert AlbumTags(style="trance", vibe="???:::!!!").vibe == ""

    def test_empty_mood_stays_empty(self) -> None:
        assert AlbumTags(style="trance", vibe="").vibe == ""

    def test_normalization_is_idempotent(self) -> None:
        once = AlbumTags(style="trance", vibe=_PATHOLOGICAL_MOOD).vibe
        twice = AlbumTags(style="trance", vibe=once).vibe
        assert once == twice

    def test_query_vibe_matches_the_stored_bounded_vibe(self) -> None:
        # The resume path filters on the raw mood; it must still match the pool's
        # bounded vibe, or every session mints a fresh album instead of resuming.
        stored = AlbumTags(style="trance", vibe=_PATHOLOGICAL_MOOD)
        query = TagQuery(style="trance", vibe=_PATHOLOGICAL_MOOD)
        assert query.matches(stored) is True


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


class TestTagQueryNormalized:
    _TAGS = AlbumTags(style="trance", vibe="calm", name="mix")

    def test_trims_present_tags_to_the_stored_form(self) -> None:
        query = TagQuery.normalized(style="  trance ", vibe=" calm", name="mix ")
        assert query == TagQuery(style="trance", vibe="calm", name="mix")
        assert query.matches(self._TAGS) is True

    def test_absent_tags_stay_wildcards(self) -> None:
        assert TagQuery.normalized(style=None, vibe=None, name=None) == TagQuery()

    def test_blank_tag_collapses_to_a_wildcard(self) -> None:
        # A whitespace-only tag is absence, never an impossible empty-string filter.
        query = TagQuery.normalized(style="   ", vibe="calm", name=None)
        assert query.style is None
        assert query.matches(self._TAGS) is True

    def test_canonical_agrees_with_the_query_normalization(self) -> None:
        # Both the write path (AlbumTags.canonical) and the read path share one rule.
        assert AlbumTags.canonical("  trance ") == "trance"


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
