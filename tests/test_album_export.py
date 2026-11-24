"""Tests for the ``music get`` export value objects (album_export).

``ExportManifest`` parses the daemon's ``music_manifest`` reply, validating every
name before a client joins it onto a destination; ``AlbumExport`` is the result
both surfaces report, naming the ready parts that were deleted out of band.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.album_export import AlbumExport, ExportManifest


def _reply(parts: list[str], missing: list[str] | None = None) -> dict[str, object]:
    reply: dict[str, object] = {
        "type": "manifest",
        "album": "pads-a3f1c9",
        "parts": [{"part": name, "bytes": 5} for name in parts],
    }
    if missing is not None:
        reply["missing"] = [{"part": name} for name in missing]
    return reply


class TestExportManifest:
    """``ExportManifest.parse`` and ``require_parts``."""

    def test_parses_parts_and_missing(self) -> None:
        manifest = ExportManifest.parse(_reply(["001.mp3"], ["002.mp3"]))

        assert manifest == ExportManifest("pads-a3f1c9", ("001.mp3",), ("002.mp3",))

    def test_rejects_a_reply_without_missing(self) -> None:
        with pytest.raises(ValueError, match="missing"):
            ExportManifest.parse(_reply(["001.mp3"]))

    @pytest.mark.parametrize(
        ("parts", "missing"),
        [(["../evil.mp3"], []), (["001.mp3"], ["/etc/passwd"])],
        ids=["traversing-part", "absolute-missing"],
    )
    def test_rejects_a_name_that_is_not_bare(
        self, parts: list[str], missing: list[str]
    ) -> None:
        with pytest.raises(ValueError, match="part name"):
            ExportManifest.parse(_reply(parts, missing))

    def test_require_parts_accepts_a_partial_album(self) -> None:
        ExportManifest("pads-a3f1c9", ("001.mp3",), ("002.mp3",)).require_parts(
            "a3f1c9"
        )

    def test_require_parts_names_every_missing_part(self) -> None:
        manifest = ExportManifest("pads-a3f1c9", (), ("001.mp3", "002.mp3"))

        with pytest.raises(
            ValueError, match=r"every ready part is missing on disk: 001\.mp3, 002\.mp3"
        ):
            manifest.require_parts("a3f1c9")

    def test_require_parts_reports_a_still_generating_album(self) -> None:
        with pytest.raises(ValueError, match="still generating"):
            ExportManifest("pads-a3f1c9", (), ()).require_parts("a3f1c9")


class TestAlbumExport:
    """``AlbumExport.payload`` and ``summary``."""

    def test_a_complete_export_keeps_the_locator_shape(self) -> None:
        export = AlbumExport(Path("/out/pads-a3f1c9"))

        assert export.payload("a3f1c9") == {
            "album_id": "a3f1c9",
            "path": "/out/pads-a3f1c9",
        }
        assert export.summary() == "/out/pads-a3f1c9"

    def test_a_partial_export_names_the_skipped_parts(self) -> None:
        export = AlbumExport(Path("/out/pads-a3f1c9"), ("002.mp3", "004.mp3"))

        assert export.payload("a3f1c9")["missing"] == ["002.mp3", "004.mp3"]
        assert export.summary() == (
            "/out/pads-a3f1c9\nskipped (missing on disk): 002.mp3, 004.mp3"
        )
