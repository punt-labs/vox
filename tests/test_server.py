"""Tests for server-level helpers and mic API tools."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import typing
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from _program_fakes import FakeProgramGateway

from punt_vox.client import SynthesizeResult
from punt_vox.client_errors import VoxdConnectionError
from punt_vox.config import ConfigStore
from punt_vox.program_control import ProgramSummary
from punt_vox.resolve import (
    apply_vibe,
    split_leading_expressive_tags,
    strip_expressive_tags,
)
from punt_vox.server import (
    SessionConfig,
    music,
    music_list,
    music_next,
    music_play,
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
from punt_vox.voxd.programs import (
    Part,
    Program,
    ProgramName,
    ProgramState,
    ProgramStatus,
    Reason,
)
from punt_vox.voxd.programs.playback_policy import Advance, AdvanceResult


class _StatusPolicy:
    """Anti-repeat policy stand-in for building a playing Program in tests."""

    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> AdvanceResult:
        for part in pool:
            if part != playing:
                return Advance(part)
        return Advance(pool[0])


@pytest.fixture()
def _patch_config(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Return a writable config directory and patch config module default.

    Returns tmp_path as the config directory. Callers write
    ``vox.md`` or ``vox.local.md`` inside tmp_path.
    """
    import punt_vox.config as cfg
    import punt_vox.dirs as dirs

    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(dirs, "DEFAULT_CONFIG_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_session(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Reset server session config before every test.

    Also stubs _find_config_dir to return None so refresh_from_config
    is a no-op by default.  Tests that need refresh behavior override via
    the _refresh_config fixture.
    """
    import punt_vox.server as srv

    monkeypatch.setattr(srv, "_session", SessionConfig())
    monkeypatch.setattr(srv, "_find_config_dir", lambda: None)


# ---------------------------------------------------------------------------
# apply_vibe tests
# ---------------------------------------------------------------------------


class TestApplyVibe:
    """Tests for apply_vibe text injection."""

    def test_prepends_tags(self, _patch_config: Path) -> None:
        local_md = _patch_config / "vox.local.md"
        local_md.write_text('---\nvibe_tags: "[excited]"\n---\n')
        result = apply_vibe("Hello world", expressive_tags=True)
        assert result == "[excited] Hello world"

    def test_multiple_tags(self, _patch_config: Path) -> None:
        (_patch_config / "vox.local.md").write_text(
            '---\nvibe_tags: "[frustrated] [sighs]"\n---\n'
        )
        result = apply_vibe("Hello world", expressive_tags=True)
        assert result == "[frustrated] [sighs] Hello world"

    def test_skips_prepend_when_text_starts_with_tag(self, _patch_config: Path) -> None:
        (_patch_config / "vox.local.md").write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe("[calm] Already tagged", expressive_tags=True)
        assert result == "[calm] Already tagged"

    def test_skips_prepend_when_text_starts_with_different_tag(
        self, _patch_config: Path
    ) -> None:
        (_patch_config / "vox.local.md").write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe("[excited] Different tag", expressive_tags=True)
        assert result == "[excited] Different tag"

    def test_skips_prepend_when_tag_contains_punctuation(
        self, _patch_config: Path
    ) -> None:
        (_patch_config / "vox.local.md").write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe("[dramatic tone] Something important", expressive_tags=True)
        assert result == "[dramatic tone] Something important"

    def test_passthrough_when_no_tags(self, tmp_path: Path, monkeypatch: Any) -> None:
        import punt_vox.config as cfg

        missing = tmp_path / "missing_dir"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_DIR", missing)
        assert apply_vibe("Hello world", expressive_tags=True) == "Hello world"

    def test_skips_tags_when_not_supported(self, _patch_config: Path) -> None:
        local_md = _patch_config / "vox.local.md"
        local_md.write_text('---\nvibe_tags: "[excited]"\n---\n')
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
        (_patch_config / "vox.local.md").write_text('---\nvibe_tags: "[calm]"\n---\n')
        result = apply_vibe(
            "Hello world",
            expressive_tags=True,
            override_tags="[excited]",
        )
        assert result == "[excited] Hello world"

    def test_override_tags_empty_falls_through_to_config(
        self, _patch_config: Path
    ) -> None:
        (_patch_config / "vox.local.md").write_text('---\nvibe_tags: "[calm]"\n---\n')
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
        ConfigStore().write_field("vibe_tags", "[excited]")
        local_md = _patch_config / "vox.local.md"
        assert local_md.exists()
        text = local_md.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert text.startswith("---\n")
        assert text.rstrip().endswith("---")

    def test_updates_existing_field(self, _patch_config: Path) -> None:
        local_md = _patch_config / "vox.local.md"
        local_md.write_text('---\nvibe_tags: "[tired]"\n---\n')
        ConfigStore().write_field("vibe_tags", "[excited]")
        text = local_md.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert "[tired]" not in text

    def test_updates_unquoted_field(self, _patch_config: Path) -> None:
        local_md = _patch_config / "vox.local.md"
        local_md.write_text("---\nvibe_tags: [whispers]\n---\n")
        ConfigStore().write_field("vibe_tags", "[excited]")
        text = local_md.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert "[whispers]" not in text

    def test_inserts_new_field_before_closing_fence(self, _patch_config: Path) -> None:
        local_md = _patch_config / "vox.local.md"
        local_md.write_text('---\nvibe: "happy"\n---\n')
        ConfigStore().write_field("vibe_tags", "[excited]")
        text = local_md.read_text()
        assert 'vibe_tags: "[excited]"' in text
        assert 'vibe: "happy"' in text

    def test_preserves_other_fields(self, _patch_config: Path) -> None:
        local_md = _patch_config / "vox.local.md"
        local_md.write_text(
            '---\nvibe: "happy"\nvibe_tags: "[tired]"\nvibe_signals: "x"\n---\n'
        )
        ConfigStore().write_field("vibe_tags", "[excited]")
        text = local_md.read_text()
        assert 'vibe: "happy"' in text
        assert 'vibe_signals: "x"' in text
        assert 'vibe_tags: "[excited]"' in text

    def test_clears_field_with_empty_string(self, _patch_config: Path) -> None:
        local_md = _patch_config / "vox.local.md"
        local_md.write_text('---\nvibe_tags: "[tired]"\n---\n')
        ConfigStore().write_field("vibe_tags", "")
        text = local_md.read_text()
        assert 'vibe_tags: ""' in text

    def test_rejects_unknown_key(self, _patch_config: Path) -> None:
        vox_md = _patch_config / "vox.md"
        vox_md.write_text("---\n---\n")
        with pytest.raises(ValueError, match="Unknown config key"):
            ConfigStore().write_field("bad_key", "value")

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "dir"
        ConfigStore(nested).write_field("notify", "y")
        vox_md = nested / "vox.md"
        assert vox_md.exists()
        assert 'notify: "y"' in vox_md.read_text()

    def test_malformed_file_warns_and_overwrites(
        self, _patch_config: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        vox_md = _patch_config / "vox.md"
        vox_md.write_text("no frontmatter at all\n")
        import logging

        with caplog.at_level(logging.WARNING, logger="punt_vox.config"):
            ConfigStore(_patch_config).write_field("notify", "y")
        assert 'notify: "y"' in vox_md.read_text()
        assert "Malformed config" in caplog.text


class TestWriteConfigFields:
    """Tests for write_fields batch helper."""

    def test_writes_multiple_fields(self, _patch_config: Path) -> None:
        vox_md = _patch_config / "vox.md"
        vox_md.write_text('---\nnotify: "y"\n---\n')
        updates = {
            "vibe": "happy",
            "vibe_tags": "[cheerful]",
            "vibe_mode": "manual",
        }
        ConfigStore().write_fields(updates)
        # notify is the only durable pref here -> vox.md
        vox_text = vox_md.read_text()
        assert 'notify: "y"' in vox_text
        assert "vibe_mode" not in vox_text
        # the whole vibe cluster is ephemeral -> vox.local.md
        local_text = (_patch_config / "vox.local.md").read_text()
        assert 'vibe: "happy"' in local_text
        assert 'vibe_tags: "[cheerful]"' in local_text
        assert 'vibe_mode: "manual"' in local_text

    def test_updates_existing_fields(self, _patch_config: Path) -> None:
        local_md = _patch_config / "vox.local.md"
        local_md.write_text(
            '---\nvibe_mode: "off"\nvibe: "old"\nvibe_tags: "[old]"\n---\n'
        )
        updates = {
            "vibe": "new",
            "vibe_tags": "[new]",
            "vibe_mode": "manual",
        }
        ConfigStore().write_fields(updates)
        local_text = local_md.read_text()
        assert 'vibe_mode: "manual"' in local_text
        assert "off" not in local_text
        assert 'vibe: "new"' in local_text
        assert 'vibe_tags: "[new]"' in local_text
        assert "old" not in local_text

    def test_creates_file_when_missing(self, _patch_config: Path) -> None:
        ConfigStore().write_fields({"vibe": "happy", "vibe_tags": "[cheerful]"})
        local_md = _patch_config / "vox.local.md"
        text = local_md.read_text()
        assert text.startswith("---\n")
        assert 'vibe: "happy"' in text
        assert 'vibe_tags: "[cheerful]"' in text
        assert text.rstrip().endswith("---")

    def test_rejects_invalid_key(self, _patch_config: Path) -> None:
        vox_md = _patch_config / "vox.md"
        vox_md.write_text("---\n---\n")
        with pytest.raises(ValueError, match="Unknown config key"):
            ConfigStore().write_fields({"vibe": "ok", "bad_key": "fail"})
        # vox.md should be unchanged — validation fails before any write
        assert vox_md.read_text() == "---\n---\n"

    def test_atomic_single_read_write(
        self, _patch_config: Path, monkeypatch: Any
    ) -> None:
        """Verify batch performs one read and one write per file."""
        local_md = _patch_config / "vox.local.md"
        local_md.write_text("---\n---\n")
        vox_md = _patch_config / "vox.md"
        vox_md.write_text("---\n---\n")
        read_count = 0
        write_count = 0
        orig_read = Path.read_text
        orig_write = Path.write_text

        def counting_read(self: Path, *args: Any, **kwargs: Any) -> str:
            nonlocal read_count
            if self == local_md:
                read_count += 1
            return orig_read(self, *args, **kwargs)

        def counting_write(self: Path, *args: Any, **kwargs: Any) -> int:
            nonlocal write_count
            if self == local_md:
                write_count += 1
            return orig_write(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", counting_read)
        monkeypatch.setattr(Path, "write_text", counting_write)
        # All ephemeral keys -> single file write to vox.local.md
        ConfigStore(_patch_config).write_fields({"vibe": "a", "vibe_tags": "[b]"})
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

    def test_cached_flag_surfaces_in_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The client's cache-hit signal reaches the unmute MCP result."""
        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(
            request_id="rc", cached=True
        )
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(unmute(text="Hello world"))

        assert isinstance(result, list)
        assert result[0]["cached"] is True

    def test_cache_miss_flag_surfaces_in_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cache miss reports cached=False in the unmute MCP result."""
        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(
            request_id="rm", cached=False
        )
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(unmute(text="Hello world"))

        assert result[0]["cached"] is False

    def test_no_input_returns_error(self) -> None:
        result = json.loads(unmute())
        assert "error" in result

    def test_vibe_tags_updates_session_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import punt_vox.server as srv

        srv._session._vibe_signals = "tests-pass@14:00"

        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(request_id="req789")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        unmute(text="Done.", vibe_tags="[warm] [satisfied]")

        assert srv._session._vibe_tags == "[warm] [satisfied]"
        assert srv._session._vibe_signals == ""

    def test_voxd_connection_error_returns_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from punt_vox.client_errors import VoxdConnectionError

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
        assert srv._session.provider == "openai"

    def test_model_persists_to_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(request_id="req_mod")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        unmute(text="Hello", model="eleven_v3")
        assert srv._session.model == "eleven_v3"

    def test_config_only_update_no_text(self) -> None:
        """Provider/model/vibe_tags update without text returns config update."""
        import punt_vox.server as srv

        result = json.loads(unmute(provider="polly"))
        assert result["status"] == "config updated"
        assert result["provider"] == "polly"
        assert srv._session.provider == "polly"

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

        spec = mock_client.synthesize.call_args.args[1]
        assert spec.language == "de"

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

        spec = mock_client.synthesize.call_args.args[1]
        assert spec.language == "fr"


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
        from punt_vox.client_errors import VoxdConnectionError

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

    @pytest.fixture(autouse=True)
    def _isolated(self, _patch_config: Path) -> Path:
        """Pin the vibe tool's config writes to an isolated tmp dir.

        The autouse ``hermetic_config`` fixture already redirects the default
        path, but ``vibe`` writes through ``_find_config_dir()`` -> the config
        default; requesting ``_patch_config`` states that dependency explicitly
        rather than leaning on ambient redirect state.
        """
        return _patch_config

    def test_set_mood(self) -> None:
        import punt_vox.server as srv

        result = json.loads(vibe(mood="excited"))
        assert result["vibe"]["vibe"] == "excited"
        assert srv._session._vibe == "excited"

    def test_set_tags(self) -> None:
        import punt_vox.server as srv

        result = json.loads(vibe(tags="[warm] [calm]"))
        assert result["vibe"]["vibe_tags"] == "[warm] [calm]"
        assert srv._session._vibe_tags == "[warm] [calm]"
        assert srv._session._vibe_signals == ""

    def test_set_mode(self) -> None:
        import punt_vox.server as srv

        result = json.loads(vibe(mode="manual"))
        assert result["vibe"]["vibe_mode"] == "manual"
        assert srv._session._vibe_mode == "manual"

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

    def test_auto_clears_stale_mood(self) -> None:
        """/vibe auto is authoritative: it wipes a lingering mood and tags."""
        import punt_vox.server as srv

        vibe(mood="sad", tags="[melancholy]", mode="manual")
        updates = json.loads(vibe(tags="", mode="auto"))["vibe"]
        assert updates["vibe"] == ""
        assert updates["vibe_tags"] == ""
        assert updates["vibe_mode"] == "auto"
        assert srv._session._vibe is None
        assert srv._session._vibe_tags is None
        assert srv._session._vibe_signals == ""
        assert srv._session._vibe_mode == "auto"

    def test_off_clears_stale_mood(self) -> None:
        """/vibe off also resets mood and tags, not just the mode."""
        import punt_vox.server as srv

        vibe(mood="sad", tags="[melancholy]", mode="manual")
        updates = json.loads(vibe(tags="", mode="off"))["vibe"]
        assert updates["vibe"] == ""
        assert updates["vibe_mode"] == "off"
        assert srv._session._vibe is None
        assert srv._session._vibe_mode == "off"


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

        srv._session.provider = "elevenlabs"

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

        srv._session.provider = "elevenlabs"

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

        srv._session.provider = "elevenlabs"

        mock_client = MagicMock()
        mock_client.voices.return_value = self._VOICE_LIST
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(who())
        assert len(result["featured"]) <= 6

    def test_current_voice_included(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        srv._session.voice = "aria"
        srv._session.provider = "elevenlabs"

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

        srv._session.provider = "say"

        mock_client = MagicMock()
        mock_client.voices.return_value = ["samantha", "alex"]
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        result = json.loads(who())
        assert result["provider"] == "say"
        assert result["featured"] == []
        assert result["all"] == ["samantha", "alex"]

    def test_voxd_connection_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from punt_vox.client_errors import VoxdConnectionError

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
        assert srv._session._notify == "y"

    def test_set_mode_n(self) -> None:
        import punt_vox.server as srv

        result = json.loads(notify(mode="n"))
        assert result["notify"]["notify"] == "n"
        assert srv._session._notify == "n"

    def test_speak_unset_c_inits_voice(self) -> None:
        """speak not yet set — mode=c should initialize it to y."""
        import punt_vox.server as srv

        result = json.loads(notify(mode="c"))
        assert result["notify"]["notify"] == "c"
        assert result["notify"]["speak"] == "y"
        assert srv._session._speak == "y"

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
        srv._session._speak = "n"
        srv._session._speak_explicit = True

        result = json.loads(notify(mode="c"))
        assert result["notify"]["notify"] == "c"
        assert "speak" not in result["notify"]
        assert srv._session._speak == "n"

    def test_speak_set_preserved_by_y(self) -> None:
        """User explicitly muted — mode=y should not re-enable voice."""
        import punt_vox.server as srv

        srv._session._speak = "n"
        srv._session._speak_explicit = True

        result = json.loads(notify(mode="y"))
        assert "speak" not in result["notify"]
        assert srv._session._speak == "n"

    def test_set_voice(self) -> None:
        import punt_vox.server as srv

        result = json.loads(notify(mode="c", voice="matilda"))
        assert result["notify"]["voice"] == "matilda"
        assert srv._session.voice == "matilda"

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
        assert srv._session._speak == "y"

    def test_set_speak_n(self) -> None:
        import punt_vox.server as srv

        result = json.loads(speak(mode="n"))
        assert result["speak"] == "n"
        assert srv._session._speak == "n"

    def test_set_voice(self) -> None:
        import punt_vox.server as srv

        result = json.loads(speak(mode="y", voice="matilda"))
        assert result["speak"] == "y"
        assert result["voice"] == "matilda"
        assert srv._session.voice == "matilda"

    def test_invalid_mode(self) -> None:
        result = json.loads(speak(mode="x"))
        assert "error" in result

    def test_marks_speak_explicit(self) -> None:
        import punt_vox.server as srv

        assert not srv._session._speak_explicit
        speak(mode="n")
        assert srv._session._speak_explicit


# ---------------------------------------------------------------------------
# status tool tests
# ---------------------------------------------------------------------------


class TestStatusTool:
    """Tests for the status MCP tool."""

    def test_returns_state_fields(self) -> None:
        import punt_vox.server as srv

        srv._session._notify = "c"
        srv._session._speak = "y"
        srv._session.voice = "sarah"
        srv._session.provider = "elevenlabs"
        srv._session._vibe_mode = "auto"
        srv._session._vibe_tags = "[excited]"
        srv._session._vibe_signals = "tests-pass@12:00"

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

    def test_music_mode_derived_on_from_program_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``music_mode`` is 'on' when the authoritative Program is playing."""
        program = Program(ProgramState.initial(), _StatusPolicy())
        program.turn_on()
        program.first_track_ok(Part("id001", 1))
        _install_fake(
            monkeypatch,
            FakeProgramGateway(status=ProgramStatus.of(program, ProgramName("amb"))),
        )
        assert json.loads(status())["music_mode"] == "on"

    def test_music_mode_derived_off_when_program_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``music_mode`` is 'off' when the daemon holds no active Program."""
        _install_fake(monkeypatch, FakeProgramGateway(status=ProgramStatus.idle()))
        assert json.loads(status())["music_mode"] == "off"

    def test_music_mode_reflects_external_change_no_shadow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Program mode change made elsewhere flips ``music_mode`` with no shadow.

        No music tool runs on this server, yet ``music_mode`` follows the daemon's
        authoritative ``program.mode`` -- no drift, because the label is derived
        instead of caching a session copy.
        """
        fake = FakeProgramGateway(status=ProgramStatus.idle())
        _install_fake(monkeypatch, fake)
        assert json.loads(status())["music_mode"] == "off"

        program = Program(ProgramState.initial(), _StatusPolicy())
        program.turn_on()
        program.first_track_ok(Part("id001", 1))
        fake.set_status(ProgramStatus.of(program, ProgramName("amb")))
        assert json.loads(status())["music_mode"] == "on"


# ---------------------------------------------------------------------------
# SessionConfig tests
# ---------------------------------------------------------------------------


class TestSessionConfig:
    """Tests for SessionConfig defaults and session_id generation."""

    def test_session_id_is_uuid_hex(self) -> None:
        state = SessionConfig()
        assert len(state.session_id) == 32
        int(state.session_id, 16)  # valid hex

    def test_each_instance_gets_unique_id(self) -> None:
        a = SessionConfig()
        b = SessionConfig()
        assert a.session_id != b.session_id


# ---------------------------------------------------------------------------
# music / program tool tests (routed through the ProgramGateway seam)
# ---------------------------------------------------------------------------


def _install_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeProgramGateway) -> None:
    """Point the server's module-level gateway at an in-memory fake."""
    import punt_vox.server as srv

    monkeypatch.setattr(srv, "_program_tools", fake)


class TestMusicTool:
    """The music tool routes on/off through the gateway with an F7 result."""

    def test_on_starts_via_gateway_and_reports_applied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import punt_vox.server as srv

        srv._session._vibe = "focused"
        fake = FakeProgramGateway()
        _install_fake(monkeypatch, fake)

        result = json.loads(music(mode="on", style="techno"))

        assert result["applied"] is True
        assert "techno" in result["message"] and "focused" in result["message"]
        assert fake.verbs() == ["start"]
        request = fake.calls[0].request
        assert request is not None and request.style == "techno"

    def test_on_forwards_agent_prompts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeProgramGateway()
        _install_fake(monkeypatch, fake)
        variations = [f"var{i}" for i in range(12)]

        music(mode="on", base_prompt="deep techno", variations=variations)

        request = fake.calls[0].request
        assert request is not None
        assert request.prompts is not None
        assert request.prompts.base == "deep techno"
        assert request.prompts.variations == tuple(variations)

    def test_on_invalid_prompt_shape_reports_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake(monkeypatch, FakeProgramGateway())

        result = json.loads(music(mode="on", base_prompt="x", variations=["only one"]))

        assert "error" in result

    def test_off_stops_via_gateway(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeProgramGateway()
        _install_fake(monkeypatch, fake)

        result = json.loads(music(mode="off"))

        assert result["applied"] is True
        assert fake.verbs() == ["stop"]

    def test_invalid_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake(monkeypatch, FakeProgramGateway())
        result = json.loads(music(mode="sideways"))
        assert "error" in result

    def test_rejected_start_surfaces_not_applied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lost-race start reaches the caller as applied=false."""
        _install_fake(monkeypatch, FakeProgramGateway(applied=False))

        result = json.loads(music(mode="on", style="techno"))

        assert result["applied"] is False

    def test_rejected_on_surfaces_reason_not_success_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rejected 'on' shows the daemon's reason, not the generating line."""
        _install_fake(
            monkeypatch,
            FakeProgramGateway(applied=False, reason="already generating"),
        )

        result = json.loads(music(mode="on", style="techno"))

        assert result["applied"] is False
        assert "already generating" in result["message"]
        assert "generating a techno track" not in result["message"]

    def test_daemon_error_reports(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        fake = MagicMock()
        fake.start.side_effect = VoxdConnectionError("not running")
        monkeypatch.setattr(srv, "_program_tools", fake)

        result = json.loads(music(mode="on"))

        assert "error" in result


class TestMusicPlayTool:
    """music_play routes a Selection replay through the gateway."""

    def test_play_forwards_the_tag_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeProgramGateway()
        _install_fake(monkeypatch, fake)

        result = json.loads(music_play(style="trance", vibe="calm"))

        assert result["applied"] is True
        assert fake.calls[0].verb == "select"
        assert fake.calls[0].selection is not None
        assert fake.calls[0].selection.style == "trance"

    def test_play_forwards_the_album_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeProgramGateway()
        _install_fake(monkeypatch, fake)

        json.loads(music_play(album_id="a3f1c9"))

        assert fake.calls[0].selection is not None
        assert fake.calls[0].selection.id == "a3f1c9"

    def test_play_daemon_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        fake = MagicMock()
        fake.select.side_effect = VoxdConnectionError("not running")
        monkeypatch.setattr(srv, "_program_tools", fake)

        result = json.loads(music_play(style="trance"))

        assert "error" in result


class TestMusicListTool:
    """music_list groups saved Programs, not loose files."""

    def test_list_groups_albums(self, monkeypatch: pytest.MonkeyPatch) -> None:
        catalog = (
            ProgramSummary(
                id="a3f1c9", style="trance", vibe="calm", format="music", ready=5
            ),
            ProgramSummary(
                id="7b2e04", style="lofi", vibe="focus", format="music", ready=1
            ),
        )
        _install_fake(monkeypatch, FakeProgramGateway(catalog=catalog))

        result = json.loads(music_list())

        assert [p["id"] for p in result["programs"]] == ["a3f1c9", "7b2e04"]
        assert "a3f1c9" in result["message"]

    def test_list_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake(monkeypatch, FakeProgramGateway())
        result = json.loads(music_list())
        assert result["programs"] == []
        assert "No saved albums" in result["message"]

    def test_list_daemon_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        fake = MagicMock()
        fake.catalog.side_effect = VoxdConnectionError("not running")
        monkeypatch.setattr(srv, "_program_tools", fake)

        result = json.loads(music_list())

        assert "error" in result


class TestMusicNextTool:
    """music_next is the one ungated advance."""

    def test_next_advances(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeProgramGateway()
        _install_fake(monkeypatch, fake)

        result = json.loads(music_next())

        assert result["applied"] is True
        assert fake.verbs() == ["advance"]

    def test_next_rejected_surfaces_not_applied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake(monkeypatch, FakeProgramGateway(applied=False))
        result = json.loads(music_next())
        assert result["applied"] is False

    def test_next_rejected_surfaces_reason_not_skip_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A next-after-off shows the reason, not a misleading skip line (F4/F7)."""
        _install_fake(
            monkeypatch,
            FakeProgramGateway(applied=False, reason="nothing is playing"),
        )

        result = json.loads(music_next())

        assert result["applied"] is False
        assert "nothing is playing" in result["message"]
        assert "Skipping" not in result["message"]

    def test_next_daemon_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import punt_vox.server as srv

        fake = MagicMock()
        fake.advance.side_effect = OSError("boom")
        monkeypatch.setattr(srv, "_program_tools", fake)

        result = json.loads(music_next())

        assert "error" in result


class TestStatusProgramSurface:
    """The status tool serves the daemon's authoritative Program status."""

    def test_status_includes_both_failure_surfaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both the program-level error and per-Part failures reach the client."""
        program = Program(ProgramState.initial(), _StatusPolicy())
        program.turn_on()
        program.first_track_ok(Part("id001", 1))
        program.fill_bad_part(Part("id002", 2), Reason("bad_prompt: unsafe"))
        status_value = ProgramStatus.of(program, ProgramName("ambient_techno"))
        _install_fake(monkeypatch, FakeProgramGateway(status=status_value))

        block = json.loads(status())["program"]

        assert block["mode"] == "playing_filling"
        assert block["now_playing"] is not None
        assert block["failed_parts"][0]["index"] == 2

    def test_status_reads_fresh_no_server_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A state change made via another path is reflected next call."""
        fake = FakeProgramGateway(status=ProgramStatus.idle())
        _install_fake(monkeypatch, fake)

        first = json.loads(status())["program"]
        assert first.get("name") is None

        # A different client turns music on; the server must not serve a shadow.
        program = Program(ProgramState.initial(), _StatusPolicy())
        program.turn_on()
        program.first_track_ok(Part("id001", 1))
        fake.set_status(ProgramStatus.of(program, ProgramName("ambient_techno")))

        second = json.loads(status())["program"]
        assert second["name"] == "ambient_techno"

    def test_status_degrades_when_daemon_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import punt_vox.server as srv

        fake = MagicMock()
        fake.status.side_effect = VoxdConnectionError("not running")
        monkeypatch.setattr(srv, "_program_tools", fake)

        block = json.loads(status())["program"]

        assert "error" in block


# ---------------------------------------------------------------------------
# SessionConfig.refresh_from_config tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def _refresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:  # pyright: ignore[reportUnusedFunction]
    """Patch _find_config_dir and DEFAULT_CONFIG_DIR for refresh tests."""
    import punt_vox.config as cfg
    import punt_vox.dirs as dirs
    import punt_vox.server as srv

    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(dirs, "DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(srv, "_find_config_dir", lambda: tmp_path)
    return tmp_path


class TestRefreshFromConfig:
    """Tests for SessionConfig.refresh_from_config reading external changes."""

    def test_ephemeral_fields_always_updated(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Vibe/tags/signals written externally are picked up on refresh."""
        import punt_vox.server as srv

        srv._session._vibe = "old-mood"
        srv._session._vibe_tags = "[old]"
        srv._session._vibe_signals = "old-signal"

        (_refresh_config / "vox.local.md").write_text(
            "---\n"
            'vibe: "happy"\n'
            'vibe_tags: "[warm]"\n'
            'vibe_signals: "tests-pass@10:00"\n'
            "---\n"
        )

        srv._session.refresh_from_config()

        assert srv._session._vibe == "happy"
        assert srv._session._vibe_tags == "[warm]"
        assert srv._session._vibe_signals == "tests-pass@10:00"

    def test_ephemeral_cleared_when_config_empty(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When config has no vibe fields, in-memory vibe is cleared."""
        import punt_vox.server as srv

        srv._session._vibe = "stale-mood"
        srv._session._vibe_tags = "[stale]"
        srv._session._vibe_signals = "stale-signal"

        # Config exists but has no vibe fields
        (_refresh_config / "vox.local.md").write_text("---\n---\n")
        (_refresh_config / "vox.md").write_text("---\n---\n")

        srv._session.refresh_from_config()

        assert srv._session._vibe is None
        assert srv._session._vibe_tags is None  # type: ignore[unreachable]
        assert srv._session._vibe_signals == ""

    def test_durable_fields_updated_from_config(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """notify, speak take the durable value; vibe_mode the ephemeral one."""
        import punt_vox.server as srv

        srv._session._notify = "n"
        srv._session._speak = "n"
        srv._session._vibe_mode = "off"

        (_refresh_config / "vox.md").write_text('---\nnotify: "c"\nspeak: "y"\n---\n')
        (_refresh_config / "vox.local.md").write_text('---\nvibe_mode: "auto"\n---\n')

        srv._session.refresh_from_config()

        assert srv._session._notify == "c"
        assert srv._session._speak == "y"
        assert srv._session._vibe_mode == "auto"

    def test_voice_only_overwritten_when_config_has_value(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In-memory voice survives refresh when config has no voice."""
        import punt_vox.server as srv

        srv._session.voice = "matilda"

        # Config has no voice field
        (_refresh_config / "vox.md").write_text("---\n---\n")

        srv._session.refresh_from_config()

        assert srv._session.voice == "matilda"

    def test_voice_overwritten_when_config_has_value(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Config voice overwrites in-memory voice."""
        import punt_vox.server as srv

        srv._session.voice = "matilda"

        (_refresh_config / "vox.md").write_text('---\nvoice: "roger"\n---\n')

        srv._session.refresh_from_config()

        assert srv._session.voice == "roger"

    def test_provider_survives_when_config_empty(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In-memory provider override survives when config has no provider."""
        import punt_vox.server as srv

        srv._session.provider = "openai"

        (_refresh_config / "vox.md").write_text("---\n---\n")

        srv._session.refresh_from_config()

        assert srv._session.provider == "openai"

    def test_model_survives_when_config_empty(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In-memory model override survives when config has no model."""
        import punt_vox.server as srv

        srv._session.model = "eleven_v3"

        (_refresh_config / "vox.md").write_text("---\n---\n")

        srv._session.refresh_from_config()

        assert srv._session.model == "eleven_v3"

    def test_no_config_dir_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When _find_config_dir returns None, refresh is a no-op."""
        import punt_vox.server as srv

        monkeypatch.setattr(srv, "_find_config_dir", lambda: None)
        srv._session._vibe = "should-survive"

        srv._session.refresh_from_config()

        assert srv._session._vibe == "should-survive"


class TestRefreshIntegrationWithTools:
    """Verify tool calls pick up external config writes via refresh."""

    def test_status_reflects_external_vibe_change(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI writes vibe to config; status tool reads the new value."""
        import punt_vox.server as srv

        srv._session._vibe = "sad"
        srv._session._vibe_tags = "[gloomy]"

        # Simulate CLI writing new vibe to config
        ConfigStore(_refresh_config).write_fields(
            {"vibe": "happy", "vibe_tags": "[cheerful]"}
        )

        result = json.loads(status())

        assert result["vibe"] == "happy"
        assert result["vibe_tags"] == "[cheerful]"

    def test_status_reflects_external_notify_change(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """External notify write is reflected in status."""
        import punt_vox.server as srv

        srv._session._notify = "n"

        ConfigStore(_refresh_config).write_field("notify", "c")

        result = json.loads(status())

        assert result["notify"] == "c"

    def test_music_display_uses_fresh_vibe(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """music reads fresh config for its display line, not stale in-memory.

        The session vibe personalises the generating *message* only -- it is
        never forwarded as a Program transition input.
        """
        import punt_vox.server as srv

        srv._session._vibe = "old-mood"
        srv._session._vibe_tags = "[old]"

        # External write clears vibe (e.g. `vox vibe auto`).
        ConfigStore(_refresh_config).write_fields({"vibe": "", "vibe_tags": ""})

        fake = FakeProgramGateway()
        monkeypatch.setattr(srv, "_program_tools", fake)

        message = json.loads(music(mode="on"))["message"]

        # The cleared vibe means the generic line, not "for your old-mood mood".
        assert "old-mood" not in message
        # And no vibe travelled into the Program start request.
        request = fake.calls[0].request
        assert request is not None and request.style is None

    def test_unmute_uses_refreshed_voice(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """unmute picks up voice written to config externally."""
        import punt_vox.server as srv

        srv._session.voice = "matilda"

        ConfigStore(_refresh_config).write_field("voice", "roger")

        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(request_id="r1")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        unmute(text="Hello")

        spec = mock_client.synthesize.call_args.args[1]
        assert spec.voice == "roger"

    def test_unmute_preserves_inmemory_provider_when_config_empty(
        self, _refresh_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Provider set via MCP tool survives refresh when config is empty."""
        import punt_vox.server as srv

        srv._session.provider = "openai"

        # Config has no provider field
        (_refresh_config / "vox.md").write_text("---\n---\n")

        mock_client = MagicMock()
        mock_client.synthesize.return_value = SynthesizeResult(request_id="r2")
        monkeypatch.setattr("punt_vox.server._voxd_client", lambda: mock_client)

        unmute(text="Hello")

        spec = mock_client.synthesize.call_args.args[1]
        assert spec.provider == "openai"
