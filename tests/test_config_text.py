"""Tests for raw Vox config text."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from punt_vox.config_text import ConfigEncodingError, ConfigText

if TYPE_CHECKING:
    from pathlib import Path


def test_constructing_a_decode_error_requires_its_config_path(tmp_path: Path) -> None:
    path = tmp_path / "vox.md"
    original = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    fault = ConfigEncodingError(path, original)

    assert fault.path == path
    assert str(fault) == str(original)


def test_decode_failure_keeps_the_config_path_and_original_decode_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "vox.md"
    path.write_bytes(b"\xff")

    with pytest.raises(ConfigEncodingError) as caught:
        ConfigText(path).read()

    assert caught.value.path == path
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)
    assert str(caught.value) == str(caught.value.__cause__)
