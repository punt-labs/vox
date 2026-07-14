"""Tests for punt_vox.config -- split config routing."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from punt_vox.config import (
    ALLOWED_CONFIG_KEYS,
    DURABLE_KEYS,
    EPHEMERAL_KEYS,
    ConfigStore,
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
        ConfigStore(tmp_path).write_field("voice", "charlie")
        assert (tmp_path / "vox.md").exists()
        assert not (tmp_path / "vox.local.md").exists()
        assert ConfigStore(tmp_path).read_field("voice") == "charlie"

    def test_write_field_routes_ephemeral_to_vox_local_md(self, tmp_path: Path) -> None:
        """Test 2: write 'vibe_nudge_turns' lands in vox.local.md only."""
        ConfigStore(tmp_path).write_field("vibe_nudge_turns", "3")
        assert (tmp_path / "vox.local.md").exists()
        assert not (tmp_path / "vox.md").exists()
        assert ConfigStore(tmp_path).read_field("vibe_nudge_turns") == "3"

    def test_write_field_creates_dir(self, tmp_path: Path) -> None:
        """Test 11: write to nonexistent dir creates it."""
        deep = tmp_path / "a" / "b" / "c"
        ConfigStore(deep).write_field("voice", "fin")
        assert (deep / "vox.md").exists()
        assert ConfigStore(deep).read_field("voice") == "fin"

    def test_write_field_rejects_unknown_key(self, tmp_path: Path) -> None:
        """Test 12: ValueError for unknown key."""
        with pytest.raises(ValueError, match="Unknown config key 'bogus'"):
            ConfigStore(tmp_path).write_field("bogus", "val")

    def test_write_field_rejects_newline_in_value(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must not contain newlines"):
            ConfigStore(tmp_path).write_field("voice", "fin\nevil: injection")

    def test_write_fields_rejects_newline_in_value(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must not contain newlines"):
            ConfigStore(tmp_path).write_fields({"voice": "ok", "vibe": "calm\nbad"})


class TestWriteFieldsRouting:
    """Design test 3."""

    def test_write_fields_mixed_keys_routes_correctly(self, tmp_path: Path) -> None:
        """Test 3: mixed durable + ephemeral routes correctly."""
        ConfigStore(tmp_path).write_fields({"notify": "y", "vibe_tags": "[calm]"})

        # Durable key "notify" must be in vox.md
        assert ConfigStore(tmp_path).read_field("notify") == "y"
        vox_text = (tmp_path / "vox.md").read_text()
        assert "notify" in vox_text
        assert "vibe_tags" not in vox_text

        # Ephemeral key "vibe_tags" must be in vox.local.md
        assert ConfigStore(tmp_path).read_field("vibe_tags") == "[calm]"
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
        cfg = ConfigStore(tmp_path).read()
        assert cfg.notify == "c"
        assert cfg.voice == "charlie"
        assert cfg.vibe == "happy"
        assert cfg.vibe_tags == "[joyful]"

    def test_repo_name_derived_from_config_dir(self, tmp_path: Path) -> None:
        """repo_name is the directory two levels above config_dir."""
        repo = tmp_path / "my-repo"
        config_dir = repo / ".punt-labs" / "vox"
        config_dir.mkdir(parents=True)
        cfg = ConfigStore(config_dir).read()
        assert cfg.repo_name == "my-repo"

    def test_repo_name_none_when_config_dir_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """repo_name is None for the shallow default (relative) config dir.

        The autouse ``hermetic_config`` fixture points ``DEFAULT_CONFIG_DIR`` at
        a deep tmp dir, so pin the *production* default (the relative
        ``.punt-labs/vox``) here and ``chdir`` somewhere with no real config to
        keep the read hermetic while still exercising the None path.
        """
        import punt_vox.config as config_mod

        monkeypatch.chdir(tmp_path)
        relative_default = Path(".punt-labs") / "vox"
        monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_DIR", relative_default)
        cfg = ConfigStore(None).read()
        assert cfg.repo_name is None

    def test_local_durable_keys_ignored(self, tmp_path: Path) -> None:
        """Durable keys in vox.local.md must not override vox.md."""
        _write_frontmatter(tmp_path / "vox.md", {"notify": "n", "provider": "polly"})
        _write_frontmatter(
            tmp_path / "vox.local.md",
            {"notify": "c", "provider": "elevenlabs", "vibe": "calm"},
        )
        cfg = ConfigStore(tmp_path).read()
        assert cfg.notify == "n"
        assert cfg.provider == "polly"
        assert cfg.vibe == "calm"

    def test_read_config_missing_files(self, tmp_path: Path) -> None:
        """Test 6: neither file exists, safe defaults."""
        cfg = ConfigStore(tmp_path).read()
        assert cfg.notify == "n"
        assert cfg.speak == "y"
        assert cfg.vibe_mode == "auto"
        assert cfg.voice is None
        assert cfg.provider is None
        assert cfg.model is None
        assert cfg.vibe is None
        assert cfg.vibe_tags is None
        assert cfg.vibe_nudge_turns == 0

    def test_read_config_only_durable(self, tmp_path: Path) -> None:
        """Test 7: only vox.md exists, ephemeral fields default."""
        _write_frontmatter(tmp_path / "vox.md", {"notify": "y", "voice": "matilda"})
        cfg = ConfigStore(tmp_path).read()
        assert cfg.notify == "y"
        assert cfg.voice == "matilda"
        assert cfg.vibe is None
        assert cfg.vibe_tags is None

    def test_read_config_only_ephemeral(self, tmp_path: Path) -> None:
        """Test 8: only vox.local.md exists, durable fields default."""
        _write_frontmatter(
            tmp_path / "vox.local.md",
            {"vibe": "chill", "vibe_nudge_turns": "4"},
        )
        cfg = ConfigStore(tmp_path).read()
        assert cfg.notify == "n"  # default
        assert cfg.speak == "y"  # default
        assert cfg.vibe == "chill"
        assert cfg.vibe_nudge_turns == 4


class TestReadField:
    """Design tests 9-10."""

    def test_read_field_durable_key(self, tmp_path: Path) -> None:
        """Test 9: read_field('voice') reads from vox.md."""
        _write_frontmatter(tmp_path / "vox.md", {"voice": "fin"})
        _write_frontmatter(tmp_path / "vox.local.md", {"vibe": "happy"})
        assert ConfigStore(tmp_path).read_field("voice") == "fin"

    def test_read_field_ephemeral_key(self, tmp_path: Path) -> None:
        """Test 10: read_field('vibe_nudge_turns') reads from vox.local.md."""
        _write_frontmatter(tmp_path / "vox.md", {"voice": "fin"})
        _write_frontmatter(
            tmp_path / "vox.local.md",
            {"vibe_nudge_turns": "2"},
        )
        assert ConfigStore(tmp_path).read_field("vibe_nudge_turns") == "2"


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
        """Test 15: .vox/config.md is NOT found by find_config_dir."""
        legacy = tmp_path / ".vox"
        legacy.mkdir()
        (legacy / "config.md").write_text('---\nnotify: "y"\n---\n')
        result = find_config_dir(start=tmp_path)
        if result is not None:
            assert not result.is_relative_to(tmp_path / ".vox")


# -- Existing test coverage (updated for config_dir API) -------------------


class TestReadFieldLegacy:
    """Existing read_field coverage, adapted for split config."""

    def test_returns_value_for_existing_field(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"notify": "y"})
        assert ConfigStore(tmp_path).read_field("notify") == "y"

    def test_returns_none_for_missing_field(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"speak": "y"})
        assert ConfigStore(tmp_path).read_field("voice") is None

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        assert ConfigStore(tmp_path).read_field("notify") is None

    def test_handles_unquoted_values(self, tmp_path: Path) -> None:
        vox = tmp_path / "vox.md"
        vox.parent.mkdir(parents=True, exist_ok=True)
        vox.write_text("---\nspeak: y\n---\n")
        assert ConfigStore(tmp_path).read_field("speak") == "y"

    def test_handles_quoted_values(self, tmp_path: Path) -> None:
        _write_frontmatter(
            tmp_path / "vox.local.md",
            {"vibe_tags": "[happy] [calm]"},
        )
        assert ConfigStore(tmp_path).read_field("vibe_tags") == "[happy] [calm]"

    def test_returns_none_for_empty_value(self, tmp_path: Path) -> None:
        vox_local = tmp_path / "vox.local.md"
        vox_local.parent.mkdir(parents=True, exist_ok=True)
        vox_local.write_text('---\nvibe_nudge_turns: ""\n---\n')
        assert ConfigStore(tmp_path).read_field("vibe_nudge_turns") is None


class TestReadConfigLegacy:
    """Existing read_config coverage, adapted for split config."""

    def test_defaults_when_files_missing(self, tmp_path: Path) -> None:
        result = ConfigStore(tmp_path).read()
        assert result.notify == "n"
        assert result.speak == "y"
        assert result.vibe_mode == "auto"
        assert result.voice is None
        assert result.provider is None
        assert result.model is None
        assert result.vibe is None
        assert result.vibe_tags is None
        assert result.vibe_nudge_turns == 0

    def test_reads_all_fields(self, tmp_path: Path) -> None:
        _write_frontmatter(
            tmp_path / "vox.md",
            {
                "notify": "c",
                "speak": "y",
                "voice": "charlie",
            },
        )
        _write_frontmatter(
            tmp_path / "vox.local.md",
            {
                "vibe_mode": "manual",
                "vibe_tags": "[happy] [calm]",
                "vibe_nudge_turns": "3",
                "vibe": "happy",
            },
        )
        result = ConfigStore(tmp_path).read()
        assert result.notify == "c"
        assert result.speak == "y"
        assert result.vibe_mode == "manual"
        assert result.voice == "charlie"
        assert result.vibe == "happy"
        assert result.vibe_tags == "[happy] [calm]"
        assert result.vibe_nudge_turns == 3

    def test_invalid_notify_defaults_to_n(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"notify": "invalid"})
        assert ConfigStore(tmp_path).read().notify == "n"

    def test_invalid_speak_defaults_to_y(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"speak": "invalid"})
        assert ConfigStore(tmp_path).read().speak == "y"

    def test_invalid_vibe_mode_defaults_to_auto(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.local.md", {"vibe_mode": "invalid"})
        assert ConfigStore(tmp_path).read().vibe_mode == "auto"

    def test_invalid_vibe_mode_warns_naming_file_and_value(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A present-but-invalid vibe_mode warns before failing open to auto.

        Silent defaulting used to mask a user's deliberate off/manual by
        promoting garbage to the nudge-injecting mode.
        """
        local = tmp_path / "vox.local.md"
        _write_frontmatter(local, {"vibe_mode": "loud"})
        with caplog.at_level(logging.WARNING, logger="punt_vox.config"):
            assert ConfigStore(tmp_path).read().vibe_mode == "auto"
        messages = [r.getMessage() for r in caplog.records]
        assert any("loud" in m and str(local) in m for m in messages)

    def test_absent_vibe_mode_defaults_silently(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An absent vibe_mode is the legitimate default -- no warning."""
        _write_frontmatter(tmp_path / "vox.local.md", {"vibe": "happy"})
        with caplog.at_level(logging.WARNING, logger="punt_vox.config"):
            assert ConfigStore(tmp_path).read().vibe_mode == "auto"
        assert not any("vibe_mode" in r.getMessage() for r in caplog.records)

    def test_committed_vibe_mode_in_durable_file_is_ignored(
        self, tmp_path: Path
    ) -> None:
        """A stale vibe_mode in the tracked vox.md never reaches the session.

        Regression for vox-73m5: a committed ``vibe_mode: "manual"`` used to
        resurrect after a git checkout. vibe_mode is ephemeral now, so the
        durable file's copy is filtered out on read.
        """
        _write_frontmatter(
            tmp_path / "vox.md", {"vibe_mode": "manual", "voice": "roger"}
        )
        result = ConfigStore(tmp_path).read()
        assert result.vibe_mode == "auto"  # default -- durable copy ignored
        assert result.voice == "roger"  # genuine durable pref still read

    def test_ephemeral_vibe_mode_wins_over_durable(self, tmp_path: Path) -> None:
        """vox.local.md is authoritative for vibe_mode even if vox.md has one."""
        _write_frontmatter(tmp_path / "vox.md", {"vibe_mode": "manual"})
        _write_frontmatter(tmp_path / "vox.local.md", {"vibe_mode": "off"})
        assert ConfigStore(tmp_path).read().vibe_mode == "off"

    def test_empty_nudge_turns_defaults_to_zero(self, tmp_path: Path) -> None:
        vox_local = tmp_path / "vox.local.md"
        vox_local.parent.mkdir(parents=True, exist_ok=True)
        vox_local.write_text('---\nvibe_nudge_turns: ""\n---\n')
        assert ConfigStore(tmp_path).read().vibe_nudge_turns == 0

    def test_garbage_nudge_turns_defaults_to_zero(self, tmp_path: Path) -> None:
        vox_local = tmp_path / "vox.local.md"
        vox_local.parent.mkdir(parents=True, exist_ok=True)
        vox_local.write_text('---\nvibe_nudge_turns: "not-a-number"\n---\n')
        assert ConfigStore(tmp_path).read().vibe_nudge_turns == 0

    def test_partial_config_fills_defaults(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"notify": "y", "voice": "matilda"})
        result = ConfigStore(tmp_path).read()
        assert result.notify == "y"
        assert result.voice == "matilda"
        assert result.speak == "y"
        assert result.vibe_mode == "auto"


class TestWriteFieldsValidation:
    """write_fields rejects unknown keys before any I/O."""

    def test_rejects_unknown_key_in_batch(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown config key 'bad_key'"):
            ConfigStore(tmp_path).write_fields({"notify": "y", "bad_key": "val"})
        # No files created
        assert not (tmp_path / "vox.md").exists()
        assert not (tmp_path / "vox.local.md").exists()


class TestKeySetConsistency:
    """DURABLE_KEYS and EPHEMERAL_KEYS are disjoint and cover ALLOWED."""

    def test_disjoint(self) -> None:
        assert not (DURABLE_KEYS & EPHEMERAL_KEYS)

    def test_union_is_allowed(self) -> None:
        assert DURABLE_KEYS | EPHEMERAL_KEYS == ALLOWED_CONFIG_KEYS


# -- ConfigStore class tests ----------------------------------------------


class TestConfigStoreConstruction:
    """ConfigStore construction and dir property."""

    def test_default_dir(self) -> None:
        """A dir-less ConfigStore adopts the module's current DEFAULT_CONFIG_DIR.

        Read the live attribute rather than the import-time binding so the
        invariant holds under the autouse ``hermetic_config`` redirect.
        """
        import punt_vox.config as config_mod

        store = ConfigStore()
        assert store.dir == config_mod.DEFAULT_CONFIG_DIR

    def test_custom_dir(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path)
        assert store.dir == tmp_path


class TestConfigStoreRead:
    """ConfigStore.read() and ConfigStore.read_field()."""

    def test_read_merges_both_files(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"notify": "c", "voice": "charlie"})
        _write_frontmatter(
            tmp_path / "vox.local.md",
            {"vibe": "happy", "vibe_tags": "[joyful]"},
        )
        store = ConfigStore(tmp_path)
        cfg = store.read()
        assert cfg.notify == "c"
        assert cfg.voice == "charlie"
        assert cfg.vibe == "happy"
        assert cfg.vibe_tags == "[joyful]"

    def test_read_missing_files_returns_defaults(self, tmp_path: Path) -> None:
        cfg = ConfigStore(tmp_path).read()
        assert cfg.notify == "n"
        assert cfg.speak == "y"
        assert cfg.vibe_mode == "auto"
        assert cfg.voice is None

    def test_read_field_durable(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.md", {"voice": "fin"})
        assert ConfigStore(tmp_path).read_field("voice") == "fin"

    def test_read_field_ephemeral(self, tmp_path: Path) -> None:
        _write_frontmatter(tmp_path / "vox.local.md", {"vibe": "calm"})
        assert ConfigStore(tmp_path).read_field("vibe") == "calm"

    def test_read_field_missing_returns_none(self, tmp_path: Path) -> None:
        assert ConfigStore(tmp_path).read_field("voice") is None


class TestConfigStoreWrite:
    """ConfigStore.write_field() and ConfigStore.write_fields()."""

    def test_write_field_durable(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path)
        store.write_field("voice", "charlie")
        assert (tmp_path / "vox.md").exists()
        assert not (tmp_path / "vox.local.md").exists()
        assert store.read_field("voice") == "charlie"

    def test_write_field_ephemeral(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path)
        store.write_field("vibe", "happy")
        assert (tmp_path / "vox.local.md").exists()
        assert not (tmp_path / "vox.md").exists()
        assert store.read_field("vibe") == "happy"

    def test_write_field_rejects_unknown_key(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown config key"):
            ConfigStore(tmp_path).write_field("bogus", "val")

    def test_write_field_rejects_newline(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must not contain newlines"):
            ConfigStore(tmp_path).write_field("voice", "bad\nvalue")

    def test_write_fields_mixed(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path)
        store.write_fields({"notify": "y", "vibe_tags": "[calm]"})
        assert store.read_field("notify") == "y"
        assert store.read_field("vibe_tags") == "[calm]"
        # Verify routing: durable in vox.md, ephemeral in vox.local.md
        assert "notify" in (tmp_path / "vox.md").read_text()
        assert "vibe_tags" in (tmp_path / "vox.local.md").read_text()

    def test_write_fields_rejects_unknown_key(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown config key"):
            ConfigStore(tmp_path).write_fields({"bad_key": "val"})

    def test_non_ascii_value_round_trips(self, tmp_path: Path) -> None:
        """A non-ASCII mood survives write+read via symmetric UTF-8 I/O.

        Regression guard for the config-write encoding fix: ``_write_batch``
        must encode as UTF-8 so a mood written on one platform reads back
        identically regardless of the filesystem's default encoding.
        """
        store = ConfigStore(tmp_path)
        mood = "café-résumé-你好-\U0001f60a"
        store.write_field("vibe", mood)
        assert store.read_field("vibe") == mood
        assert (tmp_path / "vox.local.md").read_text(encoding="utf-8").count(mood) == 1

    def test_non_ascii_value_survives_update_in_place(self, tmp_path: Path) -> None:
        """Rewriting an existing file preserves non-ASCII content on both keys."""
        store = ConfigStore(tmp_path)
        first = "こんにちは"  # konnichiwa
        second = "ça-va"  # ça-va
        store.write_field("vibe", first)
        store.write_field("vibe_tags", second)
        assert store.read_field("vibe") == first
        assert store.read_field("vibe_tags") == second
