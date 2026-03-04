"""Tests for server-level helpers and mic API tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.config import write_field, write_fields
from punt_vox.resolve import apply_vibe
from punt_vox.server import record, unmute, vibe, who
from punt_vox.types import AudioProviderId, VoiceNotFoundError
from punt_vox.voices import voice_not_found_message


@pytest.fixture()
def _patch_config(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Return a writable config path and patch the module."""
    import punt_vox.config as cfg
    import punt_vox.server as srv

    config = tmp_path / "config.md"
    monkeypatch.setattr(srv, "_CONFIG_PATH", config)
    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
    return config


# ---------------------------------------------------------------------------
# apply_vibe tests
# ---------------------------------------------------------------------------


class TestApplyVibe:
    """Tests for apply_vibe text injection."""

    def test_prepends_tags(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[excited]"\n---\n')
        result = apply_vibe("Hello world", expressive_tags=True)
        assert result == "[excited] Hello world"

    def test_multiple_tags(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[frustrated] [sighs]"\n---\n')
        result = apply_vibe("Hello world", expressive_tags=True)
        assert result == "[frustrated] [sighs] Hello world"

    def test_skips_prepend_when_text_starts_with_tag(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe("[calm] Already tagged", expressive_tags=True)
        assert result == "[calm] Already tagged"

    def test_skips_prepend_when_text_starts_with_different_tag(
        self, _patch_config: Path
    ) -> None:
        _patch_config.write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe("[excited] Different tag", expressive_tags=True)
        assert result == "[excited] Different tag"

    def test_skips_prepend_when_tag_contains_punctuation(
        self, _patch_config: Path
    ) -> None:
        _patch_config.write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe("[dramatic tone] Something important", expressive_tags=True)
        assert result == "[dramatic tone] Something important"

    def test_passthrough_when_no_tags(self, tmp_path: Path, monkeypatch: Any) -> None:
        import punt_vox.config as cfg

        missing = tmp_path / "missing.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", missing)
        assert apply_vibe("Hello world", expressive_tags=True) == "Hello world"

    def test_skips_tags_when_not_supported(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[excited]"\n---\n')
        result = apply_vibe("Hello world", expressive_tags=False)
        assert result == "Hello world"


# ---------------------------------------------------------------------------
# Config write tests
# ---------------------------------------------------------------------------


class TestWriteConfigField:
    """Tests for write_field in-place YAML editing."""

    def test_creates_file_when_missing(self, _patch_config: Path) -> None:
        write_field("vibe_tags", "[excited]")
        assert _patch_config.exists()
        text = _patch_config.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert text.startswith("---\n")
        assert text.rstrip().endswith("---")

    def test_updates_existing_field(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[tired]"\n---\n')
        write_field("vibe_tags", "[excited]")
        text = _patch_config.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert "[tired]" not in text

    def test_updates_unquoted_field(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\nvibe_tags: [whispers]\n---\n")
        write_field("vibe_tags", "[excited]")
        text = _patch_config.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert "[whispers]" not in text

    def test_inserts_new_field_before_closing_fence(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nnotify: "y"\n---\n')
        write_field("vibe_tags", "[excited]")
        text = _patch_config.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert 'notify: "y"' in text

    def test_preserves_other_fields(self, _patch_config: Path) -> None:
        _patch_config.write_text(
            '---\nnotify: "y"\nvibe_tags: "[tired]"\nspeak: "y"\n---\n'
        )
        write_field("vibe_tags", "[excited]")
        text = _patch_config.read_text()
        assert 'notify: "y"' in text
        assert 'speak: "y"' in text
        assert 'vibe_tags: "[excited]"' in text

    def test_clears_field_with_empty_string(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[tired]"\n---\n')
        write_field("vibe_tags", "")
        text = _patch_config.read_text()
        assert 'vibe_tags: ""' in text

    def test_rejects_unknown_key(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        with pytest.raises(ValueError, match="Unknown config key"):
            write_field("bad_key", "value")

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "dir" / "config.md"
        write_field("notify", "y", nested)
        assert nested.exists()
        assert 'notify: "y"' in nested.read_text()

    def test_malformed_file_warns_and_overwrites(
        self, _patch_config: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _patch_config.write_text("no frontmatter at all\n")
        import logging

        with caplog.at_level(logging.WARNING, logger="punt_vox.config"):
            write_field("notify", "y", _patch_config)
        assert 'notify: "y"' in _patch_config.read_text()
        assert "Malformed config" in caplog.text


class TestWriteConfigFields:
    """Tests for write_fields batch helper."""

    def test_writes_multiple_fields(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nnotify: "y"\n---\n')
        updates = {
            "vibe": "happy",
            "vibe_tags": "[cheerful]",
            "vibe_mode": "manual",
        }
        write_fields(updates)
        text = _patch_config.read_text()
        assert 'vibe: "happy"' in text
        assert 'vibe_tags: "[cheerful]"' in text
        assert 'vibe_mode: "manual"' in text
        assert 'notify: "y"' in text

    def test_updates_existing_fields(self, _patch_config: Path) -> None:
        _patch_config.write_text(
            '---\nvibe: "old"\nvibe_tags: "[old]"\nvibe_mode: "off"\n---\n'
        )
        updates = {
            "vibe": "new",
            "vibe_tags": "[new]",
            "vibe_mode": "manual",
        }
        write_fields(updates)
        text = _patch_config.read_text()
        assert 'vibe: "new"' in text
        assert 'vibe_tags: "[new]"' in text
        assert 'vibe_mode: "manual"' in text
        assert "old" not in text

    def test_creates_file_when_missing(self, _patch_config: Path) -> None:
        write_fields({"vibe": "happy", "vibe_tags": "[cheerful]"})
        text = _patch_config.read_text()
        assert text.startswith("---\n")
        assert 'vibe: "happy"' in text
        assert 'vibe_tags: "[cheerful]"' in text
        assert text.rstrip().endswith("---")

    def test_rejects_invalid_key(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        with pytest.raises(ValueError, match="Unknown config key"):
            write_fields({"vibe": "ok", "bad_key": "fail"})
        assert _patch_config.read_text() == "---\n---\n"

    def test_atomic_single_read_write(
        self, _patch_config: Path, monkeypatch: Any
    ) -> None:
        """Verify batch performs one read and one write."""
        _patch_config.write_text("---\n---\n")
        read_count = 0
        write_count = 0
        orig_read = Path.read_text
        orig_write = Path.write_text

        def counting_read(self: Path, *args: Any, **kwargs: Any) -> str:
            nonlocal read_count
            if self == _patch_config:
                read_count += 1
            return orig_read(self, *args, **kwargs)

        def counting_write(self: Path, *args: Any, **kwargs: Any) -> int:
            nonlocal write_count
            if self == _patch_config:
                write_count += 1
            return orig_write(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", counting_read)
        monkeypatch.setattr(Path, "write_text", counting_write)
        write_fields(
            {"vibe": "a", "vibe_tags": "[b]", "vibe_mode": "manual"}, _patch_config
        )
        assert read_count == 1
        assert write_count == 1


# ---------------------------------------------------------------------------
# voice_not_found_message tests
# ---------------------------------------------------------------------------


class TestVoiceNotFoundMessage:
    """Tests for voice_not_found_message formatter."""

    def test_includes_voice_name(self) -> None:
        exc = VoiceNotFoundError("bob", ["matilda", "aria", "charlie"])
        msg = voice_not_found_message(exc)
        assert msg.startswith("bob ")

    def test_includes_suggestions(self) -> None:
        exc = VoiceNotFoundError("bob", ["matilda", "aria", "charlie"])
        msg = voice_not_found_message(exc)
        assert "How about " in msg

    def test_single_available_voice(self) -> None:
        exc = VoiceNotFoundError("bob", ["matilda"])
        msg = voice_not_found_message(exc)
        assert "matilda" in msg

    def test_empty_available_voices(self) -> None:
        exc = VoiceNotFoundError("bob", [])
        msg = voice_not_found_message(exc)
        assert "bob " in msg
        assert "How about ?" in msg


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------


def _mock_provider_raising(voice_name: str, available: list[str]) -> MagicMock:
    """Create a mock provider whose resolve_voice raises VoiceNotFoundError."""
    provider = MagicMock()
    provider.name = "elevenlabs"
    provider.default_voice = "matilda"
    provider.supports_expressive_tags = True
    provider.resolve_voice.side_effect = VoiceNotFoundError(voice_name, available)
    provider.infer_language_from_voice.return_value = None
    return provider


def _mock_provider_ok() -> MagicMock:
    """Create a mock provider that succeeds."""
    provider = MagicMock()
    provider.name = "elevenlabs"
    provider.default_voice = "matilda"
    provider.supports_expressive_tags = True
    provider.resolve_voice.return_value = "matilda"
    provider.infer_language_from_voice.return_value = None
    return provider


def _mock_result(tmp_path: Path) -> MagicMock:
    """Create a mock SynthesisResult."""
    result = MagicMock()
    result.path = tmp_path / "out.mp3"
    result.text = "Done."
    result.provider = AudioProviderId("elevenlabs")
    result.voice = "matilda"
    result.language = None
    result.metadata = {}
    return result


# ---------------------------------------------------------------------------
# unmute tool tests
# ---------------------------------------------------------------------------


class TestUnmute:
    """Tests for the unmute MCP tool."""

    def test_simple_text(self, _patch_config: Path, tmp_path: Path) -> None:
        provider = _mock_provider_ok()
        mock_result = _mock_result(tmp_path)
        provider.synthesize.return_value = mock_result

        with (
            patch("punt_vox.server.get_provider", return_value=provider),
            patch("punt_vox.server._enqueue_audio"),
            patch("punt_vox.core._pad_audio_file"),
        ):
            result = json.loads(unmute(text="Hello world"))

        assert isinstance(result, list)
        assert len(result) == 1

    def test_segments(self, _patch_config: Path, tmp_path: Path) -> None:
        provider = _mock_provider_ok()
        mock_r = _mock_result(tmp_path)
        provider.synthesize.return_value = mock_r

        with (
            patch("punt_vox.server.get_provider", return_value=provider),
            patch("punt_vox.server._enqueue_audio"),
            patch("punt_vox.server.TTSClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.synthesize_batch.return_value = [mock_r]
            result = json.loads(
                unmute(
                    segments=[
                        {"voice": "roger", "text": "Part one."},
                        {"text": "Part two."},
                    ]
                )
            )

        assert isinstance(result, list)
        assert len(result) == 1

    def test_no_input_returns_error(self, _patch_config: Path) -> None:
        result = json.loads(unmute())
        assert "error" in result

    def test_vibe_tags_writes_config(self, _patch_config: Path, tmp_path: Path) -> None:
        _patch_config.write_text('---\nvibe_signals: "tests-pass@14:00"\n---\n')
        provider = _mock_provider_ok()
        mock_result = _mock_result(tmp_path)
        provider.synthesize.return_value = mock_result

        with (
            patch("punt_vox.server.get_provider", return_value=provider),
            patch("punt_vox.server._enqueue_audio"),
            patch("punt_vox.core._pad_audio_file"),
        ):
            unmute(text="Done.", vibe_tags="[warm] [satisfied]")

        text = _patch_config.read_text()
        assert 'vibe_tags: "[warm] [satisfied]"' in text
        assert 'vibe_signals: ""' in text

    def test_voice_not_found_uses_default(
        self, _patch_config: Path, tmp_path: Path
    ) -> None:
        """When a segment voice is not found, falls back to provider default."""
        provider = _mock_provider_ok()
        # First call raises (segment voice), provider still has default
        call_count = 0

        def resolve_side_effect(name: str, language: str | None = None) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise VoiceNotFoundError(name, ["matilda"])
            return name

        provider.resolve_voice.side_effect = resolve_side_effect
        mock_result = _mock_result(tmp_path)
        provider.synthesize.return_value = mock_result

        with (
            patch("punt_vox.server.get_provider", return_value=provider),
            patch("punt_vox.server._enqueue_audio"),
            patch("punt_vox.core._pad_audio_file"),
        ):
            result = json.loads(unmute(segments=[{"voice": "bob", "text": "Hello"}]))

        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# record tool tests
# ---------------------------------------------------------------------------


class TestRecord:
    """Tests for the record MCP tool."""

    def test_simple_text(self, _patch_config: Path, tmp_path: Path) -> None:
        provider = _mock_provider_ok()
        mock_result = _mock_result(tmp_path)
        provider.synthesize.return_value = mock_result

        with (
            patch("punt_vox.server.get_provider", return_value=provider),
            patch("punt_vox.core._pad_audio_file"),
        ):
            result = json.loads(record(text="Hello world"))

        assert isinstance(result, list)
        assert len(result) == 1

    def test_no_input_returns_error(self, _patch_config: Path) -> None:
        result = json.loads(record())
        assert "error" in result

    def test_custom_output_path(self, _patch_config: Path, tmp_path: Path) -> None:
        provider = _mock_provider_ok()
        mock_result = _mock_result(tmp_path)
        provider.synthesize.return_value = mock_result

        out_path = str(tmp_path / "custom.mp3")
        with (
            patch("punt_vox.server.get_provider", return_value=provider),
            patch("punt_vox.core._pad_audio_file"),
        ):
            result = json.loads(record(text="Hello", output_path=out_path))

        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# vibe tool tests
# ---------------------------------------------------------------------------


class TestVibeTool:
    """Tests for the vibe MCP tool."""

    def test_set_mood(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(vibe(mood="excited"))
        assert result["vibe"]["vibe"] == "excited"
        assert 'vibe: "excited"' in _patch_config.read_text()

    def test_set_tags(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(vibe(tags="[warm] [calm]"))
        assert result["vibe"]["vibe_tags"] == "[warm] [calm]"
        text = _patch_config.read_text()
        assert 'vibe_tags: "[warm] [calm]"' in text
        assert 'vibe_signals: ""' in text

    def test_set_mode(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(vibe(mode="manual"))
        assert result["vibe"]["vibe_mode"] == "manual"

    def test_invalid_mode(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(vibe(mode="invalid"))
        assert "error" in result

    def test_no_args_returns_error(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(vibe())
        assert "error" in result

    def test_combined_mood_and_tags(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(vibe(mood="happy", tags="[cheerful]", mode="manual"))
        updates = result["vibe"]
        assert updates["vibe"] == "happy"
        assert updates["vibe_tags"] == "[cheerful]"
        assert updates["vibe_mode"] == "manual"


# ---------------------------------------------------------------------------
# who tool tests
# ---------------------------------------------------------------------------


class TestWho:
    """Tests for the who MCP tool."""

    def _mock_provider(
        self, name: str = "elevenlabs", voices: list[str] | None = None
    ) -> MagicMock:
        provider = MagicMock()
        provider.name = name
        provider.list_voices.return_value = voices or [
            "aria",
            "callum",
            "charlie",
            "drew",
            "george",
            "jessica",
            "laura",
            "lily",
            "matilda",
            "river",
            "roger",
            "sarah",
        ]
        return provider

    def test_returns_provider_and_voices(self, _patch_config: Path) -> None:
        provider = self._mock_provider()
        with patch("punt_vox.server.get_provider", return_value=provider):
            result = json.loads(who())
        assert result["provider"] == "elevenlabs"
        assert isinstance(result["all"], list)
        assert len(result["all"]) == 12
        assert isinstance(result["featured"], list)

    def test_featured_includes_blurbs(self, _patch_config: Path) -> None:
        provider = self._mock_provider()
        with patch("punt_vox.server.get_provider", return_value=provider):
            result = json.loads(who())
        for entry in result["featured"]:
            assert "name" in entry
            assert "blurb" in entry
            assert len(entry["blurb"]) > 0

    def test_featured_capped_at_six(self, _patch_config: Path) -> None:
        provider = self._mock_provider()
        with patch("punt_vox.server.get_provider", return_value=provider):
            result = json.loads(who())
        assert len(result["featured"]) <= 6

    def test_current_voice_included(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvoice: "aria"\n---\n')
        provider = self._mock_provider()
        with patch("punt_vox.server.get_provider", return_value=provider):
            result = json.loads(who())
        assert result["current"] == "aria"

    def test_no_current_voice(self, _patch_config: Path) -> None:
        provider = self._mock_provider()
        with patch("punt_vox.server.get_provider", return_value=provider):
            result = json.loads(who())
        assert result["current"] is None

    def test_language_filter_passed_through(self, _patch_config: Path) -> None:
        provider = self._mock_provider()
        with patch("punt_vox.server.get_provider", return_value=provider):
            who(language="de")
        provider.list_voices.assert_called_once_with("de")

    def test_provider_without_blurbs_returns_empty_featured(
        self, _patch_config: Path
    ) -> None:
        provider = self._mock_provider(name="say", voices=["samantha", "alex"])
        with patch("punt_vox.server.get_provider", return_value=provider):
            result = json.loads(who())
        assert result["provider"] == "say"
        assert result["featured"] == []
        assert result["all"] == ["samantha", "alex"]
