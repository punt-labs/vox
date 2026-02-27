"""Tests for server-level helpers (vibe injection, config reading/writing)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from punt_tts.server import (
    _apply_vibe,  # pyright: ignore[reportPrivateUsage]
    _read_vibe_tags,  # pyright: ignore[reportPrivateUsage]
    _write_config_field,  # pyright: ignore[reportPrivateUsage]
    set_config,
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

    def test_skips_prepend_when_text_starts_with_tag(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = _apply_vibe("[calm] Already tagged")
        assert result == "[calm] Already tagged"

    def test_skips_prepend_when_text_starts_with_different_tag(
        self, _patch_config: Path
    ) -> None:
        _patch_config.write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = _apply_vibe("[excited] Different tag")
        assert result == "[excited] Different tag"

    def test_passthrough_when_no_tags(self, tmp_path: Path, monkeypatch: Any) -> None:
        import punt_tts.server as srv

        missing = tmp_path / "missing.md"
        monkeypatch.setattr(srv, "_CONFIG_PATH", missing)
        assert _apply_vibe("Hello world") == "Hello world"


class TestWriteConfigField:
    """Tests for _write_config_field in-place YAML editing."""

    def test_creates_file_when_missing(self, _patch_config: Path) -> None:
        _write_config_field("vibe_tags", "[excited]")
        assert _patch_config.exists()
        text = _patch_config.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert text.startswith("---\n")
        assert text.rstrip().endswith("---")

    def test_updates_existing_field(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[tired]"\n---\n')
        _write_config_field("vibe_tags", "[excited]")
        text = _patch_config.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert "[tired]" not in text

    def test_updates_unquoted_field(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\nvibe_tags: [whispers]\n---\n")
        _write_config_field("vibe_tags", "[excited]")
        text = _patch_config.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert "[whispers]" not in text

    def test_inserts_new_field_before_closing_fence(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nnotify: "y"\n---\n')
        _write_config_field("vibe_tags", "[excited]")
        text = _patch_config.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert 'notify: "y"' in text

    def test_preserves_other_fields(self, _patch_config: Path) -> None:
        _patch_config.write_text(
            '---\nnotify: "y"\nvibe_tags: "[tired]"\nspeak: "y"\n---\n'
        )
        _write_config_field("vibe_tags", "[excited]")
        text = _patch_config.read_text()
        assert 'notify: "y"' in text
        assert 'speak: "y"' in text
        assert 'vibe_tags: "[excited]"' in text

    def test_clears_field_with_empty_string(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[tired]"\n---\n')
        _write_config_field("vibe_tags", "")
        text = _patch_config.read_text()
        assert 'vibe_tags: ""' in text

    def test_rejects_unknown_key(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        with pytest.raises(ValueError, match="Unknown config key"):
            _write_config_field("bad_key", "value")

    def test_creates_parent_directory(self, tmp_path: Path, monkeypatch: Any) -> None:
        import punt_tts.server as srv

        nested = tmp_path / "deep" / "dir" / "config.md"
        monkeypatch.setattr(srv, "_CONFIG_PATH", nested)
        _write_config_field("notify", "y")
        assert nested.exists()
        assert 'notify: "y"' in nested.read_text()

    def test_malformed_file_warns_and_overwrites(
        self, _patch_config: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _patch_config.write_text("no frontmatter at all\n")
        import logging

        with caplog.at_level(logging.WARNING, logger="punt_tts.server"):
            _write_config_field("notify", "y")
        assert 'notify: "y"' in _patch_config.read_text()
        assert "Malformed config" in caplog.text


class TestSetConfig:
    """Tests for the set_config MCP tool."""

    def test_writes_and_returns_key_value(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nnotify: "y"\n---\n')
        result = json.loads(set_config(key="vibe_tags", value="[frustrated]"))
        assert result == {"key": "vibe_tags", "value": "[frustrated]"}
        assert 'vibe_tags: "[frustrated]"' in _patch_config.read_text()

    def test_clears_field(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[tired]"\n---\n')
        result = json.loads(set_config(key="vibe_tags", value=""))
        assert result == {"key": "vibe_tags", "value": ""}
        assert 'vibe_tags: ""' in _patch_config.read_text()

    def test_rejects_invalid_key(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        with pytest.raises(ValueError, match="Unknown config key"):
            set_config(key="invalid", value="x")

    def test_writes_vibe_mode(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(set_config(key="vibe_mode", value="auto"))
        assert result == {"key": "vibe_mode", "value": "auto"}
        assert 'vibe_mode: "auto"' in _patch_config.read_text()

    def test_writes_vibe_signals(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        set_config(key="vibe_signals", value="tests-pass@14:00")
        assert 'vibe_signals: "tests-pass@14:00"' in _patch_config.read_text()
