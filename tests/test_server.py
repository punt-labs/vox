"""Tests for server-level helpers (vibe injection, config reading/writing)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.server import (
    _apply_vibe,  # pyright: ignore[reportPrivateUsage]
    _voice_not_found_message,  # pyright: ignore[reportPrivateUsage]
    _write_config_field,  # pyright: ignore[reportPrivateUsage]
    _write_config_fields,  # pyright: ignore[reportPrivateUsage]
    chorus,
    duet,
    ensemble,
    list_voices,
    set_config,
    speak,
)
from punt_vox.types import AudioProviderId, VoiceNotFoundError


@pytest.fixture()
def _patch_config(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Return a writable config path and patch the module."""
    import punt_vox.server as srv

    config = tmp_path / "config.md"
    monkeypatch.setattr(srv, "_CONFIG_PATH", config)
    return config


class TestApplyVibe:
    """Tests for _apply_vibe text injection."""

    def test_prepends_tags(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[excited]"\n---\n')
        result = _apply_vibe("Hello world", expressive_tags=True)
        assert result == "[excited] Hello world"

    def test_multiple_tags(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[frustrated] [sighs]"\n---\n')
        result = _apply_vibe("Hello world", expressive_tags=True)
        assert result == "[frustrated] [sighs] Hello world"

    def test_skips_prepend_when_text_starts_with_tag(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = _apply_vibe("[calm] Already tagged", expressive_tags=True)
        assert result == "[calm] Already tagged"

    def test_skips_prepend_when_text_starts_with_different_tag(
        self, _patch_config: Path
    ) -> None:
        _patch_config.write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = _apply_vibe("[excited] Different tag", expressive_tags=True)
        assert result == "[excited] Different tag"

    def test_skips_prepend_when_tag_contains_punctuation(
        self, _patch_config: Path
    ) -> None:
        _patch_config.write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = _apply_vibe(
            "[dramatic tone] Something important", expressive_tags=True
        )
        assert result == "[dramatic tone] Something important"

    def test_passthrough_when_no_tags(self, tmp_path: Path, monkeypatch: Any) -> None:
        import punt_vox.server as srv

        missing = tmp_path / "missing.md"
        monkeypatch.setattr(srv, "_CONFIG_PATH", missing)
        assert _apply_vibe("Hello world", expressive_tags=True) == "Hello world"

    def test_skips_tags_when_not_supported(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[excited]"\n---\n')
        result = _apply_vibe("Hello world", expressive_tags=False)
        assert result == "Hello world"


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
        import punt_vox.server as srv

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

        with caplog.at_level(logging.WARNING, logger="punt_vox.server"):
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

    def test_writes_session_voice(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(set_config(key="voice", value="aria"))
        assert result == {"key": "voice", "value": "aria"}
        assert 'voice: "aria"' in _patch_config.read_text()

    def test_clears_session_voice(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvoice: "matilda"\n---\n')
        set_config(key="voice", value="")
        assert 'voice: ""' in _patch_config.read_text()

    def test_writes_vibe_mode(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(set_config(key="vibe_mode", value="auto"))
        assert result == {"key": "vibe_mode", "value": "auto"}
        assert 'vibe_mode: "auto"' in _patch_config.read_text()

    def test_writes_vibe_signals(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        set_config(key="vibe_signals", value="tests-pass@14:00")
        assert 'vibe_signals: "tests-pass@14:00"' in _patch_config.read_text()


class TestWriteConfigFields:
    """Tests for _write_config_fields batch helper."""

    def test_writes_multiple_fields(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nnotify: "y"\n---\n')
        updates = {
            "vibe": "happy",
            "vibe_tags": "[cheerful]",
            "vibe_mode": "manual",
        }
        _write_config_fields(updates)
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
        _write_config_fields(updates)
        text = _patch_config.read_text()
        assert 'vibe: "new"' in text
        assert 'vibe_tags: "[new]"' in text
        assert 'vibe_mode: "manual"' in text
        assert "old" not in text

    def test_creates_file_when_missing(self, _patch_config: Path) -> None:
        _write_config_fields({"vibe": "happy", "vibe_tags": "[cheerful]"})
        text = _patch_config.read_text()
        assert text.startswith("---\n")
        assert 'vibe: "happy"' in text
        assert 'vibe_tags: "[cheerful]"' in text
        assert text.rstrip().endswith("---")

    def test_rejects_invalid_key(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        with pytest.raises(ValueError, match="Unknown config key"):
            _write_config_fields({"vibe": "ok", "bad_key": "fail"})
        # File unchanged — validation before write
        assert _patch_config.read_text() == "---\n---\n"

    def test_atomic_single_read_write(
        self, _patch_config: Path, monkeypatch: Any
    ) -> None:
        """Verify batch performs one read and one write."""
        import punt_vox.server as srv

        _patch_config.write_text("---\n---\n")
        read_count = 0
        write_count = 0
        orig_read = Path.read_text
        orig_write = Path.write_text

        def counting_read(self: Path, *args: Any, **kwargs: Any) -> str:
            nonlocal read_count
            if self == srv._CONFIG_PATH:  # pyright: ignore[reportPrivateUsage]
                read_count += 1
            return orig_read(self, *args, **kwargs)

        def counting_write(self: Path, *args: Any, **kwargs: Any) -> int:
            nonlocal write_count
            if self == srv._CONFIG_PATH:  # pyright: ignore[reportPrivateUsage]
                write_count += 1
            return orig_write(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", counting_read)
        monkeypatch.setattr(Path, "write_text", counting_write)
        _write_config_fields({"vibe": "a", "vibe_tags": "[b]", "vibe_mode": "manual"})
        assert read_count == 1
        assert write_count == 1


class TestSetConfigBatch:
    """Tests for set_config batch mode via updates parameter."""

    def test_batch_returns_updates_dict(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(
            set_config(updates={"vibe": "happy", "vibe_tags": "[cheerful]"})
        )
        assert result == {"updates": {"vibe": "happy", "vibe_tags": "[cheerful]"}}

    def test_batch_writes_to_file(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        set_config(updates={"notify": "n", "speak": "n"})
        text = _patch_config.read_text()
        assert 'notify: "n"' in text
        assert 'speak: "n"' in text

    def test_batch_rejects_invalid_key(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        with pytest.raises(ValueError, match="Unknown config key"):
            set_config(updates={"vibe": "ok", "invalid": "bad"})

    def test_batch_ignores_key_value(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(
            set_config(key="notify", value="y", updates={"vibe": "happy"})
        )
        # updates takes precedence; key/value ignored
        assert "updates" in result
        assert result["updates"] == {"vibe": "happy"}
        text = _patch_config.read_text()
        assert "notify" not in text

    def test_single_mode_unchanged(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        result = json.loads(set_config(key="notify", value="y"))
        assert result == {"key": "notify", "value": "y"}

    def test_rejects_missing_key_or_value(self, _patch_config: Path) -> None:
        _patch_config.write_text("---\n---\n")
        with pytest.raises(ValueError, match="requires both"):
            set_config(key="notify")
        with pytest.raises(ValueError, match="requires both"):
            set_config(value="y")
        with pytest.raises(ValueError, match="requires both"):
            set_config()


class TestSpeakVibeTags:
    """Tests for the vibe_tags parameter on speak."""

    def test_vibe_tags_writes_config_before_synthesis(
        self, _patch_config: Path
    ) -> None:
        _patch_config.write_text('---\nvibe_signals: "tests-pass@14:00"\n---\n')
        mock_provider = MagicMock()
        mock_provider.name = "elevenlabs"
        mock_provider.default_voice = "matilda"
        mock_provider.supports_expressive_tags = True
        mock_provider.resolve_voice.return_value = "matilda"
        mock_provider.infer_language_from_voice.return_value = None
        mock_result = MagicMock()
        mock_result.path = _patch_config.parent / "out.mp3"
        mock_result.text = "Done."
        mock_result.provider = AudioProviderId("elevenlabs")
        mock_result.voice = "matilda"
        mock_result.language = None
        mock_result.metadata = {}
        mock_provider.synthesize.return_value = mock_result

        with (
            patch("punt_vox.server.get_provider", return_value=mock_provider),
            patch("punt_vox.server._enqueue_audio"),
            patch("punt_vox.core._pad_audio_file"),
        ):
            speak(
                text="Done.",
                ephemeral=False,
                auto_play=False,
                vibe_tags="[warm] [satisfied]",
            )

        text = _patch_config.read_text()
        assert 'vibe_tags: "[warm] [satisfied]"' in text
        assert 'vibe_signals: ""' in text

    def test_vibe_tags_none_does_not_write_config(self, _patch_config: Path) -> None:
        _patch_config.write_text(
            '---\nvibe_tags: "[old]"\nvibe_signals: "test@1"\n---\n'
        )
        mock_provider = MagicMock()
        mock_provider.name = "elevenlabs"
        mock_provider.default_voice = "matilda"
        mock_provider.supports_expressive_tags = True
        mock_provider.resolve_voice.return_value = "matilda"
        mock_provider.infer_language_from_voice.return_value = None
        mock_result = MagicMock()
        mock_result.path = _patch_config.parent / "out.mp3"
        mock_result.text = "Done."
        mock_result.provider = AudioProviderId("elevenlabs")
        mock_result.voice = "matilda"
        mock_result.language = None
        mock_result.metadata = {}
        mock_provider.synthesize.return_value = mock_result

        with (
            patch("punt_vox.server.get_provider", return_value=mock_provider),
            patch("punt_vox.server._enqueue_audio"),
            patch("punt_vox.core._pad_audio_file"),
        ):
            speak(text="Done.", ephemeral=False, auto_play=False)

        text = _patch_config.read_text()
        assert 'vibe_tags: "[old]"' in text
        assert 'vibe_signals: "test@1"' in text


class TestVoiceNotFoundMessage:
    """Tests for _voice_not_found_message formatter."""

    def test_includes_voice_name(self) -> None:
        exc = VoiceNotFoundError("bob", ["matilda", "aria", "charlie"])
        msg = _voice_not_found_message(exc)
        assert msg.startswith("bob ")

    def test_includes_suggestions(self) -> None:
        exc = VoiceNotFoundError("bob", ["matilda", "aria", "charlie"])
        msg = _voice_not_found_message(exc)
        assert "How about " in msg

    def test_single_available_voice(self) -> None:
        exc = VoiceNotFoundError("bob", ["matilda"])
        msg = _voice_not_found_message(exc)
        assert "matilda" in msg

    def test_empty_available_voices(self) -> None:
        exc = VoiceNotFoundError("bob", [])
        msg = _voice_not_found_message(exc)
        assert "bob " in msg
        assert "How about ?" in msg


def _mock_provider_raising(voice_name: str, available: list[str]) -> MagicMock:
    """Create a mock provider whose resolve_voice raises VoiceNotFoundError."""
    provider = MagicMock()
    provider.name = "elevenlabs"
    provider.default_voice = "matilda"
    provider.supports_expressive_tags = True
    provider.resolve_voice.side_effect = VoiceNotFoundError(voice_name, available)
    provider.infer_language_from_voice.return_value = None
    return provider


class TestSpeakVoiceNotFound:
    """Tests for speak tool returning friendly error on bad voice."""

    def test_returns_friendly_error(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvoice: "bob"\n---\n')
        provider = _mock_provider_raising("bob", ["matilda", "aria", "charlie"])

        with (
            patch("punt_vox.server.get_provider", return_value=provider),
            patch("punt_vox.server._enqueue_audio"),
        ):
            result = json.loads(speak(text="Hello", auto_play=False))

        assert "error" in result
        assert "bob " in result["error"]


class TestChorusVoiceNotFound:
    """Tests for chorus tool returning friendly error on bad voice."""

    def test_returns_friendly_error(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvoice: "bob"\n---\n')
        provider = _mock_provider_raising("bob", ["matilda", "aria", "charlie"])

        with (
            patch("punt_vox.server.get_provider", return_value=provider),
            patch("punt_vox.server._enqueue_audio"),
        ):
            result = json.loads(chorus(texts=["Hello", "World"], auto_play=False))

        assert "error" in result
        assert "bob " in result["error"]


class TestDuetVoiceNotFound:
    """Tests for duet tool returning friendly error on bad voice."""

    def test_voice1_not_found(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvoice: "bob"\n---\n')
        provider = _mock_provider_raising("bob", ["matilda", "aria", "charlie"])

        with (
            patch("punt_vox.server.get_provider", return_value=provider),
            patch("punt_vox.server._enqueue_audio"),
        ):
            result = json.loads(duet(text1="Hello", text2="Hallo", auto_play=False))

        assert "error" in result
        assert "bob " in result["error"]


class TestEnsembleVoiceNotFound:
    """Tests for ensemble tool returning friendly error on bad voice."""

    def test_voice1_not_found(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvoice: "bob"\n---\n')
        provider = _mock_provider_raising("bob", ["matilda", "aria", "charlie"])

        with (
            patch("punt_vox.server.get_provider", return_value=provider),
            patch("punt_vox.server._enqueue_audio"),
        ):
            result = json.loads(
                ensemble(
                    pairs=[["Hello", "Hallo"]],
                    auto_play=False,
                )
            )

        assert "error" in result
        assert "bob " in result["error"]


class TestListVoices:
    """Tests for the list_voices MCP tool."""

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
            result = json.loads(list_voices())
        assert result["provider"] == "elevenlabs"
        assert isinstance(result["all"], list)
        assert len(result["all"]) == 12
        assert isinstance(result["featured"], list)

    def test_featured_includes_blurbs(self, _patch_config: Path) -> None:
        provider = self._mock_provider()
        with patch("punt_vox.server.get_provider", return_value=provider):
            result = json.loads(list_voices())
        for entry in result["featured"]:
            assert "name" in entry
            assert "blurb" in entry
            assert len(entry["blurb"]) > 0

    def test_featured_capped_at_six(self, _patch_config: Path) -> None:
        provider = self._mock_provider()
        with patch("punt_vox.server.get_provider", return_value=provider):
            result = json.loads(list_voices())
        assert len(result["featured"]) <= 6

    def test_current_voice_included(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvoice: "aria"\n---\n')
        provider = self._mock_provider()
        with patch("punt_vox.server.get_provider", return_value=provider):
            result = json.loads(list_voices())
        assert result["current"] == "aria"

    def test_no_current_voice(self, _patch_config: Path) -> None:
        provider = self._mock_provider()
        with patch("punt_vox.server.get_provider", return_value=provider):
            result = json.loads(list_voices())
        assert result["current"] is None

    def test_language_filter_passed_through(self, _patch_config: Path) -> None:
        provider = self._mock_provider()
        with patch("punt_vox.server.get_provider", return_value=provider):
            list_voices(language="de")
        provider.list_voices.assert_called_once_with("de")

    def test_provider_without_blurbs_returns_empty_featured(
        self, _patch_config: Path
    ) -> None:
        provider = self._mock_provider(name="say", voices=["samantha", "alex"])
        with patch("punt_vox.server.get_provider", return_value=provider):
            result = json.loads(list_voices())
        assert result["provider"] == "say"
        assert result["featured"] == []
        assert result["all"] == ["samantha", "alex"]
