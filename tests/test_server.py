"""Tests for server-level helpers (vibe injection, config reading)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from punt_tts.server import (
    _apply_vibe,  # pyright: ignore[reportPrivateUsage]
    _read_vibe,  # pyright: ignore[reportPrivateUsage]
)


@pytest.fixture()
def _patch_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:  # pyright: ignore[reportUnusedFunction]
    """Return a writable config path and patch the module."""
    import punt_tts.server as srv

    config = tmp_path / "config.md"
    monkeypatch.setattr(srv, "_CONFIG_PATH", config)
    return config


class TestReadVibe:
    """Tests for _read_vibe config parsing."""

    def test_no_config(self, tmp_path: Path, monkeypatch: Any) -> None:
        import punt_tts.server as srv

        missing = tmp_path / "missing" / "config.md"
        monkeypatch.setattr(srv, "_CONFIG_PATH", missing)
        assert _read_vibe() is None

    def test_no_vibe_field(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nnotify: "y"\n---\n')
        assert _read_vibe() is None

    def test_quoted_vibe(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe: "dramatic tone"\n---\n')
        assert _read_vibe() == "dramatic tone"

    def test_unquoted_vibe(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\nvibe: whisper\n---\n")
        assert _read_vibe() == "whisper"

    def test_empty_vibe(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe: ""\n---\n')
        assert _read_vibe() is None


class TestApplyVibe:
    """Tests for _apply_vibe text injection."""

    def test_prepends_tag(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe: "excited"\n---\n')
        result = _apply_vibe("Hello world")
        assert result == "[excited] Hello world"

    def test_passthrough_when_no_vibe(self, tmp_path: Path, monkeypatch: Any) -> None:
        import punt_tts.server as srv

        missing = tmp_path / "missing.md"
        monkeypatch.setattr(srv, "_CONFIG_PATH", missing)
        assert _apply_vibe("Hello world") == "Hello world"

    def test_does_not_deduplicate(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe: "whisper"\n---\n')
        result = _apply_vibe("[whisper] Already tagged")
        assert result == "[whisper] [whisper] Already tagged"
