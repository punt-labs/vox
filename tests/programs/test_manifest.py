"""Tests for the album manifest value objects, the draft, and the JSON round-trip."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from punt_vox.types_programs import Format
from punt_vox.types_programs.wire import JsonObject
from punt_vox.voxd.programs import Part, PartStatus
from punt_vox.voxd.programs.album_id import AlbumId
from punt_vox.voxd.programs.album_tags import AlbumTags, PromptFingerprint
from punt_vox.voxd.programs.manifest import (
    AlbumManifest,
    ManifestDraft,
    PartEntry,
)

_CREATED = datetime(2026, 7, 8, 2, 14, 7, tzinfo=UTC)
_FINGERPRINT = PromptFingerprint("9f2a7c31")


def _obj(data: Mapping[str, object]) -> JsonObject:
    """Wrap a plain dict as a JsonObject for from_wire tests."""
    return JsonObject.coerce(dict(data), "test")


def _manifest(*entries: PartEntry, album_id: str = "a3f1c9") -> AlbumManifest:
    return AlbumManifest(
        album_id=AlbumId(album_id),
        fmt=Format.PLAYLIST,
        tags=AlbumTags(style="trance", vibe="calm"),
        created=_CREATED,
        fingerprint=_FINGERPRINT,
        parts=entries,
    )


_READY = PartEntry(index=1, file="001.mp3", status=PartStatus.READY, duration_ms=132000)
_FAILED = PartEntry(index=2, file="002.mp3", status=PartStatus.FAILED, reason="bad")


class TestPartEntry:
    def test_ready_is_ready(self) -> None:
        assert _READY.is_ready is True
        assert _FAILED.is_ready is False

    def test_as_part(self) -> None:
        assert _READY.as_part() == Part("001.mp3", 1)

    def test_ready_to_dict_omits_reason(self) -> None:
        record = _READY.to_dict()
        assert record == {
            "index": 1,
            "file": "001.mp3",
            "status": "ready",
            "duration_ms": 132000,
        }

    def test_from_wire_round_trip(self) -> None:
        for entry in (_READY, _FAILED):
            assert PartEntry.from_wire(_obj(entry.to_dict())) == entry


class TestAlbumManifest:
    def test_accessors(self) -> None:
        manifest = _manifest(_READY)
        assert manifest.id == AlbumId("a3f1c9")
        assert manifest.format is Format.PLAYLIST
        assert manifest.tags == AlbumTags(style="trance", vibe="calm")
        assert manifest.created == _CREATED
        assert manifest.prompt_fingerprint == _FINGERPRINT

    def test_parts_sorted_by_index(self) -> None:
        manifest = _manifest(_FAILED, _READY)
        assert [entry.index for entry in manifest.parts] == [1, 2]

    def test_ready_parts_excludes_failed(self) -> None:
        manifest = _manifest(_READY, _FAILED)
        assert manifest.ready_parts() == (Part("001.mp3", 1),)

    def test_next_index_after_parts(self) -> None:
        assert _manifest(_READY, _FAILED).next_index() == 3

    def test_json_round_trip(self) -> None:
        manifest = _manifest(_READY, _FAILED)
        assert AlbumManifest.from_json(manifest.to_json()) == manifest

    def test_created_round_trips_tz_aware(self) -> None:
        # The timestamp stays tz-aware so newest() can order by it without raising.
        restored = AlbumManifest.from_json(_manifest(_READY).to_json())
        assert restored.created == _CREATED
        assert restored.created.tzinfo is not None

    def test_json_round_trip_non_ascii(self) -> None:
        manifest = AlbumManifest(
            album_id=AlbumId("1f9a30"),
            fmt=Format.PLAYLIST,
            tags=AlbumTags(style="lo-fi ♪", vibe="néon 夜"),
            created=_CREATED,
            fingerprint=_FINGERPRINT,
            parts=(_READY,),
        )
        restored = AlbumManifest.from_json(manifest.to_json())
        assert restored.tags.vibe == "néon 夜"
        assert restored == manifest

    def test_value_equality_and_hash(self) -> None:
        assert _manifest(_READY) == _manifest(_READY)
        assert hash(_manifest(_READY)) == hash(_manifest(_READY))
        assert _manifest(_READY) != _manifest(_READY, album_id="7b2e04")

    def test_repr_names_the_id(self) -> None:
        assert "a3f1c9" in repr(_manifest(_READY))

    def test_from_json_rejects_non_object(self) -> None:
        with pytest.raises(ValueError, match="must be a JSON object"):
            AlbumManifest.from_json("[]")

    def test_from_json_raises_on_idless_record(self) -> None:
        # A total from_json (PY-EH-8): an idless legacy record raises rather than
        # returning a half-built manifest -- scan() skips these before from_json.
        legacy = '{"format": "playlist", "tags": {}, "parts": []}'
        with pytest.raises(ValueError, match="missing required field 'id'"):
            AlbumManifest.from_json(legacy)


class TestManifestDraft:
    def _draft(self) -> ManifestDraft:
        return ManifestDraft(
            album_id=AlbumId("a3f1c9"),
            tags=AlbumTags(style="trance", vibe="calm"),
            fingerprint=_FINGERPRINT,
        )

    def test_locator_composes_slug_and_id(self) -> None:
        assert self._draft().locator == "trance--calm-a3f1c9"

    def test_stamped_carries_the_stamp(self) -> None:
        # The store is the sole clock owner: it calls stamped(now(UTC)); the draft
        # stays a pure value object with no wall-clock read of its own.
        manifest = self._draft().stamped(_CREATED)
        assert manifest.created == _CREATED
        assert manifest.id == AlbumId("a3f1c9")


_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-\d{8}-\d{4}$")
_STAMP = datetime(2026, 7, 15, 3, 56, 12, tzinfo=UTC)
_PATHOLOGICAL_MOOD = (
    "a long brutal grind that shipped: 6 review rounds — 4.12.0 out the "
    "door — and this very nudge just proved itself firing live"
)


def _stamped_tags_name(*, style: str, vibe: str, name: str | None) -> str | None:
    draft = ManifestDraft(
        album_id=AlbumId("a3f1c9"),
        tags=AlbumTags(style=style, vibe=vibe, name=name),
        fingerprint=_FINGERPRINT,
    )
    return draft.stamped(_STAMP).tags.name


class TestAutoName:
    """A minted pool always gets a non-null, slug-safe, timestamped name."""

    def test_unnamed_pool_gets_a_non_null_name(self) -> None:
        name = _stamped_tags_name(style="trance", vibe="calm", name=None)
        assert name is not None

    def test_auto_name_matches_the_documented_shape(self) -> None:
        name = _stamped_tags_name(style="trance", vibe="calm", name=None)
        assert name is not None
        assert _NAME_PATTERN.match(name)
        assert name.endswith("-20260715-0356")
        assert name.startswith("calm-trance-")

    def test_pathological_mood_yields_a_valid_bounded_name(self) -> None:
        name = _stamped_tags_name(style="trance", vibe=_PATHOLOGICAL_MOOD, name=None)
        assert name is not None
        assert _NAME_PATTERN.match(name)
        assert len(name) <= 80
        assert name.endswith("-trance-20260715-0356")

    def test_empty_mood_drops_the_vibe_segment(self) -> None:
        name = _stamped_tags_name(style="trance", vibe="", name=None)
        assert name == "trance-20260715-0356"

    def test_vibe_equal_to_style_is_not_duplicated(self) -> None:
        name = _stamped_tags_name(style="trance", vibe="trance", name=None)
        assert name == "trance-20260715-0356"

    def test_curated_name_is_preserved(self) -> None:
        name = _stamped_tags_name(style="trance", vibe="calm", name="late-night-flow")
        assert name == "late-night-flow"

    def test_auto_name_dedupes_against_taken_names(self) -> None:
        # Two unnamed pools, same (style, vibe), same clock-minute, different
        # fingerprint (a resume miss) must NOT collide on the auto-name: by_name
        # is documented "0 or 1", so a duplicate would strand the second pool.
        tags = AlbumTags(style="trance", vibe="calm")
        first = ManifestDraft(
            album_id=AlbumId("a3f1c9"), tags=tags, fingerprint=_FINGERPRINT
        ).stamped(_STAMP)
        assert first.tags.name == "calm-trance-20260715-0356"
        second = ManifestDraft(
            album_id=AlbumId("b7e204"),
            tags=tags,
            fingerprint=PromptFingerprint("1122334455"),
            taken_names=frozenset({first.tags.name}),
        ).stamped(_STAMP)
        assert second.tags.name != first.tags.name
        assert second.tags.name == "calm-trance-20260715-03561"
