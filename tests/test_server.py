"""Tests for server-level helpers and mic API tools."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import typing
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from punt_vox.client import SynthesizeResult
from punt_vox.config import write_field, write_fields
from punt_vox.resolve import (
    apply_vibe,
    split_leading_expressive_tags,
    strip_expressive_tags,
)
from punt_vox.server import (
    SessionState,
    music,
    notify,
    record,
    speak,
    status,
    unmute,
    vibe,
    who,
)
from punt_vox.types import VoiceNotFoundError
from punt_vox.voices import voice_not_found_message


@pytest.fixture()
def _patch_config(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Return a writable config path and patch config module default."""
    import punt_vox.config as cfg

    config = tmp_path / "config.md"
    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
    return config


@pytest.fixture(autouse=True)
def _fresh_session(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Reset server session state before every test."""
    import punt_vox.server as srv

    monkeypatch.setattr(srv, "_state", SessionState())
    monkeypatch.setattr(srv, "_speak_explicit", False)


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

    def test_strips_leading_tags_when_not_supported(self, _patch_config: Path) -> None:
        result = apply_vibe("[serious] Hello world", expressive_tags=False)
        assert result == "Hello world"

    def test_strips_multiple_leading_tags_when_not_supported(
        self, _patch_config: Path
    ) -> None:
        result = apply_vibe("[serious] [calm] Hello world", expressive_tags=False)
        assert result == "Hello world"

    def test_preserves_text_when_only_tags(self, _patch_config: Path) -> None:
        result = apply_vibe("[serious]", expressive_tags=False)
        assert result == "[serious]"

    def test_override_tags_win_over_config(self, _patch_config: Path) -> None:
        _patch_config.write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe(
            "Hello world",
            expressive_tags=True,
            override_tags="[excited]",
        )
        assert result == "[excited] Hello world"

    def test_override_tags_empty_falls_through_to_config(
        self, _patch_config: Path
    ) -> None:
        _patch_config.write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe("Hello world", expressive_tags=True, override_tags="")
        assert result == "[calm] Hello world"


class TestStripExpressiveTags:
    """Tests for strip_expressive_tags — the underlying primitive."""

    def test_strips_single_leading_tag(self) -> None:
        assert strip_expressive_tags("[serious] Hello world") == "Hello world"

    def test_strips_multiple_leading_tags(self) -> None:
        assert strip_expressive_tags("[serious] [calm] Hello world") == "Hello world"

    def test_strips_tag_with_leading_whitespace(self) -> None:
        assert strip_expressive_tags("   [excited] Hello") == "Hello"

    def test_strips_tag_with_punctuation_inside(self) -> None:
        assert (
            strip_expressive_tags("[dramatic tone] Important message")
            == "Important message"
        )

    def test_passes_through_text_with_no_tags(self) -> None:
        assert strip_expressive_tags("Hello world") == "Hello world"

    def test_only_strips_leading_tags_not_embedded(self) -> None:
        assert strip_expressive_tags("Hello [serious] world") == "Hello [serious] world"

    def test_returns_original_when_stripping_would_empty_text(self) -> None:
        # Degenerate case: text was nothing but tags. Returning empty would
        # produce silence, which is worse than speaking the literal text.
        assert strip_expressive_tags("[serious]") == "[serious]"

    def test_returns_original_when_stripping_would_leave_only_whitespace(self) -> None:
        assert strip_expressive_tags("[serious]   ") == "[serious]   "

    def test_empty_string_passes_through(self) -> None:
        assert strip_expressive_tags("") == ""


class TestSplitLeadingExpressiveTags:
    """Tests for split_leading_expressive_tags — the (tags, body) splitter."""

    def test_splits_single_leading_tag(self) -> None:
        tags, body = split_leading_expressive_tags("[serious] Hello world")
        assert tags == "[serious]"
        # _LEADING_TAGS_RE consumes the whitespace after the bracket
        # via the trailing \s* in its pattern.
        assert body == "Hello world"

    def test_splits_multiple_leading_tags(self) -> None:
        tags, body = split_leading_expressive_tags("[serious] [calm] Hello world")
        assert tags == "[serious] [calm]"
        assert body == "Hello world"

    def test_splits_tag_with_leading_whitespace(self) -> None:
        tags, body = split_leading_expressive_tags("   [excited] Hello")
        assert tags == "[excited]"
        assert body == "Hello"

    def test_splits_tag_with_punctuation_inside(self) -> None:
        tags, body = split_leading_expressive_tags("[dramatic tone] Important message")
        assert tags == "[dramatic tone]"
        assert body == "Important message"

    def test_no_tags_returns_empty_tags_and_full_text(self) -> None:
        tags, body = split_leading_expressive_tags("Hello world")
        assert tags == ""
        assert body == "Hello world"

    def test_only_splits_leading_not_embedded(self) -> None:
        tags, body = split_leading_expressive_tags("Hello [serious] world")
        assert tags == ""
        assert body == "Hello [serious] world"

    def test_tags_only_input(self) -> None:
        tags, body = split_leading_expressive_tags("[serious]")
        assert tags == "[serious]"
        assert body == ""

    def test_empty_string(self) -> None:
        tags, body = split_leading_expressive_tags("")
        assert tags == ""
        assert body == ""


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
# unmute tool tests
# ---------------------------------------------------------------------------


class TestUnmute:
    """Tests for the unmute MCP tool."""

    def test_simple_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(request_id="req123")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(unmute(text="Hello world"))

        assert isinstance(result, list)
        assert len(result) == 1  # pyright: ignore[reportUnknownArgumentType]
        assert result[0]["text"] == "Hello world"
        mock_client.synthesize.assert_called_once()

    def test_segments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(request_id="req456")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(
            unmute(
                segments=[
                    {"voice": "roger", "text": "Part one."},
                    {"text": "Part two."},
                ]
            )
        )

        assert isinstance(result, list)
        assert len(result) == 2  # pyright: ignore[reportUnknownArgumentType]
        assert mock_client.synthesize.call_count == 2

    def test_no_input_returns_error(self) -> None:
        result = json.loads(unmute())
        assert "error" in result

    def test_vibe_tags_updates_session_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import punt_vox.server as srv

        srv._state.vibe_signals = "tests-pass@14:00"

        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(request_id="req789")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        unmute(text="Done.", vibe_tags="[warm] [satisfied]")

        assert srv._state.vibe_tags == "[warm] [satisfied]"
        assert srv._state.vibe_signals == ""

    def test_voxd_connection_error_returns_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from punt_vox.client import VoxdConnectionError

        mock_client = MagicMock()
        mock_client.synthesize.side_effect = VoxdConnectionError("not running")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(unmute(text="Hello"))
        assert "error" in result
        assert "not running" in result["error"]

    def test_provider_persists_to_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(request_id="req_prov")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        unmute(text="Hello", provider="openai")
        assert srv._state.provider == "openai"

    def test_model_persists_to_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(request_id="req_mod")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        unmute(text="Hello", model="eleven_v3")
        assert srv._state.model == "eleven_v3"

    def test_config_only_update_no_text(self) -> None:
        """Provider/model/vibe_tags update without text returns config update."""
        import punt_vox.server as srv

        result = json.loads(unmute(provider="polly"))
        assert result["status"] == "config updated"
        assert result["provider"] == "polly"
        assert srv._state.provider == "polly"

    def test_voice_settings_validation(self) -> None:
        """Invalid voice settings raise ValueError."""
        with pytest.raises(ValueError, match="stability"):
            unmute(text="Hello", stability=1.5)

    def test_language_passed_to_synthesize(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Top-level language param is forwarded to voxd synthesize."""
        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(request_id="req_lang")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        unmute(text="Guten Tag", language="de")

        call_kwargs = mock_client.synthesize.call_args
        assert call_kwargs[1]["language"] == "de"

    def test_per_segment_language_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Per-segment language overrides the top-level default."""
        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(request_id="req_lang2")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        unmute(
            language="en",
            segments=[{"text": "Bonjour", "language": "fr"}],
        )

        call_kwargs = mock_client.synthesize.call_args
        assert call_kwargs[1]["language"] == "fr"


# ---------------------------------------------------------------------------
# record tool tests
# ---------------------------------------------------------------------------


class TestRecord:
    """Tests for the record MCP tool."""

    def test_simple_text(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.record.return_value = b"\xff\xfb\x90\x00" * 10
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)
        monkeypatch.setattr("punt_vox.server._default_output_dir", lambda: tmp_path)

        result = json.loads(record(text="Hello world"))

        assert isinstance(result, list)
        assert len(result) == 1  # pyright: ignore[reportUnknownArgumentType]
        assert result[0]["text"] == "Hello world"
        assert "path" in result[0]
        mock_client.record.assert_called_once()

    def test_no_input_returns_error(self) -> None:
        result = json.loads(record())
        assert "error" in result

    def test_custom_output_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        mock_client = MagicMock()
        mock_client.record.return_value = b"\xff\xfb\x90\x00" * 10
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        out_path = str(tmp_path / "custom.mp3")
        result = json.loads(record(text="Hello", output_path=out_path))

        assert isinstance(result, list)
        assert result[0]["path"] == out_path

    def test_voxd_connection_error_returns_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from punt_vox.client import VoxdConnectionError

        mock_client = MagicMock()
        mock_client.record.side_effect = VoxdConnectionError("not running")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)
        monkeypatch.setattr("punt_vox.server._default_output_dir", lambda: tmp_path)

        result = json.loads(record(text="Hello"))
        assert "error" in result


# ---------------------------------------------------------------------------
# vibe tool tests
# ---------------------------------------------------------------------------


class TestVibeTool:
    """Tests for the vibe MCP tool."""

    def test_set_mood(self) -> None:
        import punt_vox.server as srv

        result = json.loads(vibe(mood="excited"))
        assert result["vibe"]["vibe"] == "excited"
        assert srv._state.vibe == "excited"

    def test_set_tags(self) -> None:
        import punt_vox.server as srv

        result = json.loads(vibe(tags="[warm] [calm]"))
        assert result["vibe"]["vibe_tags"] == "[warm] [calm]"
        assert srv._state.vibe_tags == "[warm] [calm]"
        assert srv._state.vibe_signals == ""

    def test_set_mode(self) -> None:
        import punt_vox.server as srv

        result = json.loads(vibe(mode="manual"))
        assert result["vibe"]["vibe_mode"] == "manual"
        assert srv._state.vibe_mode == "manual"

    def test_invalid_mode(self) -> None:
        result = json.loads(vibe(mode="invalid"))
        assert "error" in result

    def test_no_args_returns_error(self) -> None:
        result = json.loads(vibe())
        assert "error" in result

    def test_combined_mood_and_tags(self) -> None:
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

    _VOICE_LIST: typing.ClassVar[list[str]] = [
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

    def test_returns_provider_and_voices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        srv._state.provider = "elevenlabs"

        mock_client = MagicMock()
        mock_client.voices.return_value = self._VOICE_LIST
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(who())
        assert result["provider"] == "elevenlabs"
        assert isinstance(result["all"], list)
        assert len(result["all"]) == 12
        assert isinstance(result["featured"], list)

    def test_featured_includes_blurbs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        srv._state.provider = "elevenlabs"

        mock_client = MagicMock()
        mock_client.voices.return_value = self._VOICE_LIST
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(who())
        for entry in result["featured"]:
            assert "name" in entry
            assert "blurb" in entry
            assert len(entry["blurb"]) > 0

    def test_featured_capped_at_six(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        srv._state.provider = "elevenlabs"

        mock_client = MagicMock()
        mock_client.voices.return_value = self._VOICE_LIST
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(who())
        assert len(result["featured"]) <= 6

    def test_current_voice_included(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        srv._state.voice = "aria"
        srv._state.provider = "elevenlabs"

        mock_client = MagicMock()
        mock_client.voices.return_value = self._VOICE_LIST
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(who())
        assert result["current"] == "aria"

    def test_no_current_voice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        mock_client.voices.return_value = self._VOICE_LIST
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(who())
        assert result["current"] is None

    def test_language_filter_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_client = MagicMock()
        mock_client.voices.return_value = []
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        who(language="de")
        mock_client.voices.assert_called_once_with(provider=None)

    def test_provider_without_blurbs_returns_empty_featured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import punt_vox.server as srv

        srv._state.provider = "say"

        mock_client = MagicMock()
        mock_client.voices.return_value = ["samantha", "alex"]
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(who())
        assert result["provider"] == "say"
        assert result["featured"] == []
        assert result["all"] == ["samantha", "alex"]

    def test_voxd_connection_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from punt_vox.client import VoxdConnectionError

        mock_client = MagicMock()
        mock_client.voices.side_effect = VoxdConnectionError("not running")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(who())
        assert "error" in result


# ---------------------------------------------------------------------------
# notify tool tests
# ---------------------------------------------------------------------------


class TestNotifyTool:
    """Tests for the notify MCP tool."""

    def test_set_mode_y(self) -> None:
        import punt_vox.server as srv

        result = json.loads(notify(mode="y"))
        assert result["notify"]["notify"] == "y"
        assert srv._state.notify == "y"

    def test_set_mode_n(self) -> None:
        import punt_vox.server as srv

        result = json.loads(notify(mode="n"))
        assert result["notify"]["notify"] == "n"
        assert srv._state.notify == "n"

    def test_speak_unset_c_inits_voice(self) -> None:
        """speak not yet set — mode=c should initialize it to y."""
        import punt_vox.server as srv

        result = json.loads(notify(mode="c"))
        assert result["notify"]["notify"] == "c"
        assert result["notify"]["speak"] == "y"
        assert srv._state.speak == "y"

    def test_speak_unset_y_inits_voice(self) -> None:
        """speak not yet set — mode=y should also initialize it."""
        result = json.loads(notify(mode="y"))
        assert result["notify"]["speak"] == "y"

    def test_speak_unset_first_init_inits_voice(self) -> None:
        """Fresh session — first init."""
        result = json.loads(notify(mode="y"))
        assert result["notify"]["speak"] == "y"

    def test_speak_set_preserved_by_c(self) -> None:
        """User explicitly muted — mode=c should not re-enable voice."""
        import punt_vox.server as srv

        # Simulate explicit mute.
        srv._state.speak = "n"
        srv._speak_explicit = True

        result = json.loads(notify(mode="c"))
        assert result["notify"]["notify"] == "c"
        assert "speak" not in result["notify"]
        assert srv._state.speak == "n"

    def test_speak_set_preserved_by_y(self) -> None:
        """User explicitly muted — mode=y should not re-enable voice."""
        import punt_vox.server as srv

        srv._state.speak = "n"
        srv._speak_explicit = True

        result = json.loads(notify(mode="y"))
        assert "speak" not in result["notify"]
        assert srv._state.speak == "n"

    def test_set_voice(self) -> None:
        import punt_vox.server as srv

        result = json.loads(notify(mode="c", voice="matilda"))
        assert result["notify"]["voice"] == "matilda"
        assert srv._state.voice == "matilda"

    def test_invalid_mode(self) -> None:
        result = json.loads(notify(mode="x"))
        assert "error" in result


# ---------------------------------------------------------------------------
# speak tool tests
# ---------------------------------------------------------------------------


class TestSpeakTool:
    """Tests for the speak MCP tool."""

    def test_set_speak_y(self) -> None:
        import punt_vox.server as srv

        result = json.loads(speak(mode="y"))
        assert result["speak"] == "y"
        assert srv._state.speak == "y"

    def test_set_speak_n(self) -> None:
        import punt_vox.server as srv

        result = json.loads(speak(mode="n"))
        assert result["speak"] == "n"
        assert srv._state.speak == "n"

    def test_set_voice(self) -> None:
        import punt_vox.server as srv

        result = json.loads(speak(mode="y", voice="matilda"))
        assert result["speak"] == "y"
        assert result["voice"] == "matilda"
        assert srv._state.voice == "matilda"

    def test_invalid_mode(self) -> None:
        result = json.loads(speak(mode="x"))
        assert "error" in result

    def test_marks_speak_explicit(self) -> None:
        import punt_vox.server as srv

        assert not srv._speak_explicit
        speak(mode="n")
        assert srv._speak_explicit


# ---------------------------------------------------------------------------
# status tool tests
# ---------------------------------------------------------------------------


class TestStatusTool:
    """Tests for the status MCP tool."""

    def test_returns_state_fields(self) -> None:
        import punt_vox.server as srv

        srv._state.notify = "c"
        srv._state.speak = "y"
        srv._state.voice = "sarah"
        srv._state.provider = "elevenlabs"
        srv._state.vibe_mode = "auto"
        srv._state.vibe_tags = "[excited]"
        srv._state.vibe_signals = "tests-pass@12:00"

        result = json.loads(status())
        assert result["provider"] == "elevenlabs"
        assert result["voice"] == "sarah"
        assert result["notify"] == "c"
        assert result["speak"] == "y"
        assert result["vibe_mode"] == "auto"
        assert result["vibe_tags"] == "[excited]"
        assert result["vibe_signals"] == "tests-pass@12:00"

    def test_defaults_when_no_state_set(self) -> None:
        result = json.loads(status())
        assert result["voice"] is None
        assert result["provider"] is None
        assert result["notify"] == "n"
        assert result["speak"] == "n"
        assert result["music_mode"] == "off"

    def test_music_mode_reflected(self) -> None:
        import punt_vox.server as srv

        srv._state.music_mode = "on"
        result = json.loads(status())
        assert result["music_mode"] == "on"


# ---------------------------------------------------------------------------
# SessionState identity tests
# ---------------------------------------------------------------------------


class TestSessionState:
    """Tests for SessionState defaults and session_id generation."""

    def test_session_id_is_uuid_hex(self) -> None:
        state = SessionState()
        assert len(state.session_id) == 32
        int(state.session_id, 16)  # valid hex

    def test_each_instance_gets_unique_id(self) -> None:
        a = SessionState()
        b = SessionState()
        assert a.session_id != b.session_id

    def test_music_mode_defaults_off(self) -> None:
        state = SessionState()
        assert state.music_mode == "off"


# ---------------------------------------------------------------------------
# music tool tests
# ---------------------------------------------------------------------------


class TestMusicTool:
    """Tests for the music MCP tool."""

    def test_music_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        srv._state.vibe = "focused"
        srv._state.vibe_tags = "[calm]"

        mock_client = MagicMock()
        mock_client.music.return_value = {
            "type": "music_on",
            "id": "abc",
            "status": "generating",
        }
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(music(mode="on", style="techno"))

        assert result["status"] == "generating"
        assert srv._state.music_mode == "on"
        mock_client.music.assert_called_once_with(
            mode="on",
            style="techno",
            vibe="focused",
            vibe_tags="[calm]",
            owner_id=srv._state.session_id,
        )

    def test_music_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        srv._state.music_mode = "on"

        mock_client = MagicMock()
        mock_client.music.return_value = {
            "type": "music_off",
            "id": "abc",
            "status": "stopped",
        }
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(music(mode="off"))

        assert result["status"] == "stopped"
        assert srv._state.music_mode == "off"

    def test_music_invalid_mode(self) -> None:
        result = json.loads(music(mode="pause"))
        assert "error" in result

    def test_music_on_no_vibe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When vibe is None, empty strings are sent to voxd."""
        import punt_vox.server as srv

        mock_client = MagicMock()
        mock_client.music.return_value = {
            "type": "music_on",
            "id": "x",
            "status": "generating",
        }
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        music(mode="on")

        call_kwargs = mock_client.music.call_args[1]
        assert call_kwargs["vibe"] == ""
        assert call_kwargs["vibe_tags"] == ""
        assert srv._state.music_mode == "on"

    def test_music_on_no_style(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When style is None, empty string is sent."""
        mock_client = MagicMock()
        mock_client.music.return_value = {
            "type": "music_on",
            "id": "x",
            "status": "generating",
        }
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        music(mode="on")

        call_kwargs = mock_client.music.call_args[1]
        assert call_kwargs["style"] == ""

    def test_music_connection_error_resets_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import punt_vox.server as srv
        from punt_vox.client import VoxdConnectionError

        srv._state.music_mode = "on"

        mock_client = MagicMock()
        mock_client.music.side_effect = VoxdConnectionError("not running")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(music(mode="on"))

        assert "error" in result
        assert srv._state.music_mode == "off"


# ---------------------------------------------------------------------------
# vibe tool — music propagation tests
# ---------------------------------------------------------------------------


class TestVibeToolMusicPropagation:
    """Tests for vibe changes propagating to music loop."""

    def test_vibe_propagates_when_music_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import punt_vox.server as srv

        srv._state.music_mode = "on"
        srv._state.vibe = "old"
        srv._state.vibe_tags = "[old]"

        mock_client = MagicMock()
        mock_client.music_vibe.return_value = {
            "type": "music_vibe",
            "id": "x",
            "status": "generating",
        }
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        vibe(mood="happy", tags="[warm]")

        mock_client.music_vibe.assert_called_once_with(
            vibe="happy",
            vibe_tags="[warm]",
            owner_id=srv._state.session_id,
        )

    def test_vibe_no_propagation_when_music_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import punt_vox.server as srv

        srv._state.music_mode = "off"

        mock_client = MagicMock()
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        vibe(mood="happy")

        mock_client.music_vibe.assert_not_called()

    def test_vibe_connection_error_resets_music(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import punt_vox.server as srv
        from punt_vox.client import VoxdConnectionError

        srv._state.music_mode = "on"

        mock_client = MagicMock()
        mock_client.music_vibe.side_effect = VoxdConnectionError("gone")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(vibe(mood="sad"))

        # Vibe update itself should succeed.
        assert result["vibe"]["vibe"] == "sad"
        # But music_mode should be reset.
        assert srv._state.music_mode == "off"
