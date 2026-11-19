"""Tests for punt_vox.api_key_file."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import typer

from punt_vox.api_key_file import ApiKeyFile

if TYPE_CHECKING:
    from pathlib import Path


class TestApiKeyFile:
    def test_read_strips_whitespace(self, tmp_path: Path) -> None:
        """A 0600 key file yields the key without its trailing newline."""
        key_path = tmp_path / "key.txt"
        key_path.write_text("sk_test_key\n", encoding="utf-8")
        key_path.chmod(0o600)
        assert ApiKeyFile(key_path).read() == "sk_test_key"

    def test_read_missing(self, tmp_path: Path) -> None:
        """Missing file raises BadParameter."""
        with pytest.raises(typer.BadParameter, match="is not a file"):
            ApiKeyFile(tmp_path / "nope.txt").read()

    def test_read_empty(self, tmp_path: Path) -> None:
        """Empty file raises BadParameter."""
        empty = tmp_path / "empty.txt"
        empty.write_text("", encoding="utf-8")
        empty.chmod(0o600)
        with pytest.raises(typer.BadParameter, match="is empty"):
            ApiKeyFile(empty).read()

    def test_read_not_utf8(self, tmp_path: Path) -> None:
        """Non-UTF-8 bytes raise BadParameter without echoing the key bytes."""
        binary = tmp_path / "key.bin"
        binary.write_bytes(b"\xffsk_secret\n")
        binary.chmod(0o600)
        with pytest.raises(typer.BadParameter, match="is not valid UTF-8") as info:
            ApiKeyFile(binary).read()
        assert "0xff" not in str(info.value)
        assert isinstance(info.value.__cause__, UnicodeDecodeError)

    def test_read_warns_on_shared_mode(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Group/other permission bits warn on stderr but still return the key."""
        key_path = tmp_path / "key.txt"
        key_path.write_text("sk_test_key\n", encoding="utf-8")
        key_path.chmod(0o644)
        assert ApiKeyFile(key_path).read() == "sk_test_key"
        assert "chmod 600" in capsys.readouterr().err
