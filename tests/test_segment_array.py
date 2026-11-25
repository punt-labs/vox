"""Tests for decoding the JSON array of a CLI ``--from`` segments file."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import typer

from punt_vox.segment_array import SegmentArray, SegmentItem

if TYPE_CHECKING:
    from pathlib import Path


class TestSegmentArray:
    def test_items_decode_each_element_in_order(self, tmp_path: Path) -> None:
        path = tmp_path / "segments.json"
        path.write_text('["one", {"text": "two"}, ""]', encoding="utf-8")
        texts = [item.text() for item in SegmentArray(path).items()]
        assert texts == ["one", "two", ""]

    def test_invalid_json_is_usage_error(self, tmp_path: Path) -> None:
        path = tmp_path / "segments.json"
        path.write_text('["one",', encoding="utf-8")
        with pytest.raises(typer.BadParameter, match="invalid JSON"):
            SegmentArray(path).items()

    def test_non_array_is_usage_error(self, tmp_path: Path) -> None:
        path = tmp_path / "segments.json"
        path.write_text('{"text": "one"}', encoding="utf-8")
        with pytest.raises(typer.BadParameter, match="must contain a JSON array"):
            SegmentArray(path).items()


class TestSegmentItem:
    @pytest.mark.parametrize(
        ("item", "expected"),
        [("plain", "plain"), ({"text": "boxed"}, "boxed"), ("😀", "😀")],
        ids=["string", "object", "astral"],
    )
    def test_text(self, item: object, expected: str) -> None:
        assert SegmentItem(0, item).text() == expected

    @pytest.mark.parametrize("item", [3, None, {"text": 3}, {"words": "x"}])
    def test_element_without_string_text_is_usage_error(self, item: object) -> None:
        with pytest.raises(typer.BadParameter, match=r"^Element 2 must be a string"):
            SegmentItem(2, item).text()

    @pytest.mark.parametrize(
        ("item", "message"),
        [
            ("\ud800", "unpaired surrogate U+D800 at character 0."),
            ({"text": "ab\udfff"}, "unpaired surrogate U+DFFF at character 2."),
        ],
        ids=["string", "object"],
    )
    def test_lone_surrogate_is_usage_error(self, item: object, message: str) -> None:
        with pytest.raises(typer.BadParameter) as raised:
            SegmentItem(4, item).text()
        assert str(raised.value) == f"Element 4 text is not valid UTF-8: {message}"
        assert isinstance(raised.value.__cause__, UnicodeEncodeError)
