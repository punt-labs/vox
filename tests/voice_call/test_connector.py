"""Account-backed agent construction is deferred and cached per connector."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from punt_vox.voxd.voice_call.connector import CallConnector


def test_injected_connector_needs_no_environment_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    agent = Mock()
    connector = CallConnector(agent)
    assert connector.agent() is agent
    assert connector.agent() is agent


def test_missing_key_is_only_an_error_when_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    connector = CallConnector()
    with pytest.raises(ValueError, match="no ELEVENLABS_API_KEY"):
        connector.agent()


def test_configured_agent_is_constructed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    with (
        patch("punt_vox.voxd.voice_call.connector.ConvAIControl") as control,
        patch("punt_vox.voxd.voice_call.connector.VoiceAgent") as agent,
    ):
        connector = CallConnector()
        control.assert_not_called()
        assert connector.agent() is agent.return_value
        assert connector.agent() is agent.return_value
        control.assert_called_once_with("test-key")
        agent.assert_called_once_with(control.return_value)
