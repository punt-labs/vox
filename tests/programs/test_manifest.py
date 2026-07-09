"""Tests for the album manifest value objects, the draft, and the JSON round-trip."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from punt_vox.voxd.programs import Format, Part, PartStatus
from punt_vox.voxd.programs.album_id import AlbumId
from punt_vox.voxd.programs.album_tags import AlbumTags, PromptFingerprint
from punt_vox.voxd.programs.manifest import (
    AlbumManifest,
    ManifestDraft,
    PartEntry,
)
from punt_vox.voxd.programs.wire import JsonObject

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
