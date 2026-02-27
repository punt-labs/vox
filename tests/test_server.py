"""Tests for server-level helpers (vibe injection, config reading)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from punt_tts.server import (
    _apply_vibe,  # pyright: ignore[reportPrivateUsage]
    _read_vibe_tags,  # pyright: ignore[reportPrivateUsage]
)


@pytest.fixture()
def _patch_config(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Return a writable config path and patch the module."""
    import punt_tts.server as srv

    config = tmp_path / "config.md"
    monkeypatch.setattr(srv, "_CONFIG_PATH", config)
    return config


class TestReadVibeTags:
    """Tests for _read_vibe_tags config parsing."""

    def test_no_config(self, tmp_path: Path, monkeypatch: Any) -> None:
        import punt_tts.server as srv

        missing = tmp_path / "missing" / "config.md"
        monkeypatch.setattr(srv, "_CONFIG_PATH", missing)
        assert _read_vibe_tags() is None

    def test_no_vibe_tags_field(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nnotify: "y"\n---\n')
        assert _read_vibe_tags() is None

    def test_quoted_tags(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[frustrated] [sighs]"\n---\n')
        assert _read_vibe_tags() == "[frustrated] [sighs]"

    def test_unquoted_single_tag(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\nvibe_tags: [whispers]\n---\n")
        assert _read_vibe_tags() == "[whispers]"

    def test_empty_tags(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: ""\n---\n')
        assert _read_vibe_tags() is None

    def test_ignores_vibe_field(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe: "some mood"\n---\n')
        assert _read_vibe_tags() is None


class TestApplyVibe:
    """Tests for _apply_vibe text injection."""

    def test_prepends_tags(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[excited]"\n---\n')
        result = _apply_vibe("Hello world")
        assert result == "[excited] Hello world"

    def test_multiple_tags(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[frustrated] [sighs]"\n---\n')
        result = _apply_vibe("Hello world")
        assert result == "[frustrated] [sighs] Hello world"

    def test_passthrough_when_no_tags(self, tmp_path: Path, monkeypatch: Any) -> None:
        import punt_tts.server as srv

        missing = tmp_path / "missing.md"
        monkeypatch.setattr(srv, "_CONFIG_PATH", missing)
        assert _apply_vibe("Hello world") == "Hello world"
