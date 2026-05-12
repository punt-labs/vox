"""Tests for punt_vox.config -- split config routing."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.config import (
    ALLOWED_CONFIG_KEYS,
    DURABLE_KEYS,
    EPHEMERAL_KEYS,
    VoxConfig,
    read_config,
    read_field,
    write_field,
    write_fields,
)
from punt_vox.dirs import find_config_dir

# -- Helpers ---------------------------------------------------------------


def _write_frontmatter(path: Path, fields: dict[str, str]) -> None:
    """Write a minimal YAML frontmatter file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'{k}: "{v}"' for k, v in fields.items()]
    path.write_text("---\n" + "\n".join(lines) + "\n---\n")


# -- Design tests 1-12: config.py -----------------------------------------


class TestWriteFieldRouting:
    """Design tests 1, 2, 11, 12."""

    def test_write_field_routes_durable_to_vox_md(self, tmp_path: Path) -> None:
        """Test 1: write 'voice' lands in vox.md only."""
        write_field("voice", "charlie", config_dir=tmp_path)
        assert (tmp_path / "vox.md").exists()
        assert not (tmp_path / "vox.local.md").exists()
        assert read_field("voice", config_dir=tmp_path) == "charlie"

    def test_write_field_routes_ephemeral_to_vox_local_md(self, tmp_path: Path) -> None:
        """Test 2: write 'vibe_signals' lands in vox.local.md only."""
        write_field("vibe_signals", "tests-pass@14:00", config_dir=tmp_path)
        assert (tmp_path / "vox.local.md").exists()
        assert not (tmp_path / "vox.md").exists()
        assert read_field("vibe_signals", config_dir=tmp_path) == "tests-pass@14:00"

    def test_write_field_creates_dir(self, tmp_path: Path) -> None:
        """Test 11: write to nonexistent dir creates it."""
        deep = tmp_path / "a" / "b" / "c"
        write_field("voice", "fin", config_dir=deep)
        assert (deep / "vox.md").exists()
        assert read_field("voice", config_dir=deep) == "fin"

    def test_write_field_rejects_unknown_key(self, tmp_path: Path) -> None:
        """Test 12: ValueError for unknown key."""
        with pytest.raises(ValueError, match="Unknown config key 'bogus'"):
            write_field("bogus", "val", config_dir=tmp_path)


class TestWriteFieldsRouting:
    """Design test 3."""

    def test_write_fields_mixed_keys_routes_correctly(self, tmp_path: Path) -> None:
        """Test 3: mixed durable + ephemeral routes correctly."""
        write_fields({"notify": "y", "vibe_tags": "[calm]"}, config_dir=tmp_path)

        # notify (durable) in vox.md
        assert read_field("notify", config_dir=tmp_path) == "y"
        vox_text = (tmp_path / "vox.md").read_text()
        assert "notify" in vox_text
        assert "vibe_tags" not in vox_text

        # vibe_tags (ephemeral) in vox.local.md
        assert read_field("vibe_tags", config_dir=tmp_path) == "[calm]"
        local_text = (tmp_path / "vox.local.md").read_text()
        assert "vibe_tags" in local_text
        assert "notify" not in local_text


class TestReadConfig:
    """Design tests 4-8."""

    def test_read_config_merges_both_files(self, tmp_path: Path) -> None:
        """Test 4: durable + ephemeral merged into single VoxConfig."""
        _write_frontmatter(tmp_path / "vox.md", {"notify": "c", "voice": "charlie"})
        _write_frontmatter(
            tmp_path / "vox.local.md",
            {"vibe": "happy", "vibe_tags": "[joyful]"},
        )
        cfg = read_config(config_dir=tmp_path)
        assert cfg.notify == "c"
        assert cfg.voice == "charlie"
        assert cfg.vibe == "happy"
        assert cfg.vibe_tags == "[joyful]"

    def test_read_config_ephemeral_wins_on_conflict(self, tmp_path: Path) -> None:
        """Test 5: same key in both files, ephemeral value wins."""
        _write_frontmatter(tmp_path / "vox.md", {"voice": "stale"})
        _write_frontmatter(tmp_path / "vox.local.md", {"voice": "fresh"})
        cfg = read_config(config_dir=tmp_path)
        assert cfg.voice == "fresh"

    def test_read_config_missing_files(self, tmp_path: Path) -> None:
        """Test 6: neither file exists, safe defaults."""
        cfg = read_config(config_dir=tmp_path)
        assert cfg == VoxConfig(
            notify="n",
            speak="y",
            vibe_mode="auto",
            voice=None,
            provider=None,
            model=None,
            vibe=None,
            vibe_tags=None,
            vibe_signals=None,
        )

    def test_read_config_only_durable(self, tmp_path: Path) -> None:
        """Test 7: only vox.md exists, ephemeral fields default."""
        _write_frontmatter(tmp_path / "vox.md", {"notify": "y", "voice": "matilda"})
        cfg = read_config(config_dir=tmp_path)
        assert cfg.notify == "y"
        assert cfg.voice == "matilda"
        assert cfg.vibe is None
        assert cfg.vibe_tags is None

    def test_read_config_only_ephemeral(self, tmp_path: Path) -> None:
        """Test 8: only vox.local.md exists, durable fields default."""
        _write_frontmatter(
            tmp_path / "vox.local.md",
            {"vibe": "chill", "vibe_signals": "test@12"},
        )
        cfg = read_config(config_dir=tmp_path)
        assert cfg.notify == "n"  # default
        assert cfg.speak == "y"  # default
        assert cfg.vibe == "chill"
        assert cfg.vibe_signals == "test@12"


class TestReadField:
    """Design tests 9-10."""

    def test_read_field_durable_key(self, tmp_path: Path) -> None:
        """Test 9: read_field('voice') reads from vox.md."""
        _write_frontmatter(tmp_path / "vox.md", {"voice": "fin"})
        _write_frontmatter(tmp_path / "vox.local.md", {"vibe": "happy"})
        assert read_field("voice", config_dir=tmp_path) == "fin"

    def test_read_field_ephemeral_key(self, tmp_path: Path) -> None:
        """Test 10: read_field('vibe_signals') reads from vox.local.md."""
        _write_frontmatter(tmp_path / "vox.md", {"voice": "fin"})
        _write_frontmatter(
            tmp_path / "vox.local.md",
            {"vibe_signals": "deploy@15:30"},
        )
        assert read_field("vibe_signals", config_dir=tmp_path) == "deploy@15:30"


# -- Design tests 13-15: dirs.py ------------------------------------------


class TestFindConfigDir:
    """Design tests 13-15."""

    def test_find_config_dir_walks_up(self, tmp_path: Path) -> None:
        """Test 13: finds .punt-labs/vox/ from a child directory."""
        config_d = tmp_path / ".punt-labs" / "vox"
        _write_frontmatter(config_d / "vox.md", {"notify": "y"})
        child = tmp_path / "a" / "b" / "c"
        child.mkdir(parents=True)
        result = find_config_dir(start=child)
        assert result == config_d

    def test_find_config_dir_finds_ephemeral_only(self, tmp_path: Path) -> None:
        """Test 14: directory with only vox.local.md is found."""
        config_d = tmp_path / ".punt-labs" / "vox"
        _write_frontmatter(config_d / "vox.local.md", {"vibe": "happy"})
        result = find_config_dir(start=tmp_path)
        assert result == config_d

    def test_find_config_dir_no_legacy(self, tmp_path: Path) -> None:
        """Test 15: .vox/config.md is NOT found."""
        legacy = tmp_path / ".vox"
        legacy.mkdir()
        (legacy / "config.md").write_text('---\nnotify: "y"\n---\n')
        result = find_config_dir(start=tmp_path)
        assert result is None


# -- Existing test coverage (updated for config_dir API) -------------------


class TestReadFieldLegacy:
    """Existing read_field coverage, adapted for split config."""

    def test_returns_value_for_existing_field(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"notify": "y"})
        assert read_field("notify", config_dir=tmp_path) == "y"

    def test_returns_none_for_missing_field(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"speak": "y"})
        assert read_field("voice", config_dir=tmp_path) is None

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        assert read_field("notify", config_dir=tmp_path) is None

    def test_handles_unquoted_values(self, tmp_path: Path) -> None:
        vox = tmp_path / "vox.md"
        vox.parent.mkdir(parents=True, exist_ok=True)
        vox.write_text("---\nspeak: y\n---\n")
        assert read_field("speak", config_dir=tmp_path) == "y"

    def test_handles_quoted_values(self, tmp_path: Path) -> None:
        _write_frontmatter(
            tmp_path / "vox.local.md",
            {"vibe_tags": "[happy] [calm]"},
        )
        assert read_field("vibe_tags", config_dir=tmp_path) == "[happy] [calm]"

    def test_returns_none_for_empty_value(self, tmp_path: Path) -> None:
        vox_local = tmp_path / "vox.local.md"
        vox_local.parent.mkdir(parents=True, exist_ok=True)
        vox_local.write_text('---\nvibe_signals: ""\n---\n')
        assert read_field("vibe_signals", config_dir=tmp_path) is None


class TestReadConfigLegacy:
    """Existing read_config coverage, adapted for split config."""

    def test_defaults_when_files_missing(self, tmp_path: Path) -> None:
        result = read_config(config_dir=tmp_path)
        assert result == VoxConfig(
            notify="n",
            speak="y",
            vibe_mode="auto",
            voice=None,
            provider=None,
            model=None,
            vibe=None,
            vibe_tags=None,
            vibe_signals=None,
        )

    def test_reads_all_fields(self, tmp_path: Path) -> None:
        _write_frontmatter(
            tmp_path / "vox.md",
            {
                "notify": "c",
                "speak": "y",
                "vibe_mode": "manual",
                "voice": "charlie",
            },
        )
        _write_frontmatter(
            tmp_path / "vox.local.md",
            {
                "vibe_tags": "[happy] [calm]",
                "vibe_signals": "tests-pass@14:00",
                "vibe": "happy",
            },
        )
        result = read_config(config_dir=tmp_path)
        assert result.notify == "c"
        assert result.speak == "y"
        assert result.vibe_mode == "manual"
        assert result.voice == "charlie"
        assert result.vibe == "happy"
        assert result.vibe_tags == "[happy] [calm]"
        assert result.vibe_signals == "tests-pass@14:00"

    def test_invalid_notify_defaults_to_n(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"notify": "invalid"})
        assert read_config(config_dir=tmp_path).notify == "n"

    def test_invalid_speak_defaults_to_y(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"speak": "invalid"})
        assert read_config(config_dir=tmp_path).speak == "y"

    def test_invalid_vibe_mode_defaults_to_auto(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"vibe_mode": "invalid"})
        assert read_config(config_dir=tmp_path).vibe_mode == "auto"

    def test_empty_signals_returns_none(self, tmp_path: Path) -> None:
        vox_local = tmp_path / "vox.local.md"
        vox_local.parent.mkdir(parents=True, exist_ok=True)
        vox_local.write_text('---\nvibe_signals: ""\n---\n')
        assert read_config(config_dir=tmp_path).vibe_signals is None

    def test_partial_config_fills_defaults(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"notify": "y", "voice": "matilda"})
        result = read_config(config_dir=tmp_path)
        assert result.notify == "y"
        assert result.voice == "matilda"
        assert result.speak == "y"
        assert result.vibe_mode == "auto"


class TestWriteFieldsValidation:
    """write_fields rejects unknown keys before any I/O."""

    def test_rejects_unknown_key_in_batch(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown config key 'bad_key'"):
            write_fields({"notify": "y", "bad_key": "val"}, config_dir=tmp_path)
        # No files created
        assert not (tmp_path / "vox.md").exists()
        assert not (tmp_path / "vox.local.md").exists()


class TestKeySetConsistency:
    """DURABLE_KEYS and EPHEMERAL_KEYS are disjoint and cover ALLOWED."""

    def test_disjoint(self) -> None:
        assert not (DURABLE_KEYS & EPHEMERAL_KEYS)

    def test_union_is_allowed(self) -> None:
        assert DURABLE_KEYS | EPHEMERAL_KEYS == ALLOWED_CONFIG_KEYS
