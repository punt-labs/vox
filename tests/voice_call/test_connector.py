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
    monkeypatch.delenv("VOX_CONVAI_BASE_URL", raising=False)
    with patch("punt_vox.voxd.voice_call.connector.VoiceAgent") as agent:
        connector = CallConnector()
        agent.assert_not_called()
        assert connector.agent() is agent.return_value
        assert connector.agent() is agent.return_value
    (control,) = agent.call_args.args
    assert control._http.headers["xi-api-key"] == "test-key"
    agent.assert_called_once_with(control)


def test_the_rest_override_needs_no_environment_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The override supplies the placeholder key, so the three switches suffice."""
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("VOX_CONVAI_BASE_URL", "http://127.0.0.1:4321")
    with patch("punt_vox.voxd.voice_call.connector.VoiceAgent") as agent:
        assert CallConnector().agent() is agent.return_value
    (control,) = agent.call_args.args
    assert str(control._http.base_url).rstrip("/") == "http://127.0.0.1:4321"
