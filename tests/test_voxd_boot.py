"""Daemon boot validates the test-only call switches before wiring anything."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from punt_vox.voxd.boot import DaemonBoot
from punt_vox.voxd.pcm_output import sounddevice_output_stream
from punt_vox.voxd.voice_agent.call_overrides import CallOverrides

if TYPE_CHECKING:
    from punt_vox.voxd.config import DaemonConfig


class _UntouchedConfig:
    """A config the boot must not reach once a switch is malformed."""

    def read_or_create_token(self) -> str:
        msg = "the boot wired subsystems with a malformed switch in place"
        raise AssertionError(msg)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VOX_CONVAI_BASE_URL", "http://example.com"),
        ("VOX_CALL_ALLOW_LOOPBACK", "yes"),
        ("VOX_PCM_OUTPUT", "Silent"),
    ],
)
def test_boot_stops_on_a_malformed_switch_naming_it(
    name: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for switch in ("VOX_CONVAI_BASE_URL", "VOX_CALL_ALLOW_LOOPBACK", "VOX_PCM_OUTPUT"):
        monkeypatch.delenv(switch, raising=False)
    monkeypatch.setenv(name, value)
    boot = DaemonBoot("127.0.0.1", 0)
    # The config is a stand-in: a correct boot fails before it is touched.
    with pytest.raises(ValueError, match=name):
        boot._compose(cast("DaemonConfig", _UntouchedConfig()))


def test_check_passes_with_every_switch_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for switch in ("VOX_CONVAI_BASE_URL", "VOX_CALL_ALLOW_LOOPBACK", "VOX_PCM_OUTPUT"):
        monkeypatch.delenv(switch, raising=False)
    assert CallOverrides.check(sounddevice_output_stream) is sounddevice_output_stream
