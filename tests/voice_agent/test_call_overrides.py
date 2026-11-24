"""The output-device switch: unset is PortAudio, silent is silent, else an error."""

from __future__ import annotations

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
