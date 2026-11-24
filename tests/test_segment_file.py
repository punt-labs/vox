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
            (["", "kept"], ["kept"]),
        ],
    )
    def test_segments(
        self, tmp_path: Path, raw: list[object], expected: list[str]
    ) -> None:
        path = tmp_path / "segments.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        assert SegmentFile(path).segments() == expected

    @pytest.mark.parametrize(
        "raw", [[], [""], ["", {"text": ""}]], ids=["empty", "blank", "mixed-blank"]
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
