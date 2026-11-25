"""Tests for CLI JSON segment file decoding and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from punt_vox.segment_file import SegmentFile


class TestSegmentFile:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (["héllo", {"text": "世界"}], ["héllo", "世界"]),
            (["", " \n", "kept"], ["kept"]),
        ],
    )
    def test_segments(
        self, tmp_path: Path, raw: list[object], expected: list[str]
    ) -> None:
        path = tmp_path / "segments.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        assert SegmentFile(path).segments() == expected

    @pytest.mark.parametrize(
        "raw",
        [[], [""], ["", {"text": ""}], [" ", {"text": "\n\t"}]],
        ids=["empty", "blank", "mixed-blank", "whitespace"],
    )
    def test_no_text_is_usage_error(self, tmp_path: Path, raw: list[object]) -> None:
        path = tmp_path / "segments.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(typer.BadParameter) as raised:
            SegmentFile(path).segments()
        assert str(raised.value) == "--from file has no text to speak."

    @pytest.mark.parametrize(
        "error",
        [OSError("read failed"), FileNotFoundError(2, "No such file or directory")],
    )
    def test_read_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        error: OSError,
    ) -> None:
        def fail_read(*args: object, **kwargs: object) -> str:
            raise error

        monkeypatch.setattr(Path, "read_text", fail_read)
        with pytest.raises(typer.BadParameter) as raised:
            SegmentFile(tmp_path / "segments.json").segments()
        assert (
            str(raised.value)
            == f"Cannot read --from file: {error.strerror or str(error)}."
        )
        assert raised.value.__cause__ is error

    @pytest.mark.parametrize(
        ("raw", "index"),
        [
            ('["before", "\\ud800", "after"]', 1),
            ('[{"text": "ok"}, {"text": "x\\udfff"}]', 1),
            ('["\\udc80 lone low"]', 0),
        ],
        ids=["string", "object", "first"],
    )
    def test_unencodable_text_is_usage_error(
        self, tmp_path: Path, raw: str, index: int
    ) -> None:
        path = tmp_path / "segments.json"
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(typer.BadParameter) as raised:
            SegmentFile(path).segments()
        assert str(raised.value).startswith(
            f"Element {index} text is not valid UTF-8: unpaired surrogate"
        )
        assert isinstance(raised.value.__cause__, UnicodeEncodeError)

    def test_paired_surrogate_escape_is_text(self, tmp_path: Path) -> None:
        path = tmp_path / "segments.json"
        path.write_text('["\\ud83d\\ude00 smile"]', encoding="utf-8")
        assert SegmentFile(path).segments() == ["\N{GRINNING FACE} smile"]
