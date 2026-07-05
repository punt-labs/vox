"""Tests for the Program manifest value objects and JSON round-trip."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from punt_vox.voxd.programs import Format, Part, PartStatus, ProgramName
from punt_vox.voxd.programs.manifest import (
    PartEntry,
    PlaylistSubject,
    ProgramManifest,
)
from punt_vox.voxd.programs.wire import JsonObject


def _obj(data: Mapping[str, object]) -> JsonObject:
    """Wrap a plain dict as a JsonObject for from_wire tests."""
    return JsonObject.coerce(dict(data), "test")


def _manifest(*entries: PartEntry, name: str = "ambient_techno") -> ProgramManifest:
    return ProgramManifest(
        name=ProgramName(name),
        fmt=Format.PLAYLIST,
        subject=PlaylistSubject(vibe="ambient", style="techno"),
        parts=entries,
    )


_READY = PartEntry(index=1, file="001.mp3", status=PartStatus.READY, duration_ms=132000)
_FAILED = PartEntry(index=2, file="002.mp3", status=PartStatus.FAILED, reason="bad")


class TestPlaylistSubject:
    def test_round_trip(self) -> None:
        subject = PlaylistSubject(vibe="calm", style="lofi")
        assert PlaylistSubject.from_wire(_obj(subject.to_dict())) == subject


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

    def test_failed_to_dict_omits_duration(self) -> None:
        record = _FAILED.to_dict()
        assert "duration_ms" not in record
        assert record["reason"] == "bad"

    def test_from_wire_round_trip(self) -> None:
        for entry in (_READY, _FAILED):
            assert PartEntry.from_wire(_obj(entry.to_dict())) == entry


class TestProgramManifest:
    def test_accessors(self) -> None:
        manifest = _manifest(_READY)
        assert manifest.name == ProgramName("ambient_techno")
        assert manifest.format is Format.PLAYLIST
        assert manifest.subject == PlaylistSubject(vibe="ambient", style="techno")

    def test_parts_sorted_by_index(self) -> None:
        manifest = _manifest(_FAILED, _READY)
        assert [entry.index for entry in manifest.parts] == [1, 2]

    def test_ready_parts_excludes_failed(self) -> None:
        manifest = _manifest(_READY, _FAILED)
        assert manifest.ready_parts() == (Part("001.mp3", 1),)

    def test_next_index_of_empty(self) -> None:
        assert _manifest().next_index() == 1

    def test_next_index_after_parts(self) -> None:
        assert _manifest(_READY, _FAILED).next_index() == 3

    def test_with_part_appends(self) -> None:
        grown = _manifest(_READY).with_part(_FAILED)
        assert len(grown.parts) == 2

    def test_json_round_trip(self) -> None:
        manifest = _manifest(_READY, _FAILED)
        assert ProgramManifest.from_json(manifest.to_json()) == manifest

    def test_json_round_trip_non_ascii(self) -> None:
        manifest = ProgramManifest(
            name=ProgramName("chill"),
            fmt=Format.PLAYLIST,
            subject=PlaylistSubject(vibe="néon 夜", style="lo-fi ♪"),
            parts=(_READY,),
        )
        restored = ProgramManifest.from_json(manifest.to_json())
        assert restored.subject.vibe == "néon 夜"
        assert restored == manifest

    def test_value_equality_and_hash(self) -> None:
        assert _manifest(_READY) == _manifest(_READY)
        assert hash(_manifest(_READY)) == hash(_manifest(_READY))
        assert _manifest(_READY) != _manifest(_READY, name="other")

    def test_not_equal_to_foreign_type(self) -> None:
        assert _manifest() != "manifest"

    def test_repr(self) -> None:
        assert "ambient_techno" in repr(_manifest(_READY))

    def test_from_json_rejects_non_object(self) -> None:
        with pytest.raises(ValueError, match="must be a JSON object"):
            ProgramManifest.from_json("[]")

    def test_from_json_rejects_missing_parts_list(self) -> None:
        bad = (
            '{"name": "x", "format": "playlist", '
            '"subject": {"vibe": "a", "style": "b"}}'
        )
        with pytest.raises(ValueError, match="missing required field 'parts'"):
            ProgramManifest.from_json(bad)
