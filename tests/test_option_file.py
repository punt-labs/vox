"""Tests for reading a CLI option's UTF-8 file as usage errors."""

from __future__ import annotations

import errno
import os
from typing import TYPE_CHECKING

import pytest
import typer

from punt_vox.option_file import OptionFile

if TYPE_CHECKING:
    from pathlib import Path


class TestOptionFile:
    def test_text(self, tmp_path: Path) -> None:
        path = tmp_path / "script.jsonl"
        path.write_text("héllo 世界\n", encoding="utf-8")
        assert OptionFile(path, "--script").text() == "héllo 世界\n"

    def test_missing_file_names_the_option(self, tmp_path: Path) -> None:
        with pytest.raises(typer.BadParameter) as caught:
            OptionFile(tmp_path / "absent.jsonl", "--script").text()
        reason = os.strerror(errno.ENOENT)
        assert str(caught.value) == f"Cannot read --script file: {reason}."
        assert isinstance(caught.value.__cause__, FileNotFoundError)

    def test_non_utf8_file_says_to_save_as_utf8(self, tmp_path: Path) -> None:
        path = tmp_path / "segments.json"
        path.write_bytes(b'["\xff"]')
        with pytest.raises(typer.BadParameter) as caught:
            OptionFile(path, "--from").text()
        assert str(caught.value) == (
            "--from file is not UTF-8 (byte 2); save it as UTF-8."
        )
        assert isinstance(caught.value.__cause__, UnicodeDecodeError)
