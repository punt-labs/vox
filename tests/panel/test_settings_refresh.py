"""Test the snapshot and notice policy used for write-failure recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.client_sync import VoxClientSync
from punt_vox.config import ConfigStore
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.panel.panel_settings import PanelSettings
from punt_vox.panel.settings_refresh import RefreshNotices, SettingsRefresh

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("unavailable", [False, True], ids=["reread", "retain"])
def test_recovering_a_write_failure_rereads_without_persisting(
    tmp_path: Path, unavailable: bool
) -> None:
    """RejectStaleTarget updates the held snapshot only on a usable read."""
    store = ConfigStore(tmp_path)
    store.write_fields({"provider": "openai", "model": "tts-1", "voice": "alloy"})
    client = MagicMock(spec=VoxClientSync)
    client.voices.return_value = ["alloy"]
    settings = PanelSettings(client, store)
    refresh = SettingsRefresh(client, store)
    assert refresh.refresh(settings)
    before = settings.snapshot()

    store.write_fields({"provider": "espeak", "model": "", "voice": "af"})
    saved = store.read()
    if unavailable:
        client.voices.side_effect = VoxdConnectionError("roster unavailable")
    else:
        client.voices.return_value = ["af"]

    assert refresh.refresh(settings, RefreshNotices.write_failure("voice"))
    scene = settings.scene()
    if unavailable:
        assert settings.snapshot() is before
        assert scene.notice == PanelNotice.write_failed_and_voxd_unavailable("voice")
    else:
        assert (scene.provider, scene.voice, scene.roster) == ("espeak", "af", ("af",))
        assert scene.notice == PanelNotice.write_failed("voice")
    assert store.read() == saved
