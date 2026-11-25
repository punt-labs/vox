"""The output-device switch: unset is PortAudio, silent is silent, else an error."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.voxd.pcm_output import sounddevice_output_stream
from punt_vox.voxd.pcm_silent import SilentOutputStream
from punt_vox.voxd.voice_agent.call_overrides import CallOverrides

_LOGGER = "punt_vox.voxd.voice_agent.call_overrides"


def test_pcm_output_silent_selects_the_device_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("VOX_PCM_OUTPUT", "silent")
    with caplog.at_level("WARNING", logger=_LOGGER):
        factory = CallOverrides.pcm_output(sounddevice_output_stream)
    assert factory is SilentOutputStream
    assert "VOX_PCM_OUTPUT=silent" in caplog.text


def test_pcm_output_unset_keeps_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOX_PCM_OUTPUT", raising=False)
    assert CallOverrides.pcm_output(sounddevice_output_stream) is (
        sounddevice_output_stream
    )


def test_pcm_output_empty_keeps_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOX_PCM_OUTPUT", "")
    assert CallOverrides.pcm_output(sounddevice_output_stream) is (
        sounddevice_output_stream
    )


@pytest.mark.parametrize("value", ["Silent", "1", "none", "slient", " silent"])
def test_pcm_output_any_other_value_fails_by_name_and_value(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A misspelt switch never falls back to PortAudio without saying so."""
    monkeypatch.setenv("VOX_PCM_OUTPUT", value)
    with pytest.raises(ValueError, match="VOX_PCM_OUTPUT") as failure:
        CallOverrides.pcm_output(sounddevice_output_stream)
    assert repr(value) in str(failure.value)


def test_an_explicit_device_over_the_switch_is_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Both set: the caller's device wins, the log says so, and no warning
    claims that agent audio is not played, because it is."""
    monkeypatch.setenv("VOX_PCM_OUTPUT", "silent")
    with caplog.at_level("DEBUG", logger=_LOGGER):
        assert CallOverrides.check(sounddevice_output_stream) is (
            sounddevice_output_stream
        )
    kept = [r for r in caplog.records if "keeping it" in r.getMessage()]
    assert len(kept) == 1
    assert "VOX_PCM_OUTPUT" in kept[0].getMessage()
    assert "agent audio is not played" not in caplog.text


def test_registration_path_is_the_default_without_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VOX_CONVAI_BASE_URL", raising=False)
    default = Path("/cfg/voice-agent.json")
    assert CallOverrides.registration_path(default) == default


def test_registration_path_is_a_sibling_named_for_the_override_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOX_CONVAI_BASE_URL", "http://127.0.0.1:4321")
    default = Path("/cfg/voice-agent.json")
    assert CallOverrides.registration_path(default) == Path(
        "/cfg/voice-agent.loopback-4321.json"
    )


def test_registration_path_rejects_a_malformed_override_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOX_CONVAI_BASE_URL", "http://example.com:1")
    with pytest.raises(ValueError, match="VOX_CONVAI_BASE_URL"):
        CallOverrides.registration_path(Path("/cfg/voice-agent.json"))
