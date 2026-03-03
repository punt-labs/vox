"""Tests for punt_vox.config — centralized .vox/config.md reader."""

from __future__ import annotations

from pathlib import Path

from punt_vox.config import VoxConfig, read_config, read_field


class TestReadField:
    def test_returns_value_for_existing_field(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.md"
        cfg.write_text('---\nnotify: "y"\n---\n')
        assert read_field("notify", cfg) == "y"

    def test_returns_none_for_missing_field(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.md"
        cfg.write_text("---\nspeak: y\n---\n")
        assert read_field("voice", cfg) is None

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "nonexistent.md"
        assert read_field("notify", cfg) is None

    def test_handles_unquoted_values(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.md"
        cfg.write_text("---\nspeak: y\n---\n")
        assert read_field("speak", cfg) == "y"

    def test_handles_quoted_values(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.md"
        cfg.write_text('---\nvibe_tags: "[happy] [calm]"\n---\n')
        assert read_field("vibe_tags", cfg) == "[happy] [calm]"

    def test_returns_none_for_empty_value(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.md"
        cfg.write_text('---\nvibe_signals: ""\n---\n')
        assert read_field("vibe_signals", cfg) is None


class TestReadConfig:
    def test_defaults_when_file_missing(self, tmp_path: Path) -> None:
        cfg = tmp_path / "nonexistent.md"
        result = read_config(cfg)
        assert result == VoxConfig(
            notify="n",
            speak="y",
            voice_enabled="true",
            vibe_mode="auto",
            voice=None,
            vibe=None,
            vibe_tags=None,
            vibe_signals=None,
        )

    def test_reads_all_fields(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.md"
        cfg.write_text(
            "---\n"
            'voice_enabled: "true"\n'
            'notify: "c"\n'
            'speak: "y"\n'
            'vibe_tags: "[happy] [calm]"\n'
            'vibe_signals: "tests-pass@14:00"\n'
            'vibe: "happy"\n'
            'vibe_mode: "manual"\n'
            'voice: "charlie"\n'
            "---\n"
        )
        result = read_config(cfg)
        assert result.notify == "c"
        assert result.speak == "y"
        assert result.voice_enabled == "true"
        assert result.vibe_mode == "manual"
        assert result.voice == "charlie"
        assert result.vibe == "happy"
        assert result.vibe_tags == "[happy] [calm]"
        assert result.vibe_signals == "tests-pass@14:00"

    def test_invalid_notify_defaults_to_n(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.md"
        cfg.write_text('---\nnotify: "invalid"\n---\n')
        assert read_config(cfg).notify == "n"

    def test_invalid_speak_defaults_to_y(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.md"
        cfg.write_text('---\nspeak: "invalid"\n---\n')
        assert read_config(cfg).speak == "y"

    def test_invalid_vibe_mode_defaults_to_auto(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.md"
        cfg.write_text('---\nvibe_mode: "invalid"\n---\n')
        assert read_config(cfg).vibe_mode == "auto"

    def test_empty_signals_returns_none(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.md"
        cfg.write_text('---\nvibe_signals: ""\n---\n')
        assert read_config(cfg).vibe_signals is None

    def test_partial_config_fills_defaults(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.md"
        cfg.write_text('---\nnotify: "y"\nvoice: "matilda"\n---\n')
        result = read_config(cfg)
        assert result.notify == "y"
        assert result.voice == "matilda"
        assert result.speak == "y"
        assert result.vibe_mode == "auto"
