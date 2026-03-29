"""Tests for punt_vox.keys — provider key management."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from punt_vox.keys import (
    format_keys_env,
    load_keys_env,
    parse_keys_env,
    write_keys_env,
)

if TYPE_CHECKING:
    import pytest


# ---------------------------------------------------------------------------
# parse_keys_env
# ---------------------------------------------------------------------------


class TestParseKeysEnv:
    def test_basic(self) -> None:
        text = "FOO=bar\nBAZ=qux"
        assert parse_keys_env(text) == {"FOO": "bar", "BAZ": "qux"}

    def test_comments_skipped(self) -> None:
        text = "# comment\nFOO=bar\n# another comment"
        assert parse_keys_env(text) == {"FOO": "bar"}

    def test_blank_lines_skipped(self) -> None:
        text = "\n\nFOO=bar\n\n"
        assert parse_keys_env(text) == {"FOO": "bar"}

    def test_value_with_equals(self) -> None:
        text = "KEY=val=ue=with=equals"
        assert parse_keys_env(text) == {"KEY": "val=ue=with=equals"}

    def test_malformed_no_equals(self) -> None:
        text = "NOEQUALS\nFOO=bar"
        assert parse_keys_env(text) == {"FOO": "bar"}

    def test_whitespace_stripped(self) -> None:
        text = "  FOO  =  bar  "
        assert parse_keys_env(text) == {"FOO": "bar"}

    def test_empty_key_skipped(self) -> None:
        text = "=value"
        assert parse_keys_env(text) == {}

    def test_empty_string(self) -> None:
        assert parse_keys_env("") == {}


# ---------------------------------------------------------------------------
# format_keys_env
# ---------------------------------------------------------------------------


class TestFormatKeysEnv:
    def test_sorted_output(self) -> None:
        result = format_keys_env({"ZZZ": "last", "AAA": "first"})
        lines = result.strip().splitlines()
        # Skip header comment lines
        data_lines = [line for line in lines if not line.startswith("#") and line]
        assert data_lines == ["AAA=first", "ZZZ=last"]

    def test_header_present(self) -> None:
        result = format_keys_env({"FOO": "bar"})
        assert result.startswith("# vox provider keys")

    def test_trailing_newline(self) -> None:
        result = format_keys_env({"FOO": "bar"})
        assert result.endswith("\n")

    def test_empty_values_omitted(self) -> None:
        result = format_keys_env({"FOO": "bar", "EMPTY": ""})
        assert "EMPTY" not in result
        assert "FOO=bar" in result


# ---------------------------------------------------------------------------
# write_keys_env
# ---------------------------------------------------------------------------


class TestWriteKeysEnv:
    def test_creates_file_with_0600(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)

        env = {
            "ELEVENLABS_API_KEY": "sk-test",
            "UNRELATED": "ignored",
        }
        path = write_keys_env(env)

        assert path == keys_file
        assert path.exists()
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600
        content = path.read_text()
        assert "ELEVENLABS_API_KEY=sk-test" in content
        # Non-provider keys are NOT written
        assert "UNRELATED" not in content

    def test_omits_empty_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)

        env = {
            "ELEVENLABS_API_KEY": "",
            "OPENAI_API_KEY": "sk-openai",
        }
        write_keys_env(env)

        content = keys_file.read_text()
        assert "ELEVENLABS_API_KEY" not in content
        assert "OPENAI_API_KEY=sk-openai" in content

    def test_idempotent_preserves_non_provider_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)

        # Write initial content with a non-provider key
        keys_file.write_text("CUSTOM_KEY=custom_value\nELEVENLABS_API_KEY=old\n")

        env = {"ELEVENLABS_API_KEY": "new"}
        write_keys_env(env)

        content = keys_file.read_text()
        assert "CUSTOM_KEY=custom_value" in content
        assert "ELEVENLABS_API_KEY=new" in content
        assert "ELEVENLABS_API_KEY=old" not in content

    def test_overlay_updates_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)

        env1 = {
            "ELEVENLABS_API_KEY": "first",
            "OPENAI_API_KEY": "openai1",
        }
        write_keys_env(env1)

        env2 = {"ELEVENLABS_API_KEY": "second"}
        write_keys_env(env2)

        parsed = parse_keys_env(keys_file.read_text())
        assert parsed["ELEVENLABS_API_KEY"] == "second"
        # OPENAI_API_KEY was not in env2, so it should be preserved
        assert parsed["OPENAI_API_KEY"] == "openai1"

    def test_absent_env_key_preserves_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)

        env1 = {
            "ELEVENLABS_API_KEY": "keep-me",
            "OPENAI_API_KEY": "also-keep",
        }
        write_keys_env(env1)

        # env2 has neither key — both should be preserved
        env2: dict[str, str] = {}
        write_keys_env(env2)

        parsed = parse_keys_env(keys_file.read_text())
        assert parsed["ELEVENLABS_API_KEY"] == "keep-me"
        assert parsed["OPENAI_API_KEY"] == "also-keep"

    def test_empty_string_removes_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)

        env1 = {"ELEVENLABS_API_KEY": "to-remove"}
        write_keys_env(env1)

        # Empty string means "remove this key"
        env2 = {"ELEVENLABS_API_KEY": ""}
        write_keys_env(env2)

        parsed = parse_keys_env(keys_file.read_text())
        assert "ELEVENLABS_API_KEY" not in parsed

    def test_corrupted_existing_file_overwrites(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("OLD=data\n")
        keys_file.chmod(0o000)
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)

        # Should warn and overwrite despite unreadable existing file
        keys_file.chmod(0o200)  # write-only so read fails but write succeeds
        path = write_keys_env({"OPENAI_API_KEY": "new-key"})

        # Restore read permission to verify
        path.chmod(0o600)
        content = path.read_text()
        assert "OPENAI_API_KEY=new-key" in content

    def test_creates_parent_directories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "nested" / "dir" / "keys.env"
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)

        write_keys_env({"OPENAI_API_KEY": "test"})
        assert keys_file.exists()


# ---------------------------------------------------------------------------
# load_keys_env
# ---------------------------------------------------------------------------


class TestLoadKeysEnv:
    def test_sets_missing_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("ELEVENLABS_API_KEY=sk-test\n")
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

        loaded = load_keys_env()

        assert "ELEVENLABS_API_KEY" in loaded
        assert os.environ["ELEVENLABS_API_KEY"] == "sk-test"

    def test_does_not_override_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("ELEVENLABS_API_KEY=from_file\n")
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)
        monkeypatch.setenv("ELEVENLABS_API_KEY", "from_env")

        loaded = load_keys_env()

        assert "ELEVENLABS_API_KEY" not in loaded
        assert os.environ["ELEVENLABS_API_KEY"] == "from_env"

    def test_returns_loaded_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("ELEVENLABS_API_KEY=sk-1\nOPENAI_API_KEY=sk-2\n")
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "existing")

        loaded = load_keys_env()

        assert loaded == frozenset({"ELEVENLABS_API_KEY"})

    def test_ignores_non_provider_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("PYTHONPATH=/evil\nELEVENLABS_API_KEY=sk-good\n")
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)
        monkeypatch.delenv("PYTHONPATH", raising=False)
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

        loaded = load_keys_env()

        assert "ELEVENLABS_API_KEY" in loaded
        assert "PYTHONPATH" not in loaded
        assert "PYTHONPATH" not in os.environ

    def test_missing_file_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "nonexistent" / "keys.env"
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)

        loaded = load_keys_env()

        assert loaded == frozenset()

    def test_unreadable_file_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("MY_KEY=my_value\n")
        keys_file.chmod(0o000)
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)

        loaded = load_keys_env()

        assert loaded == frozenset()

    def test_empty_values_not_loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("ELEVENLABS_API_KEY=\nOPENAI_API_KEY=value\n")
        monkeypatch.setattr("punt_vox.keys._KEYS_FILE", keys_file)
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        loaded = load_keys_env()

        # Empty value should not be loaded
        assert "ELEVENLABS_API_KEY" not in loaded
        assert "OPENAI_API_KEY" in loaded
