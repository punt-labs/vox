"""Tests for :obj:`punt_vox.commands.voice` -- the pure command function.

Exercised directly through a real :class:`~punt_vox.config.ConfigStore` on
a tmp dir and a stub voxd client; no CLI runner, no MCP round-trip.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.commands import Ctx, voice
from punt_vox.config import ConfigStore
from punt_vox.sibling_lock import SiblingLock


def _ctx(tmp_path: Path, *, voices: list[str] | None = None) -> Ctx:
    """Build a Ctx with a real ConfigStore and a stub client returning *voices*.

    Seeds ``vox.md`` with ``provider: elevenlabs`` so listing has an
    authoritative provider to fetch a roster for -- an unset provider is
    now the F1 refusal (state is the sole authority on which provider
    voxd runs). Tests that mean to exercise the refusal overwrite the
    seeded file explicitly.
    """
    vox_md = tmp_path / "vox.md"
    if not vox_md.exists():
        vox_md.write_text('---\nprovider: "elevenlabs"\n---\n')
    client = MagicMock()
    client.voices.return_value = voices if voices is not None else []
    return Ctx(store=ConfigStore(tmp_path), client=client)


class TestList:
    """No name given -- reach voxd for the roster."""

    async def test_lists_roster(self, tmp_path: Path) -> None:
        result = await voice(_ctx(tmp_path, voices=["matilda", "roger"]), None)
        assert result.error is False
        assert "matilda" in result.text
        assert "roger" in result.text
        assert result.json_data is not None
        names = cast("list[str]", result.json_data["names"])
        assert names == ["matilda", "roger"]

    async def test_marks_current(self, tmp_path: Path) -> None:
        (tmp_path / "vox.md").write_text(
            '---\nprovider: "elevenlabs"\nvoice: "roger"\n---\n'
        )
        result = await voice(_ctx(tmp_path, voices=["matilda", "roger"]), None)
        assert "roger (current)" in result.text
        assert result.json_data is not None
        assert result.json_data["current"] == "roger"

    async def test_passes_provider_through(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, voices=["joanna"])
        result = await voice(ctx, None, provider="polly")
        assert result.error is False
        # The stub client received the caller-supplied provider unchanged.
        ctx.client.voices.assert_called_once_with("polly")  # type: ignore[attr-defined]
        assert result.json_data is not None
        assert result.json_data.get("provider") == "polly"

    async def test_daemon_connection_error_returns_error(self, tmp_path: Path) -> None:
        (tmp_path / "vox.md").write_text('---\nprovider: "elevenlabs"\n---\n')
        ctx = Ctx(store=ConfigStore(tmp_path), client=MagicMock())
        ctx.client.voices.side_effect = VoxdConnectionError("not running")  # type: ignore[attr-defined]
        result = await voice(ctx, None)
        assert result.error is True
        assert result.exit_code == 1
        assert "not running" in result.text
        assert result.json_data is not None
        assert result.json_data["error"] == "not running"

    async def test_daemon_protocol_error_returns_error(self, tmp_path: Path) -> None:
        (tmp_path / "vox.md").write_text('---\nprovider: "elevenlabs"\n---\n')
        ctx = Ctx(store=ConfigStore(tmp_path), client=MagicMock())
        ctx.client.voices.side_effect = VoxdProtocolError("bad wire")  # type: ignore[attr-defined]
        result = await voice(ctx, None)
        assert result.error is True
        assert result.exit_code == 1

    async def test_unconfigured_provider_lists_empty_no_refusal(
        self, tmp_path: Path
    ) -> None:
        """``vox voices`` with no provider lists an empty roster, does not refuse.

        Missing provider configuration must be reported rather than substituted with
        a different provider.
        """
        client = MagicMock()
        result = await voice(Ctx(store=ConfigStore(tmp_path), client=client), None)
        assert result.error is False
        assert result.exit_code == 0
        assert result.text == "No voices for this provider."
        assert result.json_data is not None
        assert cast("list[str]", result.json_data["names"]) == []
        assert result.json_data["provider"] is None
        # Empty branch must not call the daemon: VoxClientSync.voices
        # requires a provider now.
        client.voices.assert_not_called()


class TestSet:
    """A name given -- normalize and write to config."""

    async def test_rejects_inactive_provider_without_writing(
        self, tmp_path: Path
    ) -> None:
        ctx = _ctx(tmp_path)
        ctx.store.write_field("voice", "matilda")
        config_path = tmp_path / "vox.md"
        before = config_path.read_bytes()

        result = await voice(ctx, "alloy", provider="openai")

        assert result.exit_code == 1
        assert result.error is True
        assert "openai" in result.text
        assert "elevenlabs" in result.text
        assert result.json_data is not None
        assert result.text == f"Error: {result.json_data['error']}"
        assert config_path.read_bytes() == before

    async def test_rejects_provider_switched_before_write_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _ctx(tmp_path)
        config_path = tmp_path / "vox.md"
        switched = b'---\nprovider: "openai"\nmodel: "tts-1"\nvoice: "alloy"\n---\n'
        acquire = SiblingLock._acquire

        def after_provider_switch(lock: SiblingLock, descriptor: int) -> None:
            # A peer's provider cascade finished just before we got the lock.
            config_path.write_bytes(switched)
            acquire(lock, descriptor)

        monkeypatch.setattr(SiblingLock, "_acquire", after_provider_switch)

        result = await voice(ctx, "matilda", provider="elevenlabs")

        assert result.exit_code == 1
        assert "the active provider is 'openai'" in result.text
        assert config_path.read_bytes() == switched

    async def test_checked_write_preserves_uneditable_config(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "vox.md"
        original = b'---\nprovider: "elevenlabs"\n'
        config_path.write_bytes(original)

        result = await voice(_ctx(tmp_path), "matilda", provider="elevenlabs")

        assert result.exit_code == 1
        assert "no closing ---" in result.text
        assert config_path.read_bytes() == original

    @pytest.mark.parametrize("provider", ["elevenlabs", "ElevenLabs", ""])
    async def test_allows_active_or_empty_provider_option(
        self, tmp_path: Path, provider: str
    ) -> None:
        ctx = _ctx(tmp_path)
        result = await voice(ctx, "matilda", provider=provider)
        assert result.error is False
        assert ctx.store.read().voice == "matilda"

    async def test_writes_normalized_voice(self, tmp_path: Path) -> None:
        result = await voice(_ctx(tmp_path), "matilda")
        assert result.error is False
        assert result.json_data == {"voice": "matilda"}
        assert "matilda" in result.text
        assert ConfigStore(tmp_path).read().voice == "matilda"

    async def test_strips_leading_at_sigil(self, tmp_path: Path) -> None:
        # SynthesisSpec.normalize_voice strips a stray ``@`` so the CLI and MCP
        # both accept "@matilda" as "matilda". The stored value is normalized.
        result = await voice(_ctx(tmp_path), "@matilda")
        assert result.error is False
        assert ConfigStore(tmp_path).read().voice == "matilda"

    async def test_blank_name_returns_error(self, tmp_path: Path) -> None:
        result = await voice(_ctx(tmp_path), "@")
        assert result.error is True
        assert result.exit_code == 1
        assert "empty" in result.text

    @pytest.mark.parametrize("has_config", [True, False])
    async def test_unconfigured_provider_refuses_set(
        self, tmp_path: Path, has_config: bool
    ) -> None:
        """Setting a voice with no provider configured refuses, never writes.

        A voice name is provider-scoped, so writing ``matilda`` into
        ``vox.md`` while no provider is set would land a wrong-provider
        voice the moment a caller runs ``vox provider <name>``. Symmetric
        with ``vox model``'s set-refusal; unlike the list branch, setting
        needs an authoritative provider.
        """
        config_path = tmp_path / "vox.md"
        original = b"---\n---\n"
        if has_config:
            config_path.write_bytes(original)
        client = MagicMock()
        result = await voice(Ctx(store=ConfigStore(tmp_path), client=client), "matilda")
        assert result.error is True
        assert result.exit_code == 1
        assert "no TTS provider" in result.text
        if has_config:
            assert config_path.read_bytes() == original
        else:
            assert not config_path.exists()
        client.voices.assert_not_called()

    async def test_config_write_error_returns_envelope(self, tmp_path: Path) -> None:
        """ConfigStore rejects control chars -- the command envelopes it, not raises.

        Historical footgun: a voice string with a newline or unescaped quote
        raised ConfigValueError across the CommandResult boundary. The command
        must catch it and return an error result.
        """
        result = await voice(_ctx(tmp_path), 'matilda"; injected: "bad')
        assert result.error is True
        assert result.exit_code == 1
        assert "Error" in result.text or "quote" in result.text.lower()
